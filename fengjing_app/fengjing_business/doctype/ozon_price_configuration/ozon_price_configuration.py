# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""Ozon price snapshots driven by the independent price configuration."""

import hashlib
import json
import math
import uuid
from datetime import timedelta

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, now_datetime

from fengjing_app.fengjing_business.doctype.ozon_store_configuration.ozon_store_configuration import (
	ensure_database_connection,
	get_store,
	ozon_seller_request,
	response_error_summary,
)


DOCTYPE = "Ozon Price Configuration"
STORAGE_DOCTYPE = "Ozon Price History"
MAPPING_DOCTYPE = "Fengjing - Product Corresponding Platform - Main Table"
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


def _get_skus(price_item, product_info):
	values = [price_item.get("sku"), product_info.get("sku")]
	values.extend(
		source.get("sku")
		for source in (product_info.get("sources") or [])
		if isinstance(source, dict)
	)
	return list(dict.fromkeys(str(value).strip() for value in values if str(value or "").strip()))


def _load_item_mappings(store):
	return frappe.get_all(
		MAPPING_DOCTYPE,
		filters={"店铺": store, "启用": 1},
		fields=["平台asin", "平台sku", "物料id", "物料名称"],
		limit_page_length=0,
	)


def _match_item(mapping_rows, product_id, offer_id, skus):
	product_id = str(product_id or "").upper()
	offer_id = str(offer_id or "").upper()
	candidate_skus = {str(value).upper() for value in skus if value}
	if offer_id:
		candidate_skus.add(offer_id)
	candidates = []
	for row in mapping_rows:
		platform_product = str(row.get("平台asin") or "").upper()
		platform_sku = str(row.get("平台sku") or "").upper()
		score = 0
		if product_id and platform_product == product_id and platform_sku in candidate_skus:
			score = 4
		elif platform_sku and platform_sku in candidate_skus:
			score = 3
		elif product_id and platform_product == product_id:
			score = 2
		if score and row.get("物料id"):
			candidates.append((score, row))
	return max(candidates, key=lambda value: value[0])[1] if candidates else None


def _number(value):
	try:
		value = float(value)
		return value if math.isfinite(value) else None
	except (TypeError, ValueError):
		return None


def _first_number(*values):
	for value in values:
		result = _number(value)
		if result is not None:
			return result
	return None


def _market_price_data(price_item, seller_currency=None):
	indexes = price_item.get("price_indexes") or {}
	candidates = []
	for key in ("external_index_data", "ozon_index_data", "self_marketplaces_index_data"):
		entry = indexes.get(key) or {}
		seller_minimum = _first_number(entry.get("min_price_in_seller"))
		seller_minimum_currency = str(entry.get("min_price_in_seller_currency") or "").upper()
		original_minimum = _first_number(entry.get("minimal_price"), entry.get("min_price"))
		original_minimum_currency = str(entry.get("min_price_currency") or "").upper()
		if seller_minimum is not None and seller_minimum > 0 and (
			not seller_currency or not seller_minimum_currency or seller_minimum_currency == seller_currency
		):
			minimum = seller_minimum
		elif original_minimum is not None and original_minimum > 0 and (
			not seller_currency or not original_minimum_currency or original_minimum_currency == seller_currency
		):
			minimum = original_minimum
		else:
			minimum = None
		index = _first_number(entry.get("price_index_value"), entry.get("index_value"))
		if minimum is not None and minimum > 0:
			candidates.append((minimum, index))
	if not candidates:
		return None, None
	return min(candidates, key=lambda value: value[0])


def _commission_percent(price_item):
	commissions = price_item.get("commissions") or {}
	if isinstance(commissions, dict):
		return _first_number(
			commissions.get("sales_percent_rfbs"),
			commissions.get("sales_percent_fbs"),
			commissions.get("sales_percent_fbo"),
		)
	if isinstance(commissions, list):
		for item in commissions:
			if isinstance(item, dict):
				value = _first_number(item.get("percent"), item.get("value"))
				if value is not None:
					return value
	return None


def _previous_price(store, product_id, current_bucket):
	rows = frappe.get_all(
		STORAGE_DOCTYPE,
		filters={
			"store": store,
			"ozon_product_id": product_id,
			"hour_bucket": ["<", current_bucket],
		},
		fields=["buyer_price", "seller_price"],
		order_by="hour_bucket desc",
		limit_page_length=1,
	)
	if not rows:
		return None
	return _first_number(rows[0].get("buyer_price"), rows[0].get("seller_price"))


def _parse_datetime(value):
	if not value:
		return None
	try:
		return get_datetime(value)
	except Exception:
		return None


