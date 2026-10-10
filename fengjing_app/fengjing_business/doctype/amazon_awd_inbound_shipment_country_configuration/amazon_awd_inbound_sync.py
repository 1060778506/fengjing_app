# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""AWD inbound order, shipment, item and change-history synchronization."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, get_system_timezone, now_datetime

from fengjing_app.fengjing_business.doctype.amazon_store_configuration.amazon_store_configuration import (
	AmazonAPIError,
	ensure_database_connection,
	get_region,
	get_store,
)

from .amazon_awd_inbound_api import (
	frappe_datetime,
	get_inbound_order,
	get_inbound_shipment,
	iter_inbound_shipment_pages,
)


CONFIG_DOCTYPE = "Amazon AWD Inbound Shipment Country Configuration"
MASTER_DOCTYPE = "Amazon AWD Inbound Shipment Master Configuration"
ORDER_DOCTYPE = "Amazon AWD Inbound Order"
SHIPMENT_DOCTYPE = "Amazon AWD Inbound Shipment"
ITEM_DOCTYPE = "Amazon AWD Inbound Shipment Item"
HISTORY_DOCTYPE = "Amazon AWD Inbound Shipment History"
RECHECK_DAYS = (7, 14, 30, 90, 180)
TASK_TIMEOUT = 6 * 60 * 60
QUOTA_RETRY_MINUTES = 10
COUNTRY_CODES = {
	"US": "United States", "CA": "Canada", "MX": "Mexico", "BR": "Brazil",
	"GB": "United Kingdom", "DE": "Germany", "FR": "France", "ES": "Spain",
	"IT": "Italy", "NL": "Netherlands", "BE": "Belgium", "SE": "Sweden",
	"PL": "Poland", "IE": "Ireland", "JP": "Japan", "AU": "Australia", "SG": "Singapore",
}


def _json(value, *, pretty=False):
	return json.dumps(
		value or {}, ensure_ascii=False, sort_keys=True,
		indent=2 if pretty else None, separators=None if pretty else (",", ":"), default=str,
	)


def _hash(value):
	return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _to_utc(value):
	if not value:
		return None
	dt = get_datetime(value)
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=ZoneInfo(get_system_timezone()))
	return dt.astimezone(timezone.utc)


def _master(name):
	ensure_database_connection()
	return frappe.get_doc(MASTER_DOCTYPE, name)


def _configuration(name):
	ensure_database_connection()
	return frappe.get_doc(CONFIG_DOCTYPE, name)


def _update_configuration(name, **values):
	ensure_database_connection()
	valid = set(frappe.get_meta(CONFIG_DOCTYPE).get_valid_columns())
	values = {key: value for key, value in values.items() if key in valid}
	if values:
		frappe.db.set_value(CONFIG_DOCTYPE, name, values, update_modified=False)
		frappe.db.commit()


def _enabled_group(master_name):
	master = _master(master_name)
	if not cint(master.enabled):
		frappe.throw(_("AWD入库货件总配置尚未启用。"))
	names = frappe.get_all(
		CONFIG_DOCTYPE,
		filters={"master_configuration": master.name, "enabled": 1},
		pluck="name", order_by="name asc",
	)
	configs = [_configuration(name) for name in names]
	if not configs:
		frappe.throw(_("当前总配置没有已启用的AWD入库货件国家配置。"))
	stores = [get_store(config.amazon_store) for config in configs]
	seller_ids = {str(store.seller_id or "").strip().upper() for store in stores}
	regions = {get_region(store) for store in stores}
	marketplaces = {str(store.marketplace_id or "").strip().upper() for store in stores}
	if "" in seller_ids or len(seller_ids) != 1:
		frappe.throw(_("同一个AWD入库货件总配置只能关联同一个Amazon卖家。"))
	if len(regions) != 1 or str(master.api_region or "") not in regions:
		frappe.throw(_("AWD入库货件总配置与国家配置的API区域不一致。"))
	if len(marketplaces) != len(stores):
		frappe.throw(_("同一个AWD入库货件总配置中存在重复的Marketplace ID。"))
	return master, configs, stores


