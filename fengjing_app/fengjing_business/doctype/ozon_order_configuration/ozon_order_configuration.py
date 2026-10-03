# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""Ozon order synchronization driven by the independent order configuration.

This module is intentionally not connected to hooks yet. It can be deployed and
reviewed without taking over or starting any existing Ozon synchronization.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, get_system_timezone, now_datetime

from fengjing_app.fengjing_business.doctype.ozon_store_configuration.ozon_store_configuration import (
	ensure_database_connection,
	get_store,
	ozon_seller_request,
	response_error_summary,
)


DOCTYPE = "Ozon Order Configuration"
STORAGE_DOCTYPE = "Ozon order storage"
MAPPING_DOCTYPE = "Fengjing - Product Corresponding Platform - Main Table"
TASK_TIMEOUT = 6 * 60 * 60
SAFE_DELAY_MINUTES = 5
HISTORY_WINDOW_DAYS = 30
RECHECK_DAYS = (7, 14, 30, 90, 180)

SYNC_TYPE_LABELS = {
	"history": "历史订单",
	"incremental": "最新订单增量",
	"recheck_7": "7天核对",
	"recheck_14": "14天核对",
	"recheck_30": "30天核对",
	"recheck_90": "90天核对",
	"recheck_180": "180天核对",
}


class OzonOrderConfiguration(Document):
	def validate(self):
		if self.ozon_store:
			get_store(self.ozon_store, require_enabled=False)
		if cint(self.enabled) and not (cint(self.fetch_fbs_crossborder) or cint(self.fetch_fbo)):
			frappe.throw("Please enable at least one of FBS/Cross-border or FBO order fetching")
		self.incremental_interval_minutes = max(cint(self.incremental_interval_minutes), 1)
		self.incremental_lookback_minutes = max(cint(self.incremental_lookback_minutes), 1)
		for days in RECHECK_DAYS:
			fieldname = f"recheck_{days}_interval_days"
			self.set(fieldname, max(cint(self.get(fieldname)), 1))
		if self.history_start_datetime and self.history_end_datetime:
			if get_datetime(self.history_end_datetime) <= get_datetime(self.history_start_datetime):
				frappe.throw("History end datetime must be later than history start datetime")


def _to_utc(value):
	if not value:
		return None
	dt = get_datetime(value)
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=ZoneInfo(get_system_timezone()))
	return dt.astimezone(timezone.utc)


def _to_system(value):
	if not value:
		return None
	if value.tzinfo is None:
		value = value.replace(tzinfo=timezone.utc)
	return value.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)


def _ozon_datetime(value):
	return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _get_configuration(name):
	ensure_database_connection()
	return frappe.get_doc(DOCTYPE, name)


def _update_configuration(name, **values):
	ensure_database_connection()
	valid = set(frappe.get_meta(DOCTYPE).get_valid_columns())
	values = {key: value for key, value in values.items() if key in valid}
	if values:
		frappe.db.set_value(DOCTYPE, name, values, update_modified=False)
		frappe.db.commit()


def _empty_summary():
	return {
		"created": 0,
		"updated": 0,
		"archived": 0,
		"pages": 0,
		"postings": 0,
		"windows": 0,
	}


def _merge_summary(target, source):
	for key in ("created", "updated", "archived", "pages", "postings", "windows"):
		target[key] = cint(target.get(key)) + cint(source.get(key))
	return target


def _summary_text(status, summary):
	return json.dumps({"status": status, **summary}, ensure_ascii=False, default=str)


