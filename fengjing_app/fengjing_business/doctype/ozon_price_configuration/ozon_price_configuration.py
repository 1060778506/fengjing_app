# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""Ozon price snapshots driven by the independent price configuration."""

import hashlib
import json
import uuid
from datetime import timedelta

import frappe
from frappe.model.document import Document
from frappe.utils import cint, get_datetime, now_datetime

from fengjing_app.fengjing_business.doctype.ozon_store_configuration.ozon_store_configuration import (
	ensure_database_connection,
	get_store,
	ozon_seller_request,
	response_error_summary,
)


DOCTYPE = "Ozon Price Configuration"
STORAGE_DOCTYPE = "Ozon Price History"
TASK_TIMEOUT = 2 * 60 * 60
PAGE_SIZE = 1000


class OzonPriceConfiguration(Document):
	def validate(self):
		if self.ozon_store:
			get_store(self.ozon_store, require_enabled=False)
		if cint(self.enabled) and not (
			cint(self.record_cost_price)
			or cint(self.record_promotion_price)
			or cint(self.record_market_price)
		):
			frappe.throw("Please enable at least one price type to record")
		self.interval_minutes = max(cint(self.interval_minutes), 5)


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


def _request_json(store, path, body, api_name):
	response = ozon_seller_request(store, "POST", path, json_data=body, timeout=90)
	ensure_database_connection()
	if response is None or response.status_code != 200:
		status = response.status_code if response is not None else "no response"
		raise RuntimeError(f"Ozon {api_name} failed (HTTP {status}): {response_error_summary(response, 1800)}")
	try:
		payload = response.json()
	except ValueError as exc:
		raise RuntimeError(f"Ozon {api_name} returned invalid JSON") from exc
	if not isinstance(payload, dict):
		raise RuntimeError(f"Ozon {api_name} returned an unexpected structure")
	return payload


def _read_all_prices(store):
	items = []
	cursor = ""
	seen_cursors = set()
	while True:
		payload = _request_json(
			store,
			"/v5/product/info/prices",
			{"filter": {"visibility": "ALL"}, "limit": PAGE_SIZE, "cursor": cursor},
			"product prices API",
		)
		page_items = payload.get("items") or (payload.get("result") or {}).get("items") or []
		if not isinstance(page_items, list):
			raise RuntimeError("Ozon product prices API items field is not a list")
		items.extend(row for row in page_items if isinstance(row, dict))
		next_cursor = str(
			payload.get("cursor") or (payload.get("result") or {}).get("cursor") or ""
		).strip()
		if len(page_items) < PAGE_SIZE or not next_cursor:
			break
		if next_cursor == cursor or next_cursor in seen_cursors:
			raise RuntimeError("Ozon product prices pagination cursor did not advance")
		seen_cursors.add(next_cursor)
		cursor = next_cursor
	return items


def _chunks(values, size=1000):
	for start in range(0, len(values), size):
		yield values[start : start + size]


def _read_product_info(store, product_ids):
	result = {}
	for batch in _chunks(list(dict.fromkeys(product_ids))):
		if not batch:
			continue
		payload = _request_json(
			store,
			"/v3/product/info/list",
			{"product_id": [int(value) if str(value).isdigit() else value for value in batch]},
			"product information API",
		)
		items = payload.get("items") or (payload.get("result") or {}).get("items") or []
		for item in items:
			product_id = str(item.get("id") or item.get("product_id") or "").strip()
			if product_id:
				result[product_id] = item
	return result


def _storage_context(store):
	return frappe._dict({"店铺选项": store.cost_center, "ozon_id": store.ozon_id})


def _clear_disabled_price_fields(config, store, product_id, hour_bucket):
	name = frappe.db.get_value(
		STORAGE_DOCTYPE,
		{"store": store.cost_center, "ozon_product_id": product_id, "hour_bucket": hour_bucket},
		"name",
	)
	if not name:
		return
	values = {}
	if not cint(config.record_cost_price):
		values["ozon_cost_price"] = None
	if not cint(config.record_promotion_price):
		values.update(
			{
				"marketing_seller_price": None,
				"promotion_discount": None,
				"has_promotion": 0,
			}
		)
	if not cint(config.record_market_price):
		values.update({"market_min_price": None, "price_index": None})
	if values:
		frappe.db.set_value(STORAGE_DOCTYPE, name, values, update_modified=False)


def _task_lock(name):
	return frappe.cache().lock(
		frappe.cache().make_key(f"fengjing:ozon-price-config:{name}"),
		timeout=TASK_TIMEOUT,
		blocking_timeout=0,
	)


def _enqueue(name, manual=False):
	digest = hashlib.sha256(str(name).encode("utf-8")).hexdigest()[:20]
	frappe.enqueue(
		execute_price_recording,
		queue="long",
		timeout=TASK_TIMEOUT,
		enqueue_after_commit=True,
		job_id=f"ozon-price-config-{digest}",
		deduplicate=True,
		configuration_name=name,
		manual=cint(manual),
	)


@frappe.whitelist()
def start_price_recording(name):
	config = _get_configuration(name)
	if not cint(config.enabled):
		frappe.throw("Please enable price recording first")
	get_store(config.ozon_store)
	_update_configuration(
		name,
		current_task_status="Waiting",
		current_execution_type="manual",
		last_error="",
	)
	_enqueue(name, manual=True)
	return {"status": "queued", "message": "Ozon price recording has been queued"}