def _task_lock(master_name):
	key = hashlib.sha256(str(master_name).encode("utf-8")).hexdigest()[:24]
	return frappe.cache().lock(
		frappe.cache().make_key(f"fengjing:amazon-awd-inbound:{key}"),
		timeout=TASK_TIMEOUT, blocking_timeout=0,
	)


def _enqueue(master_name, mode, days=0):
	days = cint(days)
	identity = hashlib.sha256(str(master_name).encode("utf-8")).hexdigest()[:16]
	frappe.enqueue(
		execute_inbound_sync, queue="long", timeout=TASK_TIMEOUT, enqueue_after_commit=True,
		job_id=f"amazon-awd-inbound-{identity}-{mode}-{days}", deduplicate=True,
		master_name=master_name, mode=mode, days=days,
	)


def _upsert(doctype, key_field, key, values):
	name = frappe.db.get_value(doctype, {key_field: key}, "name")
	values = {**values, key_field: key}
	if name:
		frappe.db.set_value(doctype, name, values, update_modified=False)
		return name, False
	doc = frappe.get_doc({"doctype": doctype, **values})
	doc.insert(ignore_permissions=True)
	return doc.name, True


def _address_values(prefix, address):
	address = address or {}
	return {
		f"{prefix}_name": address.get("name"),
		f"{prefix}_address_line_1": address.get("addressLine1"),
		f"{prefix}_address_line_2": address.get("addressLine2"),
		f"{prefix}_city": address.get("city"),
		f"{prefix}_state_or_region": address.get("stateOrRegion"),
		f"{prefix}_postal_code": address.get("postalCode"),
		f"{prefix}_country_code": address.get("countryCode"),
	}


def _country_target(detail, configs, stores):
	address = detail.get("destinationAddress") or {}
	code = str(address.get("countryCode") or "").strip().upper()
	target_country = COUNTRY_CODES.get(code, code)
	for config, store in zip(configs, stores):
		country = str(store.country or config.country or "").strip()
		if country == target_country or country.upper() == code:
			return config, store
	if len(configs) == 1:
		return configs[0], stores[0]
	# AWD currently often omits a country on regional summaries; prefer the US site in North America.
	for config, store in zip(configs, stores):
		if str(store.marketplace_id or "") == "ATVPDKIKX0DER":
			return config, store
	raise ValueError("AWD入库货件缺少可唯一确定国家配置的目的地信息。")


def _item_mapping(store, sku):
	from fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log import 获取平台映射物料

	item_code = 获取平台映射物料(store.cost_center, store.marketplace_id, sku=sku)
	item_name = frappe.db.get_value("Item", item_code, "item_name") if item_code else None
	return item_code, item_name


def _package_count(packages):
	return sum(cint(row.get("count")) for row in (packages or []))


def _package_products(packages):
	products = {}

	def visit(rows, multiplier=1):
		for row in rows or []:
			count = max(cint(row.get("count")), 1) * multiplier
			package = row.get("distributionPackage") or {}
			contents = package.get("contents") or {}
			for product in contents.get("products") or []:
				sku = str(product.get("sku") or "").strip()
				if not sku:
					continue
				entry = products.setdefault(sku, {"quantity": 0, "details": product})
				entry["quantity"] += flt(product.get("quantity")) * count
			visit(contents.get("packages") or [], count)

	visit(packages)
	return products