def _plan_history(config, current_utc=None):
	if not config.history_start_datetime or not config.history_end_datetime:
		raise ValueError("History start datetime and history end datetime are required")
	start = _to_utc(config.history_start_datetime)
	target_end = _to_utc(config.history_end_datetime)
	if target_end <= start:
		raise ValueError("History end datetime must be later than history start datetime")
	now_utc = current_utc or datetime.now(timezone.utc)
	safe_end = now_utc.astimezone(timezone.utc) - timedelta(minutes=SAFE_DELAY_MINUTES)
	available_end = min(target_end, safe_end)
	cursor = _to_utc(config.history_checkpoint) or start
	cursor = max(cursor, start)
	total_seconds = max((target_end - start).total_seconds(), 1)
	progress = min(max((cursor - start).total_seconds() / total_seconds * 100, 0), 100)
	if cursor >= available_end:
		return {
			"status": "waiting" if target_end > safe_end else "complete",
			"progress": progress,
			"cursor": cursor,
			"target_end": target_end,
		}
	window_end = min(available_end, cursor + timedelta(days=HISTORY_WINDOW_DAYS))
	return {
		"status": "ready",
		"start": cursor,
		"end": window_end,
		"target_start": start,
		"target_end": target_end,
		"progress": progress,
	}


def _fulfillment_type(posting):
	clues = " ".join(
		str(posting.get(key) or "")
		for key in ("integration_type_flow", "tpl_integration_type", "container_sort_type")
	).lower()
	if "realfbs" in clues or "real_fbs" in clues:
		return "realFBS"
	if "fbp" in clues:
		return "FBP"
	return "FBS"


def _record_save_result(summary, result):
	summary["created"] += cint(result.get("created"))
	summary["updated"] += cint(result.get("updated"))
	summary["archived"] += cint(result.get("archived"))


def _ozon_time_to_system(value):
	"""Convert an Ozon RFC3339 value to a naive ERPNext system-time datetime."""
	if not value:
		return None
	text = str(value).strip()
	if text.endswith("Z"):
		text = text[:-1] + "+00:00"
	try:
		parsed = get_datetime(text)
	except Exception:
		return None
	if parsed.tzinfo is None:
		parsed = parsed.replace(tzinfo=timezone.utc)
	return parsed.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)


def _safe_json_array(value):
	try:
		result = json.loads(value or "[]")
		return result if isinstance(result, list) else []
	except (TypeError, ValueError, json.JSONDecodeError):
		return []


def _append_previous_json(doc, new_hash):
	"""Archive the previous payload only when Ozon returned different JSON."""
	old_json = str(doc.get("raw_json") or "").strip()
	old_hash = str(doc.get("raw_json_hash") or "").strip()
	if not old_json or not old_hash or old_hash == new_hash:
		return False
	history = _safe_json_array(doc.get("raw_json_history"))
	try:
		old_payload = json.loads(old_json)
	except (TypeError, ValueError, json.JSONDecodeError):
		old_payload = old_json
	history.append(
		{
			"archived_at": str(now_datetime()),
			"ozon_status": doc.get("ozon_status"),
			"ozon_substatus": doc.get("ozon_substatus"),
			"sync_type": doc.get("sync_type"),
			"raw_json_hash": old_hash,
			"raw_json": old_payload,
		}
	)
	doc.raw_json_history = json.dumps(history, ensure_ascii=False, sort_keys=True, indent=2)
	return True


def _find_corresponding_item(store, sku, offer_id, product_id):
	if not store:
		return None, None, None
	base = {"启用": 1, "店铺": store}
	lookups = []
	for value in (offer_id, sku):
		value = str(value or "").strip()
		if value:
			lookups.append({**base, "平台sku": value})
	for value in (product_id, sku):
		value = str(value or "").strip()
		if value:
			lookups.append({**base, "平台asin": value})
	item_code = None
	for filters in lookups:
		item_code = frappe.db.get_value(MAPPING_DOCTYPE, filters, "物料id")
		if item_code:
			break
	if not item_code:
		return None, None, None
	item = frappe.db.get_value("Item", item_code, ["item_name", "image"], as_dict=True) or {}
	return item_code, item.get("item_name"), item.get("image")


def _financial_product(posting, sku, product_id):
	rows = ((posting.get("financial_data") or {}).get("products") or [])
	wanted = {str(value) for value in (sku, product_id) if value not in (None, "")}
	for row in rows:
		if str(row.get("product_id") or "") in wanted:
			return row
	return {}