def _save_price_snapshot(context, price_item, product_info, mapping_rows, batch_id, recorded_at, hour_bucket):
	"""Upsert one price snapshot without depending on the legacy price controller."""
	product_id = str(
		price_item.get("product_id") or product_info.get("id") or product_info.get("product_id") or ""
	).strip()
	if not product_id:
		raise ValueError("Ozon 价格记录缺少 Product ID")
	store = context.get("店铺选项")
	offer_id = str(price_item.get("offer_id") or product_info.get("offer_id") or "").strip()
	skus = _get_skus(price_item, product_info)
	mapping = _match_item(mapping_rows, product_id, offer_id, skus)
	price = price_item.get("price") or {}
	seller_price = _first_number(price.get("price"), price_item.get("price"))
	buyer_price = _first_number(price.get("marketing_price"), price.get("marketing_seller_price"), seller_price)
	old_price = _first_number(price.get("old_price"))
	promotion_price = _first_number(price.get("marketing_seller_price"))
	currency = str(price.get("currency_code") or price_item.get("currency_code") or "").upper()
	market_minimum, price_index = _market_price_data(price_item, currency)
	commission_percent = _commission_percent(price_item)
	previous_price = _previous_price(store, product_id, hour_bucket)
	comparison_price = buyer_price if buyer_price is not None else seller_price
	change_amount = None if previous_price is None or comparison_price is None else comparison_price - previous_price
	change_percent = None if previous_price in (None, 0) or change_amount is None else change_amount / previous_price * 100
	if previous_price is None:
		change_direction = "首次记录"
	elif abs(change_amount or 0) < 0.000001:
		change_direction = "不变"
	elif change_amount > 0:
		change_direction = "上涨"
	else:
		change_direction = "下降"
	image = product_info.get("primary_image") or product_info.get("images") or ""
	if isinstance(image, list):
		image = image[0] if image else ""
	promotion_info = price_item.get("marketing_actions") or {}
	has_promotion = bool(
		promotion_info.get("actions")
		or (old_price is not None and comparison_price is not None and comparison_price < old_price)
	)
	snapshot_key = hashlib.sha256(f"{store}|{product_id}|{hour_bucket.isoformat()}".encode("utf-8")).hexdigest()
	raw_item = {key: value for key, value in price_item.items() if key != "_attempt_count"}
	raw_json = json.dumps(raw_item, ensure_ascii=False, sort_keys=True, default=str)
	values = {
		"snapshot_key": snapshot_key,
		"store": store,
		"ozon_product_id": product_id,
		"sku_id": skus[0] if skus else "",
		"offer_id": offer_id,
		"corresponding_item": mapping.get("物料id") if mapping else None,
		"product_name": product_info.get("name") or price_item.get("name"),
		"ozon_image_url": image,
		"recorded_at": recorded_at,
		"hour_bucket": hour_bucket,
		"source_updated_at": _parse_datetime(price_item.get("updated_at") or price.get("updated_at")),
		"fetched_at": recorded_at,
		"currency_code": currency,
		"seller_price": seller_price,
		"buyer_price": buyer_price,
		"old_price": old_price,
		"marketing_seller_price": promotion_price,
		"premium_price": _first_number(price.get("premium_price")),
		"minimum_price": _first_number(price.get("min_price"), price.get("minimum_price")),
		"market_min_price": market_minimum,
		"recommended_price": _first_number(price.get("recommended_price")),
		"ozon_cost_price": _first_number(price.get("net_price")),
		"price_index": price_index,
		"commission_percent": commission_percent,
		"commission_amount": comparison_price * commission_percent / 100 if comparison_price is not None and commission_percent is not None else None,
		"promotion_discount": max((old_price or 0) - (comparison_price or 0), 0) if old_price is not None and comparison_price is not None else None,
		"has_promotion": 1 if has_promotion else 0,
		"auto_action_enabled": cint(price.get("auto_action_enabled")),
		"visibility": price_item.get("visibility") or product_info.get("visibility"),
		"product_status": price_item.get("status") or product_info.get("status_name") or product_info.get("status"),
		"previous_price": previous_price,
		"price_change_amount": change_amount,
		"price_change_percent": change_percent,
		"change_direction": change_direction,
		"price_changed": 1 if change_direction in {"上涨", "下降"} else 0,
		"sync_batch_id": batch_id,
		"attempt_count": cint(price_item.get("_attempt_count")) or 1,
		"sync_status": "成功",
		"raw_json_hash": hashlib.sha256(raw_json.encode("utf-8")).hexdigest(),
		"last_error": "",
		"raw_json": raw_json,
	}
	name = frappe.db.get_value(STORAGE_DOCTYPE, {"snapshot_key": snapshot_key}, "name")
	if name:
		doc = frappe.get_doc(STORAGE_DOCTYPE, name)
		doc.update(values)
		doc.save(ignore_permissions=True)
		return "updated"
	frappe.get_doc({"doctype": STORAGE_DOCTYPE, **values}).insert(ignore_permissions=True)
	return "inserted"


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
		mapping_rows = _load_item_mappings(store.cost_center)
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
				skus = _get_skus(item, info)
				if not _match_item(mapping_rows, product_id, offer_id, skus):
					summary["skipped"] += 1
					continue
			try:
				result = _save_price_snapshot(
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