def _save_order(master, config, store, payload, shipment_id):
	order_id = str(payload.get("orderId") or "").strip()
	if not order_id:
		return None, {}, False
	now = now_datetime()
	destination = payload.get("destinationDetails") or {}
	packages = payload.get("packagesToInbound") or []
	raw_hash = _hash(payload)
	existing = frappe.db.get_value(ORDER_DOCTYPE, order_id, ["raw_json_hash"], as_dict=True)
	values = {
		"external_reference_id": payload.get("externalReferenceId"),
		"order_status": payload.get("orderStatus"),
		"master_configuration": master.name,
		"country_configuration": config.name,
		"amazon_store": store.name,
		"marketplace_id": store.marketplace_id,
		"seller_id": store.seller_id,
		"api_region": get_region(store),
		"destination_region": destination.get("destinationRegion") or payload.get("preferences", {}).get("destinationRegion"),
		"shipment_id": destination.get("shipmentId") or shipment_id,
		**_address_values("origin", payload.get("originAddress")),
		**_address_values("destination", destination.get("destinationAddress")),
		"origin_phone_number": (payload.get("originAddress") or {}).get("phoneNumber"),
		"package_count": _package_count(packages),
		"total_expected_quantity": sum(row["quantity"] for row in _package_products(packages).values()),
		"packages_json": _json(packages, pretty=True),
		"amazon_created_at": frappe_datetime(payload.get("createdAt")),
		"amazon_updated_at": frappe_datetime(payload.get("updatedAt")),
		"last_fetched_at": now,
		"raw_json_hash": raw_hash,
		"raw_json": _json(payload, pretty=True),
	}
	if not existing:
		values["first_fetched_at"] = now
	name, created = _upsert(ORDER_DOCTYPE, "order_id", order_id, values)
	return name, _package_products(packages), created or not existing or existing.raw_json_hash != raw_hash


def _save_shipment(master, config, store, payload, order_name, batch_id):
	shipment_id = str(payload.get("shipmentId") or "").strip()
	if not shipment_id:
		raise ValueError("AWD入库货件缺少shipmentId。")
	now = now_datetime()
	quantities = payload.get("shipmentSkuQuantities") or []
	expected = sum(flt((row.get("expectedQuantity") or {}).get("quantity")) for row in quantities)
	received = sum(flt((row.get("receivedQuantity") or {}).get("quantity")) for row in quantities)
	raw_hash = _hash(payload)
	existing = frappe.db.get_value(
		SHIPMENT_DOCTYPE, shipment_id,
		["shipment_status", "expected_quantity", "received_quantity", "quantity_difference", "raw_json_hash"],
		as_dict=True,
	)
	carrier = payload.get("carrierCode") or {}
	values = {
		"external_reference_id": payload.get("externalReferenceId"),
		"shipment_status": payload.get("shipmentStatus"),
		"inbound_order": order_name,
		"master_configuration": master.name,
		"country_configuration": config.name,
		"amazon_store": store.name,
		"marketplace_id": store.marketplace_id,
		"warehouse_reference_id": payload.get("warehouseReferenceId"),
		"destination_region": payload.get("destinationRegion"),
		"carrier_code_type": carrier.get("carrierCodeType"),
		"carrier_code_value": carrier.get("carrierCodeValue"),
		"tracking_id": payload.get("trackingId"),
		"ship_by": frappe_datetime(payload.get("shipBy")),
		"shipment_container_quantities_json": _json(payload.get("shipmentContainerQuantities") or [], pretty=True),
		"expected_quantity": expected,
		"received_quantity": received,
		"quantity_difference": expected - received,
		"sku_count": len(quantities),
		"origin_address_json": _json(payload.get("originAddress"), pretty=True),
		"destination_address_json": _json(payload.get("destinationAddress"), pretty=True),
		"amazon_created_at": frappe_datetime(payload.get("createdAt")),
		"amazon_updated_at": frappe_datetime(payload.get("updatedAt")),
		"last_fetched_at": now,
		"sync_batch_id": batch_id,
		"sync_status": "Completed",
		"retry_count": 0,
		"next_retry_at": None,
		"last_error": "",
		"raw_json_hash": raw_hash,
		"raw_json": _json(payload, pretty=True),
	}
	if not existing:
		values["first_fetched_at"] = now
	name, created = _upsert(SHIPMENT_DOCTYPE, "shipment_id", shipment_id, values)
	changed = created or not existing or any([
		existing.shipment_status != values["shipment_status"],
		flt(existing.expected_quantity) != expected,
		flt(existing.received_quantity) != received,
		str(existing.raw_json_hash or "") != raw_hash,
	])
	return name, existing, changed