def _line_key(store, fulfillment_type, posting_number, sku, offer_id, product_id):
	identity = "|".join(
		str(value or "").strip()
		for value in (
			store,
			fulfillment_type,
			posting_number,
			sku or offer_id or product_id or "__ORDER__",
		)
	)
	return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _save_order(posting, store, ozon_id, fulfillment_type, sync_type):
	"""Upsert one row per Ozon posting and SKU using the new order controller."""
	if not isinstance(posting, dict):
		raise ValueError("Ozon订单数据必须是JSON对象")
	posting_number = str(posting.get("posting_number") or "").strip()
	order_id = str(posting.get("order_id") or "").strip()
	order_number = str(posting.get("order_number") or "").strip()
	if not posting_number and not order_id and not order_number:
		raise ValueError("Ozon订单缺少posting_number、order_id和order_number")
	posting_number = posting_number or order_number or order_id
	raw_text = json.dumps(posting, ensure_ascii=False, sort_keys=True, indent=2)
	raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
	analytics = posting.get("analytics_data") or {}
	delivery_method = posting.get("delivery_method") or {}
	cancellation = posting.get("cancellation") or {}
	customer = posting.get("customer") or {}
	address = customer.get("address") or posting.get("address") or {}
	base_data = {
		"store": store,
		"ozon_id": str(ozon_id or "").strip(),
		"fulfillment_type": fulfillment_type or "未知",
		"posting_number": posting_number,
		"parent_posting_number": posting.get("parent_posting_number"),
		"order_id": order_id,
		"order_number": order_number,
		"is_multibox": cint(posting.get("is_multibox")),
		"multi_box_qty": cint(posting.get("multi_box_qty")),
		"ozon_status": posting.get("status"),
		"ozon_substatus": posting.get("substatus"),
		"cancellation_reason": cancellation.get("cancel_reason"),
		"is_cancelled": cint(bool(cancellation.get("cancel_reason_id")) or posting.get("status") == "cancelled"),
		"is_express": cint(posting.get("is_express")),
		"sync_type": "新订单增量" if sync_type == "最新订单增量" else sync_type,
		"order_created_at": _ozon_time_to_system(posting.get("created_at") or posting.get("in_process_at")),
		"in_process_at": _ozon_time_to_system(posting.get("in_process_at")),
		"source_updated_at": _ozon_time_to_system(posting.get("updated_at") or posting.get("last_changed_status_date")),
		"shipment_date": _ozon_time_to_system(posting.get("shipment_date")),
		"delivering_date": _ozon_time_to_system(posting.get("delivering_date")),
		"delivery_date": _ozon_time_to_system(posting.get("delivery_date")),
		"warehouse_id": delivery_method.get("warehouse_id") or analytics.get("warehouse_id"),
		"warehouse_name": delivery_method.get("warehouse") or analytics.get("warehouse"),
		"delivery_method_id": delivery_method.get("id"),
		"delivery_type": analytics.get("delivery_type") or posting.get("tpl_integration_type"),
		"delivery_method_name": delivery_method.get("name"),
		"tracking_number": posting.get("tracking_number"),
		"destination_place_id": posting.get("destination_place_id"),
		"destination_place_name": posting.get("destination_place_name"),
		"shipping_country_code": address.get("country_code") or address.get("country"),
		"shipping_region": analytics.get("region") or address.get("region"),
		"shipping_city": analytics.get("city") or address.get("city"),
		"shipping_postal_code": address.get("zip_code") or address.get("postal_code"),
		"fetched_at": now_datetime(),
		"raw_json_hash": raw_hash,
		"sync_status": "成功",
		"last_error": None,
		"raw_json": raw_text,
	}
	base_data = {key: value for key, value in base_data.items() if value is not None}
	products = posting.get("products") or [{}]
	grouped = {}
	for product in products:
		sku = str(product.get("sku") or "").strip()
		offer_id = str(product.get("offer_id") or "").strip()
		product_id = str(product.get("product_id") or sku or "").strip()
		grouped.setdefault(sku or offer_id or product_id or "__ORDER__", []).append(product)

	created = updated = archived = 0
	document_names = []
	for product_rows in grouped.values():
		first = product_rows[0]
		sku = str(first.get("sku") or "").strip()
		offer_id = str(first.get("offer_id") or "").strip()
		product_id = str(first.get("product_id") or sku or "").strip()
		financial = _financial_product(posting, sku, product_id)
		quantity = sum(cint(row.get("quantity")) for row in product_rows)
		unit_price = flt(first.get("price") or financial.get("price"))
		currency = first.get("currency_code") or financial.get("currency_code")
		item_code, item_name, item_image = _find_corresponding_item(store, sku, offer_id, product_id)
		unique_key = _line_key(store, fulfillment_type, posting_number, sku, offer_id, product_id)
		line_data = {
			**base_data,
			"order_line_key": unique_key,
			"product_id": product_id,
			"sku": sku,
			"offer_id": offer_id,
			"product_name": first.get("name"),
			"corresponding_item": item_code,
			"corresponding_item_name": item_name,
			"corresponding_item_image": item_image,
			"currency_code": currency,
			"unit_price": unit_price,
			"line_amount": sum(flt(row.get("price")) * cint(row.get("quantity")) for row in product_rows),
			"quantity": quantity,
			"commission_amount": flt(financial.get("commission_amount")) if "commission_amount" in financial else None,
			"payout_amount": flt(financial.get("payout")) if "payout" in financial else None,
		}
		line_data = {key: value for key, value in line_data.items() if value not in (None, "")}
		existing_name = frappe.db.get_value(STORAGE_DOCTYPE, {"order_line_key": unique_key}, "name")
		if existing_name:
			doc = frappe.get_doc(STORAGE_DOCTYPE, existing_name)
			if _append_previous_json(doc, raw_hash):
				archived += 1
			doc.update(line_data)
			doc.save(ignore_permissions=True)
			updated += 1
		else:
			line_data["first_fetched_at"] = now_datetime()
			doc = frappe.get_doc({"doctype": STORAGE_DOCTYPE, **line_data})
			doc.insert(ignore_permissions=True)
			created += 1
		document_names.append(doc.name)
	return {"created": created, "updated": updated, "archived": archived, "document_names": document_names}