def execute_price_recording(configuration_name, manual=0):
	from fengjing_app.fengjing_business.doctype.ozon_price_history.ozon_price_history import (
		_加载物料映射,
		_匹配物料,
		_取得sku列表,
		_保存价格快照,
	)

	lock = _task_lock(configuration_name)
	if not lock.acquire(blocking=False):
		return {"status": "busy", "message": "This Ozon price configuration is already running"}
	try:
		started_at = now_datetime()
		_update_configuration(
			configuration_name,
			current_task_status="Running",
			current_execution_type="manual" if cint(manual) else "scheduled",
			current_task_started_at=started_at,
			last_error="",
		)
		config = _get_configuration(configuration_name)
		if not cint(config.enabled):
			raise ValueError("Ozon price recording is disabled")
		store = get_store(config.ozon_store)
		recorded_at = now_datetime()
		hour_bucket = recorded_at.replace(minute=0, second=0, microsecond=0)
		batch_id = uuid.uuid4().hex
		prices = _read_all_prices(store)
		product_ids = [
			str(item.get("product_id") or "").strip()
			for item in prices
			if item.get("product_id")
		]
		try:
			product_info = _read_product_info(store, product_ids)
		except Exception:
			product_info = {}
			frappe.logger("ozon_price_v2", allow_site=True).exception(
				"Ozon product details failed; price snapshots will continue: configuration=%s",
				configuration_name,
			)
		mapping_rows = _加载物料映射(store.cost_center)
		context = _storage_context(store)
		summary = {
			"products": len(prices),
			"created": 0,
			"updated": 0,
			"skipped": 0,
			"failed": 0,
		}
		failures = []
		for index, item in enumerate(prices, 1):
			product_id = str(item.get("product_id") or "").strip()
			info = product_info.get(product_id, {})
			if config.product_scope == "Only Bound Items":
				offer_id = str(item.get("offer_id") or info.get("offer_id") or "").strip()
				skus = _取得sku列表(item, info)
				if not _匹配物料(mapping_rows, product_id, offer_id, skus):
					summary["skipped"] += 1
					continue
			try:
				result = _保存价格快照(
					context,
					item,
					info,
					mapping_rows,
					batch_id,
					recorded_at,
					hour_bucket,
				)
				summary["created" if result == "inserted" else "updated"] += 1
				_clear_disabled_price_fields(config, store, product_id, hour_bucket)
			except Exception as exc:
				summary["failed"] += 1
				failures.append(f"{product_id or 'unknown'}: {str(exc)[:300]}")
			if index % 200 == 0:
				frappe.db.commit()
		frappe.db.commit()
		processed = summary["created"] + summary["updated"]
		if prices and processed == 0 and summary["failed"]:
			raise RuntimeError("All Ozon price records failed: " + "; ".join(failures[:5]))

		finished_at = now_datetime()
		result_text = json.dumps(
			{"status": "Success", "batch_id": batch_id, **summary}, ensure_ascii=False
		)
		_update_configuration(
			configuration_name,
			last_recorded_at=finished_at,
			next_record_at=finished_at + timedelta(minutes=max(cint(config.interval_minutes), 5)),
			last_record_count=processed,
			last_record_result=result_text,
			current_task_status="Success",
			current_execution_type="",
			current_task_completed_at=finished_at,
			last_run_result=result_text,
			last_error="",
		)
		return {"status": "success", "batch_id": batch_id, "summary": summary}
	except Exception as exc:
		frappe.db.rollback()
		now = now_datetime()
		_update_configuration(
			configuration_name,
			last_recorded_at=now,
			next_record_at=now + timedelta(minutes=15),
			last_record_result=f"Failed: {str(exc)[:1800]}",
			current_task_status="Failed",
			current_execution_type="",
			current_task_completed_at=now,
			last_run_result=f"Failed: {str(exc)[:1800]}",
			last_error=str(exc)[:2000],
		)
		frappe.logger("ozon_price_v2", allow_site=True).exception(
			"Ozon price recording failed: configuration=%s manual=%s",
			configuration_name,
			manual,
		)
		raise
	finally:
		try:
			lock.release()
		except Exception:
			pass


def run_scheduled_price_recording():
	"""Queue due jobs after this function is explicitly connected to hooks later."""
	now = now_datetime()
	for name in frappe.get_all(DOCTYPE, filters={"enabled": 1}, pluck="name"):
		try:
			config = _get_configuration(name)
			get_store(config.ozon_store)
			if config.current_task_status == "Running":
				started = get_datetime(config.current_task_started_at) if config.current_task_started_at else None
				if started and (now - started).total_seconds() > TASK_TIMEOUT:
					_update_configuration(
						name,
						current_task_status="Failed",
						current_execution_type="",
						current_task_completed_at=now,
						last_error="Previous task exceeded two hours and was released by the scheduler",
					)
					config = _get_configuration(name)
				else:
					continue
			if not config.next_record_at or get_datetime(config.next_record_at) <= now:
				_enqueue(name)
		except Exception:
			frappe.logger("ozon_price_v2", allow_site=True).exception(
				"Ozon price scheduler failed: configuration=%s", name
			)


启动Ozon价格抓取 = start_price_recording
定时执行Ozon价格记录 = run_scheduled_price_recording