def _save_history(*, master, config, store, shipment_name, item_name, order_name, payload, old, batch_id, sync_mode):
	observed = now_datetime()
	expected = flt(payload.get("expected_quantity"))
	received = flt(payload.get("received_quantity"))
	difference = expected - received
	old_expected = flt(old.get("expected_quantity")) if old else 0
	old_received = flt(old.get("received_quantity")) if old else 0
	old_difference = flt(old.get("quantity_difference")) if old else 0
	key = _hash({"shipment": shipment_name, "item": item_name, "observed": observed.isoformat(timespec="microseconds"), "batch": batch_id})
	frappe.get_doc({
		"doctype": HISTORY_DOCTYPE,
		"history_key": key,
		"observed_at": observed,
		"sync_mode": sync_mode,
		"sync_batch_id": batch_id,
		"inbound_shipment": shipment_name,
		"inbound_shipment_item": item_name,
		"inbound_order": order_name,
		"master_configuration": master.name,
		"country_configuration": config.name,
		"amazon_store": store.name,
		"marketplace_id": store.marketplace_id,
		"shipment_status": payload.get("shipment_status"),
		"seller_sku": payload.get("seller_sku"),
		"item_code": payload.get("item_code"),
		"amazon_updated_at": payload.get("amazon_updated_at"),
		"expected_quantity": expected,
		"received_quantity": received,
		"quantity_difference": difference,
		"expected_quantity_change": expected - old_expected,
		"received_quantity_change": received - old_received,
		"quantity_difference_change": difference - old_difference,
		"raw_json_hash": _hash(payload.get("raw_payload") or payload),
		"raw_json": _json(payload.get("raw_payload") or payload, pretty=True),
	}).insert(ignore_permissions=True)


def _save_items(master, config, store, shipment_name, order_name, shipment_payload, package_products, batch_id, sync_mode):
	now = now_datetime()
	count = 0
	for row in shipment_payload.get("shipmentSkuQuantities") or []:
		sku = str(row.get("sku") or "").strip()
		if not sku:
			continue
		expected_data = row.get("expectedQuantity") or {}
		received_data = row.get("receivedQuantity") or {}
		expected = flt(expected_data.get("quantity"))
		received = flt(received_data.get("quantity"))
		package = (package_products.get(sku) or {}).get("details") or {}
		prep = package.get("prepDetails") or {}
		item_code, item_name_value = _item_mapping(store, sku)
		external_key = hashlib.sha256(f"{shipment_name}|{sku}".encode("utf-8")).hexdigest()
		existing = frappe.db.get_value(
			ITEM_DOCTYPE, external_key,
			["expected_quantity", "received_quantity", "quantity_difference", "raw_json_hash"], as_dict=True,
		)
		raw_hash = _hash(row)
		values = {
			"inbound_shipment": shipment_name,
			"inbound_order": order_name,
			"master_configuration": master.name,
			"country_configuration": config.name,
			"amazon_store": store.name,
			"marketplace_id": store.marketplace_id,
			"seller_sku": sku,
			"item_code": item_code,
			"item_name": item_name_value,
			"unit_of_measurement": expected_data.get("unitOfMeasurement") or received_data.get("unitOfMeasurement"),
			"expected_quantity": expected,
			"received_quantity": received,
			"quantity_difference": expected - received,
			"quantity_updated_at": frappe_datetime(shipment_payload.get("updatedAt")),
			"expiration": frappe_datetime(package.get("expiration")),
			"prep_category": prep.get("prepCategory"),
			"prep_owner": prep.get("prepOwner"),
			"label_owner": prep.get("labelOwner"),
			"prep_instructions_json": _json(prep.get("prepInstructions") or [], pretty=True),
			"sync_batch_id": batch_id,
			"last_fetched_at": now,
			"raw_json_hash": raw_hash,
			"raw_json": _json(row, pretty=True),
		}
		if not existing:
			values["first_fetched_at"] = now
		name, created = _upsert(ITEM_DOCTYPE, "external_key", external_key, values)
		changed = created or not existing or any([
			flt(existing.expected_quantity) != expected,
			flt(existing.received_quantity) != received,
			str(existing.raw_json_hash or "") != raw_hash,
		])
		if changed:
			_save_history(
				master=master, config=config, store=store, shipment_name=shipment_name,
				item_name=name, order_name=order_name, old=existing, batch_id=batch_id, sync_mode=sync_mode,
				payload={
					"shipment_status": shipment_payload.get("shipmentStatus"), "seller_sku": sku,
					"item_code": item_code, "amazon_updated_at": frappe_datetime(shipment_payload.get("updatedAt")),
					"expected_quantity": expected, "received_quantity": received, "raw_payload": row,
				},
			)
		count += 1
	frappe.db.commit()
	return count