def _query_fbs_window(store, sync_label, start_utc, end_utc):
	summary = _empty_summary()
	offset = 0
	limit = 1000
	while True:
		response = ozon_seller_request(
			store,
			"POST",
			"/v3/posting/fbs/list",
			json_data={
				"dir": "ASC",
				"filter": {"since": _ozon_datetime(start_utc), "to": _ozon_datetime(end_utc)},
				"limit": limit,
				"offset": offset,
				"with": {
					"analytics_data": True,
					"barcodes": True,
					"financial_data": True,
					"translit": True,
				},
			},
			timeout=60,
		)
		ensure_database_connection()
		if response is None or response.status_code != 200:
			status = response.status_code if response is not None else "no response"
			raise RuntimeError(f"Ozon FBS orders API failed (HTTP {status}): {response_error_summary(response)}")
		payload = response.json() or {}
		result = payload.get("result") or {}
		postings = result.get("postings") or []
		for posting in postings:
			save_result = _save_order(
				posting,
				store.cost_center,
				str(store.ozon_id or "").strip(),
				_fulfillment_type(posting),
				sync_label,
			)
			_record_save_result(summary, save_result)
		frappe.db.commit()
		summary["pages"] += 1
		summary["postings"] += len(postings)
		if not result.get("has_next"):
			break
		if not postings:
			raise RuntimeError("Ozon FBS API returned has_next but the current page is empty")
		offset += len(postings)
	return summary