def _get_order_safely(store, order_id, summary):
	if not order_id:
		return {}
	try:
		return get_inbound_order(store, order_id)
	except AmazonAPIError as exc:
		if exc.status_code in {400, 404}:
			return {
				"orderId": order_id,
				"externalReferenceId": summary.get("externalReferenceId"),
				"createdAt": summary.get("createdAt"),
				"updatedAt": summary.get("updatedAt"),
			}
		raise


def _process_shipment(master, configs, stores, summary, batch_id, sync_mode):
	shipment_id = str(summary.get("shipmentId") or "").strip()
	if not shipment_id:
		return {"shipments": 0, "items": 0, "orders": 0, "history": 0}
	request_store = stores[0]
	detail = get_inbound_shipment(request_store, shipment_id)
	config, store = _country_target(detail, configs, stores)
	order_payload = _get_order_safely(request_store, detail.get("orderId") or summary.get("orderId"), summary)
	order_name, package_products, order_changed = _save_order(master, config, store, order_payload, shipment_id)
	shipment_name, old_shipment, shipment_changed = _save_shipment(master, config, store, detail, order_name, batch_id)
	before_history = frappe.db.count(HISTORY_DOCTYPE, {"sync_batch_id": batch_id})
	item_count = _save_items(master, config, store, shipment_name, order_name, detail, package_products, batch_id, sync_mode)
	if shipment_changed and not (detail.get("shipmentSkuQuantities") or []):
		_save_history(
			master=master, config=config, store=store, shipment_name=shipment_name,
			item_name=None, order_name=order_name, old=old_shipment, batch_id=batch_id, sync_mode=sync_mode,
			payload={
				"shipment_status": detail.get("shipmentStatus"), "amazon_updated_at": frappe_datetime(detail.get("updatedAt")),
				"expected_quantity": 0, "received_quantity": 0, "raw_payload": detail,
			},
		)
		frappe.db.commit()
	after_history = frappe.db.count(HISTORY_DOCTYPE, {"sync_batch_id": batch_id})
	return {
		"shipments": 1, "items": item_count, "orders": 1 if order_name and order_changed else 0,
		"history": max(after_history - before_history, 0),
	}


def _range(master, mode, days):
	now_utc = datetime.now(timezone.utc)
	if mode == "history":
		if not master.history_start_datetime or not master.history_end_datetime:
			frappe.throw(_("请先填写AWD入库货件历史开始时间和结束时间。"))
		return _to_utc(master.history_start_datetime), _to_utc(master.history_end_datetime)
	if mode == "recheck":
		if cint(days) not in RECHECK_DAYS:
			frappe.throw(_("不支持的AWD入库货件核对周期。"))
		return now_utc - timedelta(days=cint(days)), now_utc
	return now_utc - timedelta(hours=max(cint(master.incremental_lookback_hours), 1)), now_utc


def _segments(master, mode, start, end):
	if mode != "history":
		return [(start, end)]
	days = max(cint(master.history_segment_days), 1)
	segments = []
	cursor = start
	while cursor < end:
		segment_end = min(cursor + timedelta(days=days), end)
		segments.append((cursor, segment_end))
		cursor = segment_end
	return segments