def _query_fbo_window(store, sync_label, start_utc, end_utc):
	summary = _empty_summary()
	offset = 0
	limit = 1000
	while True:
		response = ozon_seller_request(
			store,
			"POST",
			"/v2/posting/fbo/list",
			json_data={
				"dir": "ASC",
				"filter": {"since": _ozon_datetime(start_utc), "to": _ozon_datetime(end_utc)},
				"limit": limit,
				"offset": offset,
				"translit": True,
				"with": {"analytics_data": True, "financial_data": True},
			},
			timeout=60,
		)
		ensure_database_connection()
		if response is None or response.status_code != 200:
			status = response.status_code if response is not None else "no response"
			raise RuntimeError(f"Ozon FBO orders API failed (HTTP {status}): {response_error_summary(response)}")
		payload = response.json() or {}
		result = payload.get("result") or []
		if isinstance(result, dict):
			postings = result.get("postings") or []
			has_next = bool(result.get("has_next"))
		else:
			postings = result if isinstance(result, list) else []
			has_next = len(postings) >= limit
		for posting in postings:
			save_result = _save_order(
				posting,
				store.cost_center,
				str(store.ozon_id or "").strip(),
				"FBO",
				sync_label,
			)
			_record_save_result(summary, save_result)
		frappe.db.commit()
		summary["pages"] += 1
		summary["postings"] += len(postings)
		if not has_next:
			break
		if not postings:
			raise RuntimeError("Ozon FBO API requires another page but the current page is empty")
		offset += len(postings)
	return summary


def _query_window(config, store, sync_type, start_utc, end_utc):
	if start_utc >= end_utc:
		return _empty_summary()
	if not (cint(config.fetch_fbs_crossborder) or cint(config.fetch_fbo)):
		raise ValueError("At least one of FBS/Cross-border or FBO order fetching must be enabled")

	summary = _empty_summary()
	sync_label = SYNC_TYPE_LABELS[sync_type]
	if cint(config.fetch_fbs_crossborder):
		_merge_summary(summary, _query_fbs_window(store, sync_label, start_utc, end_utc))
	if cint(config.fetch_fbo):
		_merge_summary(summary, _query_fbo_window(store, sync_label, start_utc, end_utc))
	return summary


def _query_range(config, store, sync_type, start_utc, end_utc):
	summary = _empty_summary()
	cursor = start_utc
	while cursor < end_utc:
		window_end = min(cursor + timedelta(days=HISTORY_WINDOW_DAYS), end_utc)
		window = _query_window(config, store, sync_type, cursor, window_end)
		_merge_summary(summary, window)
		summary["windows"] += 1
		cursor = window_end
	return summary


def _task_lock(name):
	return frappe.cache().lock(
		frappe.cache().make_key(f"fengjing:ozon-order-config:{name}"),
		timeout=TASK_TIMEOUT,
		blocking_timeout=0,
	)


def _enqueue(name, sync_type):
	job_suffix = hashlib.sha256(f"{name}|{sync_type}".encode("utf-8")).hexdigest()[:20]
	frappe.enqueue(
		execute_order_sync,
		queue="long",
		timeout=TASK_TIMEOUT,
		enqueue_after_commit=True,
		job_id=f"ozon-order-config-{job_suffix}",
		deduplicate=True,
		configuration_name=name,
		sync_type=sync_type,
	)


@frappe.whitelist()
def start_history_sync(name):
	config = _get_configuration(name)
	if not cint(config.enabled):
		frappe.throw("Please enable order sync first")
	if not config.history_start_datetime or not config.history_end_datetime:
		frappe.throw("Please set the history start and end datetime first")
	get_store(config.ozon_store)
	values = {
		"history_status": "Waiting",
		"current_task_status": "Waiting",
		"current_execution_type": "history",
		"last_error": "",
		"history_last_error": "",
	}
	# Clicking again after completion intentionally rechecks the full configured range.
	if config.history_status == "Completed":
		values.update(
			{
				"history_checkpoint": None,
				"history_progress": 0,
				"history_inserted_count": 0,
				"history_updated_count": 0,
				"history_archived_count": 0,
				"history_summary": "",
			}
		)
	_update_configuration(name, **values)
	_enqueue(name, "history")
	return {"status": "queued", "message": "Ozon order history sync has been queued"}


@frappe.whitelist()
def start_incremental_sync(name):
	config = _get_configuration(name)
	if not cint(config.enabled):
		frappe.throw("Please enable order sync first")
	get_store(config.ozon_store)
	_update_configuration(
		name,
		current_task_status="Waiting",
		current_execution_type="incremental",
		last_error="",
	)
	_enqueue(name, "incremental")
	return {"status": "queued", "message": "Ozon order incremental sync has been queued"}


@frappe.whitelist()
def start_recheck_sync(name, days):
	days = cint(days)
	if days not in RECHECK_DAYS:
		frappe.throw("Unsupported Ozon order recheck range")
	config = _get_configuration(name)
	if not cint(config.enabled):
		frappe.throw("Please enable order sync first")
	get_store(config.ozon_store)
	_update_configuration(
		name,
		current_task_status="Waiting",
		current_execution_type=f"recheck_{days}",
		last_error="",
	)
	_enqueue(name, f"recheck_{days}")
	return {"status": "queued", "message": f"Ozon order {days}-day recheck has been queued"}


def _next_recheck(now, interval_days):
	return get_datetime(now).replace(hour=2, minute=59, second=0, microsecond=0) + timedelta(
		days=max(cint(interval_days), 1)
	)


def _first_recheck(now):
	now = get_datetime(now)
	candidate = now.replace(hour=2, minute=59, second=0, microsecond=0)
	return candidate if candidate > now else candidate + timedelta(days=1)


def execute_order_sync(configuration_name, sync_type):
	if sync_type not in SYNC_TYPE_LABELS:
		raise ValueError(f"Unsupported Ozon order sync type: {sync_type}")
	lock = _task_lock(configuration_name)
	if not lock.acquire(blocking=False):
		return {"status": "busy", "message": "This Ozon order configuration is already running"}

	started_at = now_datetime()
	summary = _empty_summary()
	try:
		_update_configuration(
			configuration_name,
			current_task_status="Running",
			current_execution_type=sync_type,
			current_task_started_at=started_at,
			last_error="",
			**({"history_status": "Running", "history_last_error": ""} if sync_type == "history" else {}),
		)
		config = _get_configuration(configuration_name)
		if not cint(config.enabled):
			raise ValueError("Ozon order synchronization is disabled")
		store = get_store(config.ozon_store)

		if sync_type == "history":
			summary.update(
				{
					"created": cint(config.history_inserted_count),
					"updated": cint(config.history_updated_count),
					"archived": cint(config.get("history_archived_count")),
				}
			)
			while True:
				config = _get_configuration(configuration_name)
				plan = _plan_history(config)
				if plan["status"] != "ready":
					status = "Completed" if plan["status"] == "complete" else "Waiting"
					text = _summary_text(status, summary)
					_update_configuration(
						configuration_name,
						history_status=status,
						history_progress=100 if status == "Completed" else plan["progress"],
						history_summary=text,
						current_task_status="Success" if status == "Completed" else "Waiting",
						current_execution_type="",
						current_task_completed_at=now_datetime(),
						last_run_result=text,
					)
					return {"status": plan["status"], "summary": summary}

				window = _query_window(config, store, sync_type, plan["start"], plan["end"])
				_merge_summary(summary, window)
				summary["windows"] += 1
				total = max((plan["target_end"] - plan["target_start"]).total_seconds(), 1)
				progress = min(
					max((plan["end"] - plan["target_start"]).total_seconds() / total * 100, 0),
					100,
				)
				_update_configuration(
					configuration_name,
					history_checkpoint=_to_system(plan["end"]),
					history_progress=progress,
					history_inserted_count=summary["created"],
					history_updated_count=summary["updated"],
					history_archived_count=summary["archived"],
					history_summary=json.dumps(summary, ensure_ascii=False),
				)

		config = _get_configuration(configuration_name)
		end_utc = datetime.now(timezone.utc) - timedelta(minutes=SAFE_DELAY_MINUTES)
		if sync_type == "incremental":
			lookback = max(cint(config.incremental_lookback_minutes), 1)
			checkpoint = _to_utc(config.incremental_checkpoint)
			start_utc = (checkpoint or end_utc) - timedelta(minutes=lookback)
		else:
			days = int(sync_type.rsplit("_", 1)[1])
			start_utc = end_utc - timedelta(days=days)

		summary = _query_range(config, store, sync_type, start_utc, end_utc)
		finished_at = now_datetime()
		values = {
			"current_task_status": "Success",
			"current_execution_type": "",
			"current_task_completed_at": finished_at,
			"last_run_result": _summary_text("Success", summary),
			"last_error": "",
		}
		if sync_type == "incremental":
			values.update(
				{
					"incremental_checkpoint": _to_system(end_utc),
					"incremental_last_at": finished_at,
					"incremental_next_at": finished_at
					+ timedelta(minutes=max(cint(config.incremental_interval_minutes), 1)),
					"incremental_summary": json.dumps(summary, ensure_ascii=False),
				}
			)
		else:
			days = int(sync_type.rsplit("_", 1)[1])
			values.update(
				{
					f"recheck_{days}_last_at": finished_at,
					f"recheck_{days}_next_at": _next_recheck(
						finished_at, config.get(f"recheck_{days}_interval_days")
					),
				}
			)
		_update_configuration(configuration_name, **values)
		return {"status": "success", "summary": summary}
	except Exception as exc:
		now = now_datetime()
		values = {
			"current_task_status": "Failed",
			"current_execution_type": "",
			"current_task_completed_at": now,
			"last_error": str(exc)[:2000],
			"last_run_result": f"Failed: {str(exc)[:1800]}",
		}
		if sync_type == "history":
			values.update({"history_status": "Failed", "history_last_error": str(exc)[:2000]})
		elif sync_type == "incremental":
			values["incremental_last_at"] = now
			values["incremental_next_at"] = now + timedelta(minutes=15)
		else:
			days = int(sync_type.rsplit("_", 1)[1])
			values[f"recheck_{days}_next_at"] = now + timedelta(minutes=15)
		_update_configuration(configuration_name, **values)
		frappe.logger("ozon_orders_v2", allow_site=True).exception(
			"Ozon order sync failed: configuration=%s type=%s", configuration_name, sync_type
		)
		raise
	finally:
		try:
			lock.release()
		except Exception:
			pass