def _delete_expired_history(master):
	days = cint(master.record_retention_days)
	if days <= 0:
		return 0
	cutoff = now_datetime() - timedelta(days=days)
	names = frappe.get_all(HISTORY_DOCTYPE, filters={"master_configuration": master.name, "observed_at": ["<", cutoff]}, pluck="name", limit_page_length=0)
	if names:
		frappe.db.delete(HISTORY_DOCTYPE, {"name": ["in", names]})
		frappe.db.commit()
	return len(names)


def _next_recheck(now, interval_days):
	return get_datetime(now).replace(hour=3, minute=17, second=0, microsecond=0) + timedelta(days=max(cint(interval_days), 1))


def _first_recheck(now):
	now = get_datetime(now)
	candidate = now.replace(hour=3, minute=17, second=0, microsecond=0)
	return candidate if candidate > now else candidate + timedelta(days=1)


def _is_quota(exc):
	return (isinstance(exc, AmazonAPIError) and exc.status_code == 429) or "http 429" in str(exc).lower()


def execute_inbound_sync(master_name, mode="incremental", days=0):
	master, configs, stores = _enabled_group(master_name)
	mode = str(mode or "incremental").strip().lower()
	if mode not in {"history", "incremental", "recheck"}:
		frappe.throw(_("不支持的AWD入库货件同步类型。"))
	start, end = _range(master, mode, days)
	lock = _task_lock(master.name)
	if not lock.acquire(blocking=False):
		return {"status": "busy", "message": "AWD inbound shipment task is already running"}
	started_at = now_datetime()
	batch_id = f"awd-inbound-{frappe.generate_hash(length=12)}"
	try:
		for config in configs:
			_update_configuration(
				config.name, current_status="Running", current_execution_type=mode,
				started_at=started_at, last_error="", history_status="Running" if mode == "history" else config.history_status,
			)
		stats = {
			"batch_id": batch_id, "mode": mode, "segments": 0, "listed": 0,
			"orders": 0, "shipments": 0, "items": 0, "history": 0, "skipped": 0,
			"range_start": start.isoformat(), "range_end": end.isoformat(),
		}
		seen = set()
		for segment_start, segment_end in _segments(master, mode, start, end):
			stats["segments"] += 1
			for shipments, _next_token in iter_inbound_shipment_pages(
				stores[0], updated_after=segment_start, updated_before=segment_end,
			):
				for summary in shipments:
					shipment_id = str(summary.get("shipmentId") or "").strip()
					if not shipment_id or shipment_id in seen:
						stats["skipped"] += 1
						continue
					seen.add(shipment_id)
					stats["listed"] += 1
					result = _process_shipment(master, configs, stores, summary, batch_id, mode)
					for key in ("orders", "shipments", "items", "history"):
						stats[key] += result[key]
		finished_at = now_datetime()
		stats["expired_history_deleted"] = _delete_expired_history(master)
		for config in configs:
			values = {
				"current_status": "Completed", "completed_at": finished_at,
				"last_success_at": finished_at, "last_sync_result": _json(stats), "last_error": "",
			}
			if mode == "history":
				values.update({
					"history_status": "Completed", "history_completed": 1,
					"history_checkpoint": end.replace(tzinfo=None), "history_last_error": "",
					"incremental_next_at": finished_at + timedelta(minutes=max(cint(master.sync_interval_minutes), 5)),
				})
			elif mode == "incremental":
				values.update({
					"incremental_last_at": finished_at, "incremental_checkpoint": end.replace(tzinfo=None),
					"incremental_next_at": finished_at + timedelta(minutes=max(cint(master.sync_interval_minutes), 5)),
					"incremental_summary": _json(stats),
				})
			else:
				for covered in RECHECK_DAYS:
					if covered > cint(days) or not cint(master.get(f"enable_recheck_{covered}")):
						continue
					values[f"recheck_{covered}_last_at"] = finished_at
					values[f"recheck_{covered}_next_at"] = _next_recheck(finished_at, master.get(f"recheck_{covered}_interval_days"))
			_update_configuration(config.name, **values)
		return {"status": "success", "summary": stats}
	except Exception as exc:
		retry_at = now_datetime() + timedelta(minutes=QUOTA_RETRY_MINUTES if _is_quota(exc) else 30)
		for config in configs:
			values = {
				"current_status": "Waiting" if _is_quota(exc) else "Failed", "completed_at": now_datetime(),
				"last_error": str(exc)[:2000], "incremental_next_at": retry_at,
			}
			if mode == "history":
				values.update({"history_status": "Waiting" if _is_quota(exc) else "Failed", "history_last_error": str(exc)[:2000]})
			if mode == "recheck":
				values[f"recheck_{cint(days)}_next_at"] = retry_at
			_update_configuration(config.name, **values)
		frappe.logger("amazon_awd_inbound", allow_site=True).exception(
			"AWD inbound shipment sync failed: master=%s mode=%s", master.name, mode,
		)
		if _is_quota(exc):
			return {"status": "waiting", "retry_at": retry_at, "message": str(exc)}
		raise
	finally:
		try:
			lock.release()
		except Exception:
			pass


@frappe.whitelist()
def start_group_sync(master_name, mode="history", days=0):
	master, configs, _stores = _enabled_group(master_name)
	master.check_permission("write")
	for config in configs:
		config.check_permission("write")
	mode = str(mode or "history").strip().lower()
	_range(master, mode, days)
	for config in configs:
		values = {"current_status": "Waiting", "current_execution_type": mode, "last_error": ""}
		if mode == "history":
			values.update({"history_status": "Waiting", "history_completed": 0, "history_checkpoint": None, "history_last_error": ""})
		_update_configuration(config.name, **values)
	_enqueue(master.name, mode, days)
	return {"status": "queued", "master_name": master.name, "mode": mode, "days": cint(days), "message": _("AWD入库货件任务已进入后台队列。")}


def start_country_sync(name, mode, days=0):
	config = _configuration(name)
	config.check_permission("write")
	if not config.master_configuration:
		frappe.throw(_("请先选择AWD入库货件总配置。"))
	return start_group_sync(config.master_configuration, mode, days)


def _run_scheduled_master(master_name, now):
	master, configs, _stores = _enabled_group(master_name)
	for config in configs:
		if config.current_status != "Running" or not config.started_at:
			continue
		if (now - get_datetime(config.started_at)).total_seconds() <= TASK_TIMEOUT:
			return
		_update_configuration(config.name, current_status="Failed", last_error="上一次AWD入库货件任务超时，已由调度器释放。")
	master, configs, _stores = _enabled_group(master_name)
	if not all(cint(config.history_completed) for config in configs):
		_enqueue(master.name, "history")
		return
	initialized = False
	for config in configs:
		values = {}
		for days in RECHECK_DAYS:
			if cint(master.get(f"enable_recheck_{days}")) and not config.get(f"recheck_{days}_next_at"):
				values[f"recheck_{days}_next_at"] = _first_recheck(now)
		if values:
			initialized = True
			_update_configuration(config.name, **values)
	if initialized:
		return
	for days in reversed(RECHECK_DAYS):
		if not cint(master.get(f"enable_recheck_{days}")):
			continue
		if any(config.get(f"recheck_{days}_next_at") and get_datetime(config.get(f"recheck_{days}_next_at")) <= now for config in configs):
			_enqueue(master.name, "recheck", days)
			return
	next_times = [get_datetime(config.incremental_next_at) for config in configs if config.incremental_next_at]
	if len(next_times) == len(configs) and min(next_times) > now:
		return
	_enqueue(master.name, "incremental")


def run_scheduled_inbound_sync():
	now = now_datetime()
	master_names = frappe.get_all(MASTER_DOCTYPE, filters={"enabled": 1}, pluck="name", order_by="name asc")
	for master_name in master_names:
		try:
			_run_scheduled_master(master_name, now)
		except Exception:
			frappe.logger("amazon_awd_inbound", allow_site=True).exception(
				"AWD inbound shipment scheduler failed: master=%s", master_name,
			)