def run_scheduled_order_sync():
	"""Queue due jobs after this function is explicitly connected to hooks later."""
	now = now_datetime()
	for name in frappe.get_all(DOCTYPE, filters={"enabled": 1}, pluck="name"):
		try:
			config = _get_configuration(name)
			get_store(config.ozon_store)
			if config.current_task_status == "Running":
				started_at = get_datetime(config.current_task_started_at) if config.current_task_started_at else None
				if started_at and (now - started_at).total_seconds() > TASK_TIMEOUT:
					_update_configuration(
						name,
						current_task_status="Failed",
						current_execution_type="",
						current_task_completed_at=now,
						last_error="Previous task exceeded six hours and was released by the scheduler",
					)
					config = _get_configuration(name)
				else:
					continue

			if (
				config.history_status in {"Waiting", "Running"}
				and config.history_start_datetime
				and config.history_end_datetime
			):
				plan = _plan_history(config)
				if plan["status"] in {"ready", "complete"}:
					_enqueue(name, "history")
					continue

			if not config.incremental_next_at or get_datetime(config.incremental_next_at) <= now:
				_enqueue(name, "incremental")
				continue

			initialized = {}
			for days in RECHECK_DAYS:
				if cint(config.get(f"enable_recheck_{days}")) and not config.get(f"recheck_{days}_next_at"):
					initialized[f"recheck_{days}_next_at"] = _first_recheck(now)
			if initialized:
				_update_configuration(name, **initialized)
				continue

			for days in RECHECK_DAYS:
				if not cint(config.get(f"enable_recheck_{days}")):
					continue
				next_at = config.get(f"recheck_{days}_next_at")
				if next_at and get_datetime(next_at) <= now:
					_enqueue(name, f"recheck_{days}")
					break
		except Exception:
			frappe.logger("ozon_orders_v2", allow_site=True).exception(
				"Ozon order scheduler failed: configuration=%s", name
			)


# Chinese aliases retained for future custom configuration-page buttons/hooks.
启动Ozon历史订单同步 = start_history_sync
启动Ozon最新订单同步 = start_incremental_sync
定时执行Ozon订单同步 = run_scheduled_order_sync

