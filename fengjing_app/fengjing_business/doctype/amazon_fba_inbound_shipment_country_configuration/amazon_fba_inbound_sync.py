# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""FBA入库计划、货件、商品和变化历史的分阶段同步。"""

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

from .amazon_fba_inbound_api import (
	amazon_datetime,
	frappe_datetime,
	get_inbound_plan,
	get_shipment,
	iter_inbound_plan_pages,
	iter_received_item_pages,
	iter_shipment_item_pages,
)


CONFIG_DOCTYPE = "Amazon FBA Inbound Shipment Country Configuration"
MASTER_DOCTYPE = "Amazon FBA Inbound Shipment Master Configuration"
PLAN_DOCTYPE = "Amazon FBA Inbound Plan"
SHIPMENT_DOCTYPE = "Amazon FBA Inbound Shipment"
ITEM_DOCTYPE = "Amazon FBA Inbound Shipment Item"
HISTORY_DOCTYPE = "Amazon FBA Inbound Shipment History"
RECHECK_DAYS = (7, 14, 30, 90, 180)
TASK_TIMEOUT = 6 * 60 * 60
QUOTA_RETRY_MINUTES = 10

COUNTRY_MARKETPLACES = {
	"US": "ATVPDKIKX0DER", "CA": "A2EUQ1WTGCTBG2", "MX": "A1AM78C64UM0Y8",
	"BR": "A2Q3Y263D00KWC", "CL": "A2ZV50J4W1RKNI", "IE": "A28R8C7NBKEWEA",
	"ES": "A1RKKUPIHCS9HS", "GB": "A1F83G8C2ARO7P", "FR": "A13V1IB3VIYZZH",
	"BE": "AMEN7PMS3EDWL", "NL": "A1805IZSGTT6HS", "DE": "A1PA6795UKMFR9",
	"IT": "APJ6JRA9NG5V4", "SE": "A2NODRKZP88ZB9", "ZA": "AE08WJ6YKNBMC",
	"PL": "A1C3SOZRARQ6R3", "EG": "ARBP9OOSHTCHU", "TR": "A33AVAJ2PDY3EV",
	"SA": "A17E79C6D8DWNP", "AE": "A2VIGQ35RCS4UG", "IN": "A21TJRUUN4KGV",
	"SG": "A19VAU5U5O7RUS", "AU": "A39IBJ37TRP1C6", "JP": "A1VC38T7YXB528",
}


def _json(value):
	return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value):
	return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _to_utc(value):
	if not value:
		return None
	dt = get_datetime(value)
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=ZoneInfo(get_system_timezone()))
	return dt.astimezone(timezone.utc)


def _configuration(name):
	ensure_database_connection()
	return frappe.get_doc(CONFIG_DOCTYPE, name)


def _master(name):
	ensure_database_connection()
	return frappe.get_doc(MASTER_DOCTYPE, name)


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
		frappe.throw(_("入库货件总配置尚未启用。"))
	names = frappe.get_all(
		CONFIG_DOCTYPE,
		filters={"master_configuration": master.name, "enabled": 1},
		pluck="name",
		order_by="name asc",
	)
	configs = [_configuration(name) for name in names]
	if not configs:
		frappe.throw(_("当前总配置没有已启用的入库货件国家配置。"))
	stores = [get_store(config.amazon_store) for config in configs]
	seller_ids = {str(store.seller_id or "").strip().upper() for store in stores}
	regions = {get_region(store) for store in stores}
	marketplaces = {str(store.marketplace_id or "").strip().upper() for store in stores}
	if "" in seller_ids or len(seller_ids) != 1:
		frappe.throw(_("同一个入库货件总配置只能关联同一个Amazon卖家。"))
	if len(regions) != 1 or str(master.api_region or "") not in regions:
		frappe.throw(_("入库货件总配置与国家配置的API区域不一致。"))
	if len(marketplaces) != len(stores):
		frappe.throw(_("同一个入库货件总配置中存在重复的Marketplace ID。"))
	return master, configs, stores


def _task_lock(master_name):
	key = hashlib.sha256(str(master_name).encode("utf-8")).hexdigest()[:24]
	return frappe.cache().lock(
		frappe.cache().make_key(f"fengjing:amazon-fba-inbound:{key}"),
		timeout=TASK_TIMEOUT,
		blocking_timeout=0,
	)


def _enqueue(master_name, mode, days=0):
	days = cint(days)
	identity = hashlib.sha256(str(master_name).encode("utf-8")).hexdigest()[:16]
	frappe.enqueue(
		execute_inbound_sync,
		queue="long",
		timeout=TASK_TIMEOUT,
		enqueue_after_commit=True,
		job_id=f"amazon-fba-inbound-{identity}-{mode}-{days}",
		deduplicate=True,
		master_name=master_name,
		mode=mode,
		days=days,
	)


def _pick_option(options, id_field):
	options = options or []
	selected = next((row for row in options if str(row.get("status") or "").upper() == "ACCEPTED"), None)
	selected = selected or (options[0] if options else {})
	return selected.get(id_field)


def _source_values(source):
	source = source or {}
	return {
		"source_name": source.get("name"),
		"source_company_name": source.get("companyName"),
		"source_phone_number": source.get("phoneNumber"),
		"source_address_line_1": source.get("addressLine1"),
		"source_address_line_2": source.get("addressLine2"),
		"source_city": source.get("city"),
		"source_state_or_province": source.get("stateOrProvinceCode"),
		"source_postal_code": source.get("postalCode"),
		"source_country_code": source.get("countryCode"),
	}


def _upsert(doctype, key_field, key, values):
	name = frappe.db.get_value(doctype, {key_field: key}, "name")
	values = {**values, key_field: key}
	if name:
		frappe.db.set_value(doctype, name, values, update_modified=False)
		return name, False
	doc = frappe.get_doc({"doctype": doctype, **values})
	doc.insert(ignore_permissions=True)
	return doc.name, True


def _store_plan(master, store, payload, *, stage="Listed", status="Running"):
	plan_id = str(payload.get("inboundPlanId") or "").strip()
	if not plan_id:
		raise ValueError("Amazon入库计划缺少inboundPlanId。")
	existing = frappe.db.get_value(
		PLAN_DOCTYPE,
		plan_id,
		["plan_detail_completed", "amazon_updated_at", "raw_json"],
		as_dict=True,
	)
	remote_updated = frappe_datetime(payload.get("lastUpdatedAt"))
	preserve_detail = bool(
		existing
		and cint(existing.plan_detail_completed)
		and existing.raw_json
		and existing.amazon_updated_at
		and remote_updated
		and get_datetime(existing.amazon_updated_at) >= get_datetime(remote_updated)
		and "shipments" not in payload
	)
	values = {
		"plan_name": payload.get("name"),
		"plan_status": payload.get("status"),
		"master_configuration": master.name,
		"seller_id": store.seller_id,
		"api_region": get_region(store),
		"marketplace_ids_json": _json(payload.get("marketplaceIds") or []),
		"amazon_created_at": frappe_datetime(payload.get("createdAt")),
		"amazon_updated_at": remote_updated,
		"last_fetched_at": now_datetime(),
		**_source_values(payload.get("sourceAddress")),
	}
	if not preserve_detail:
		values.update({
			"sync_stage": stage,
			"sync_status": status,
			"raw_json_hash": _hash(payload),
			"raw_json": _json(payload),
		})
		if "shipments" not in payload:
			values.update({"plan_detail_completed": 0, "shipment_discovery_completed": 0})
	if not existing:
		values["first_fetched_at"] = now_datetime()
	name, created = _upsert(PLAN_DOCTYPE, "inbound_plan_id", plan_id, values)
	frappe.db.commit()
	return name, created


def _country_target(plan_marketplaces, destination_country, configs, stores):
	by_marketplace = {
		str(store.marketplace_id or "").strip().upper(): (config, store)
		for config, store in zip(configs, stores)
	}
	candidates = [by_marketplace[mid] for mid in plan_marketplaces if mid in by_marketplace]
	destination_marketplace = COUNTRY_MARKETPLACES.get(str(destination_country or "").upper())
	if destination_marketplace and destination_marketplace in by_marketplace:
		candidate = by_marketplace[destination_marketplace]
		if not candidates or candidate in candidates:
			return candidate
	if len(candidates) == 1:
		return candidates[0]
	all_targets = candidates or list(zip(configs, stores))
	if len(all_targets) == 1:
		return all_targets[0]
	raise ValueError("Amazon入库货件缺少可唯一确定国家配置的目的地信息。")


def _tracking_number(tracking):
	tracking = tracking or {}
	spd = (tracking.get("spdTrackingDetail") or {}).get("spdTrackingItems") or []
	if spd:
		return spd[0].get("trackingId")
	ltl = tracking.get("ltlTrackingDetail") or {}
	return ltl.get("billOfLadingNumber") or ((ltl.get("freightBillNumber") or [None])[0])


def _history(values):
	observed_at = now_datetime()
	key_payload = {**values, "observed_at": observed_at.isoformat(timespec="microseconds")}
	history_key = _hash(key_payload)
	frappe.get_doc({
		"doctype": HISTORY_DOCTYPE,
		"history_key": history_key,
		"observed_at": observed_at,
		**values,
	}).insert(ignore_permissions=True)


def _store_shipment(master, configs, stores, plan, payload):
	shipment_id = str(payload.get("shipmentId") or "").strip()
	if not shipment_id:
		raise ValueError("Amazon入库货件缺少shipmentId。")
	destination = payload.get("destination") or {}
	address = destination.get("address") or {}
	marketplaces = json.loads(plan.marketplace_ids_json or "[]")
	config, store = _country_target(marketplaces, address.get("countryCode"), configs, stores)
	old = frappe.db.get_value(
		SHIPMENT_DOCTYPE,
		shipment_id,
		["shipment_status", "planned_quantity", "shipped_quantity", "received_quantity", "in_transit_quantity", "quantity_difference"],
		as_dict=True,
	)
	delivery = payload.get("selectedDeliveryWindow") or {}
	tracking = payload.get("trackingDetails") or {}
	values = {
		"shipment_confirmation_id": payload.get("shipmentConfirmationId"),
		"shipment_name": payload.get("name") or payload.get("shipmentConfirmationId") or shipment_id,
		"shipment_status": payload.get("status"),
		"inbound_plan": plan.name,
		"master_configuration": master.name,
		"country_configuration": config.name,
		"amazon_store": store.name,
		"marketplace_id": store.marketplace_id,
		"destination_type": destination.get("destinationType"),
		"fulfillment_center_id": destination.get("warehouseId"),
		"ship_to_name": address.get("name"),
		"ship_to_address_line_1": address.get("addressLine1"),
		"ship_to_address_line_2": address.get("addressLine2"),
		"ship_to_city": address.get("city"),
		"ship_to_state_or_province": address.get("stateOrProvinceCode"),
		"ship_to_postal_code": address.get("postalCode"),
		"ship_to_country_code": address.get("countryCode"),
		"transportation_option_id": payload.get("selectedTransportationOptionId"),
		"tracking_number": _tracking_number(tracking),
		"tracking_details_json": _json(tracking),
		"delivery_window_start": frappe_datetime(delivery.get("startDate")),
		"delivery_window_end": frappe_datetime(delivery.get("endDate")),
		"amazon_created_at": plan.amazon_created_at,
		"amazon_updated_at": plan.amazon_updated_at,
		"last_fetched_at": now_datetime(),
		"sync_stage": "Shipment Detail",
		"sync_status": "Running",
		"shipment_detail_completed": 1,
		"last_sync_at": now_datetime(),
		"last_error": "",
		"raw_json_hash": _hash(payload),
		"raw_json": _json(payload),
	}
	if not old:
		values["first_fetched_at"] = now_datetime()
	name, created = _upsert(SHIPMENT_DOCTYPE, "shipment_id", shipment_id, values)
	if created or str(old.shipment_status or "") != str(payload.get("status") or ""):
		_history({
			"inbound_shipment": name,
			"inbound_plan": plan.name,
			"master_configuration": master.name,
			"country_configuration": config.name,
			"amazon_store": store.name,
			"marketplace_id": store.marketplace_id,
			"change_type": "SHIPMENT_CREATED" if created else "STATUS_CHANGED",
			"previous_status": old.shipment_status if old else None,
			"current_status": payload.get("status"),
			"event_source": "getShipment",
			"raw_json_hash": _hash(payload),
			"raw_json": _json(payload),
		})
	frappe.db.commit()
	return frappe.get_doc(SHIPMENT_DOCTYPE, name), config, store


def _item_mapping(store, msku, asin=None):
	from fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log import (
		获取平台映射物料,
	)

	item_code = 获取平台映射物料(store.cost_center, store.marketplace_id, asin=asin, sku=msku)
	item_name = frappe.db.get_value("Item", item_code, "item_name") if item_code else None
	return item_code, item_name


def _item_key(shipment_id, payload):
	identity = {
		"shipment_id": shipment_id,
		"msku": payload.get("msku"),
		"fnsku": payload.get("fnsku"),
		"expiration": payload.get("expiration"),
		"manufacturingLotCode": payload.get("manufacturingLotCode"),
	}
	return _hash(identity)


def _store_item(master, plan, shipment, config, store, payload, batch_id, sync_mode):
	key = _item_key(shipment.shipment_id, payload)
	old = frappe.db.get_value(
		ITEM_DOCTYPE,
		{"external_key": key},
		["name", "planned_quantity", "shipped_quantity", "received_quantity", "in_transit_quantity", "quantity_difference"],
		as_dict=True,
	)
	item_code, item_name = _item_mapping(store, payload.get("msku"), payload.get("asin"))
	planned = flt(payload.get("quantity"))
	values = {
		"inbound_shipment": shipment.name,
		"inbound_plan": plan.name,
		"master_configuration": master.name,
		"country_configuration": config.name,
		"amazon_store": store.name,
		"marketplace_id": store.marketplace_id,
		"msku": payload.get("msku"),
		"asin": payload.get("asin"),
		"fnsku": payload.get("fnsku"),
		"item_code": item_code,
		"item_name": item_name,
		"packing_group_id": payload.get("packingGroupId"),
		"label_owner": payload.get("labelOwner"),
		"prep_owner": payload.get("prepOwner"),
		"expiration_date": payload.get("expiration"),
		"manufacturing_lot_code": payload.get("manufacturingLotCode"),
		"prep_instructions_json": _json(payload.get("prepInstructions") or []),
		"planned_quantity": planned,
		"quantity_updated_at": now_datetime(),
		"sync_batch_id": batch_id,
		"last_fetched_at": now_datetime(),
		"raw_json_hash": _hash(payload),
		"raw_json": _json(payload),
	}
	if not old:
		values["first_fetched_at"] = now_datetime()
	name, created = _upsert(ITEM_DOCTYPE, "external_key", key, values)
	previous_planned = flt(old.planned_quantity) if old else 0
	if created or previous_planned != planned:
		_history({
			"inbound_shipment": shipment.name,
			"inbound_shipment_item": name,
			"inbound_plan": plan.name,
			"master_configuration": master.name,
			"country_configuration": config.name,
			"amazon_store": store.name,
			"marketplace_id": store.marketplace_id,
			"change_type": "ITEM_CREATED" if created else "QUANTITY_CHANGED",
			"current_status": shipment.shipment_status,
			"event_source": "listShipmentItems",
			"sync_mode": sync_mode,
			"sync_batch_id": batch_id,
			"msku": payload.get("msku"),
			"asin": payload.get("asin"),
			"fnsku": payload.get("fnsku"),
			"item_code": item_code,
			"item_name": item_name,
			"previous_planned_quantity": previous_planned,
			"previous_shipped_quantity": old.shipped_quantity if old else None,
			"previous_received_quantity": old.received_quantity if old else None,
			"previous_in_transit_quantity": old.in_transit_quantity if old else None,
			"previous_difference_quantity": old.quantity_difference if old else None,
			"current_planned_quantity": planned,
			"current_shipped_quantity": old.shipped_quantity if old else None,
			"current_received_quantity": old.received_quantity if old else None,
			"current_in_transit_quantity": old.in_transit_quantity if old else None,
			"current_difference_quantity": old.quantity_difference if old else None,
			"planned_quantity_change": planned - previous_planned,
			"raw_json_hash": _hash(payload),
			"raw_json": _json(payload),
		})
	return name


def _sync_shipment_items(master, plan, shipment, config, store, batch_id, sync_mode):
	resume_token = shipment.items_pagination_token or None
	try:
		pages = iter_shipment_item_pages(store, plan.inbound_plan_id, shipment.shipment_id, resume_token=resume_token)
		for items, next_token in pages:
			for item in items:
				_store_item(master, plan, shipment, config, store, item, batch_id, sync_mode)
			frappe.db.set_value(
				SHIPMENT_DOCTYPE,
				shipment.name,
				{"items_pagination_token": next_token or "", "sync_stage": "Shipment Items"},
				update_modified=False,
			)
			frappe.db.commit()
	except AmazonAPIError as exc:
		if resume_token and exc.status_code == 400:
			frappe.db.set_value(SHIPMENT_DOCTYPE, shipment.name, "items_pagination_token", "", update_modified=False)
			frappe.db.commit()
			return _sync_shipment_items(master, plan, frappe.get_doc(SHIPMENT_DOCTYPE, shipment.name), config, store, batch_id, sync_mode)
		_mark_shipment_failed(shipment.name, exc)
		raise
	except Exception as exc:
		_mark_shipment_failed(shipment.name, exc)
		raise
	received_rows = 0
	if shipment.shipment_confirmation_id:
		try:
			for legacy_items, _next_token in iter_received_item_pages(store, shipment.shipment_confirmation_id):
				for legacy_item in legacy_items:
					received_rows += _apply_received_item(
						master, plan, shipment, config, store, legacy_item, batch_id, sync_mode
					)
		except Exception as exc:
			# 新版接口本身不提供收货数量。旧只读接口不可用时保留空值，不能把缺失值写成0。
			frappe.logger("amazon_fba_inbound", allow_site=True).warning(
				"Amazon received-quantity enrichment was unavailable for shipment %s: %s",
				shipment.shipment_confirmation_id,
				exc,
			)
	items = frappe.get_all(
		ITEM_DOCTYPE,
		filters={"inbound_shipment": shipment.name},
		fields=["planned_quantity", "shipped_quantity", "received_quantity", "in_transit_quantity", "quantity_difference"],
	)
	planned = sum(flt(row.planned_quantity) for row in items)
	quantity_values = {"planned_quantity": planned}
	if received_rows:
		quantity_values.update({
			"shipped_quantity": sum(flt(row.shipped_quantity) for row in items),
			"received_quantity": sum(flt(row.received_quantity) for row in items),
			"in_transit_quantity": sum(flt(row.in_transit_quantity) for row in items),
			"quantity_difference": sum(flt(row.quantity_difference) for row in items),
		})
	frappe.db.set_value(
		SHIPMENT_DOCTYPE,
		shipment.name,
		{
			**quantity_values,
			"shipment_items_completed": 1,
			"items_pagination_token": "",
			"sync_stage": "Completed",
			"sync_status": "Completed",
			"last_error": "",
		},
		update_modified=False,
	)
	frappe.db.commit()
	return len(items)


def _apply_received_item(master, plan, shipment, config, store, payload, batch_id, sync_mode):
	msku = str(payload.get("SellerSKU") or "").strip()
	fnsku = str(payload.get("FulfillmentNetworkSKU") or "").strip()
	filters = {"inbound_shipment": shipment.name, "msku": msku}
	if fnsku:
		filters["fnsku"] = fnsku
	names = frappe.get_all(ITEM_DOCTYPE, filters=filters, pluck="name", limit_page_length=2)
	if len(names) != 1:
		return 0
	item = frappe.get_doc(ITEM_DOCTYPE, names[0])
	shipped = flt(payload.get("QuantityShipped"))
	received = flt(payload.get("QuantityReceived"))
	in_transit = max(shipped - received, 0)
	difference = shipped - received
	old_values = {
		"shipped": flt(item.shipped_quantity),
		"received": flt(item.received_quantity),
		"in_transit": flt(item.in_transit_quantity),
		"difference": flt(item.quantity_difference),
	}
	new_values = {
		"shipped_quantity": shipped,
		"received_quantity": received,
		"in_transit_quantity": in_transit,
		"quantity_difference": difference,
		"quantity_updated_at": now_datetime(),
		"sync_batch_id": batch_id,
	}
	frappe.db.set_value(ITEM_DOCTYPE, item.name, new_values, update_modified=False)
	if old_values != {"shipped": shipped, "received": received, "in_transit": in_transit, "difference": difference}:
		_history({
			"inbound_shipment": shipment.name,
			"inbound_shipment_item": item.name,
			"inbound_plan": plan.name,
			"master_configuration": master.name,
			"country_configuration": config.name,
			"amazon_store": store.name,
			"marketplace_id": store.marketplace_id,
			"change_type": "RECEIPT_QUANTITY_CHANGED",
			"current_status": shipment.shipment_status,
			"event_source": "getShipmentItemsByShipmentId",
			"sync_mode": sync_mode,
			"sync_batch_id": batch_id,
			"msku": item.msku,
			"asin": item.asin,
			"fnsku": item.fnsku,
			"item_code": item.item_code,
			"item_name": item.item_name,
			"previous_planned_quantity": item.planned_quantity,
			"previous_shipped_quantity": old_values["shipped"],
			"previous_received_quantity": old_values["received"],
			"previous_in_transit_quantity": old_values["in_transit"],
			"previous_difference_quantity": old_values["difference"],
			"current_planned_quantity": item.planned_quantity,
			"current_shipped_quantity": shipped,
			"current_received_quantity": received,
			"current_in_transit_quantity": in_transit,
			"current_difference_quantity": difference,
			"shipped_quantity_change": shipped - old_values["shipped"],
			"received_quantity_change": received - old_values["received"],
			"in_transit_quantity_change": in_transit - old_values["in_transit"],
			"difference_quantity_change": difference - old_values["difference"],
			"raw_json_hash": _hash(payload),
			"raw_json": _json(payload),
		})
	frappe.db.commit()
	return 1


def _mark_shipment_failed(shipment_name, exc):
	retry_count = cint(frappe.db.get_value(SHIPMENT_DOCTYPE, shipment_name, "retry_count")) + 1
	frappe.db.set_value(
		SHIPMENT_DOCTYPE,
		shipment_name,
		{
			"sync_status": "Failed",
			"retry_count": retry_count,
			"next_retry_at": now_datetime() + timedelta(minutes=min(5 * retry_count, 60)),
			"last_error": str(exc)[:4000],
		},
		update_modified=False,
	)
	frappe.db.commit()


def _process_plan(master, configs, stores, summary, batch_id, sync_mode):
	store = stores[0]
	plan_name, _created = _store_plan(master, store, summary)
	try:
		plan = frappe.get_doc(PLAN_DOCTYPE, plan_name)
		remote_updated = frappe_datetime(summary.get("lastUpdatedAt"))
		can_resume_detail = bool(
			cint(plan.plan_detail_completed)
			and plan.raw_json
			and plan.amazon_updated_at
			and remote_updated
			and get_datetime(plan.amazon_updated_at) >= get_datetime(remote_updated)
		)
		if can_resume_detail:
			plan_payload = json.loads(plan.raw_json)
		else:
			detail = get_inbound_plan(store, summary.get("inboundPlanId"))
			plan_payload = detail.get("inboundPlan") if isinstance(detail.get("inboundPlan"), dict) else detail
			plan_name, _created = _store_plan(master, store, plan_payload, stage="Shipment Discovery")
		shipments = plan_payload.get("shipments") or []
		frappe.db.set_value(
			PLAN_DOCTYPE,
			plan_name,
			{
				"packing_option_id": _pick_option(plan_payload.get("packingOptions"), "packingOptionId"),
				"placement_option_id": _pick_option(plan_payload.get("placementOptions"), "placementOptionId"),
				"shipment_count": len(shipments),
				"plan_detail_completed": 1,
				"shipment_discovery_completed": 1,
			},
			update_modified=False,
		)
		frappe.db.commit()
		plan = frappe.get_doc(PLAN_DOCTYPE, plan_name)
		item_count = 0
		for shipment_summary in shipments:
			shipment_id = shipment_summary.get("shipmentId")
			if not shipment_id:
				continue
			existing_shipment = frappe.db.get_value(
				SHIPMENT_DOCTYPE,
				shipment_id,
				["shipment_detail_completed", "amazon_updated_at", "raw_json"],
				as_dict=True,
			)
			can_resume_shipment = bool(
				existing_shipment
				and cint(existing_shipment.shipment_detail_completed)
				and existing_shipment.raw_json
				and existing_shipment.amazon_updated_at
				and plan.amazon_updated_at
				and get_datetime(existing_shipment.amazon_updated_at) >= get_datetime(plan.amazon_updated_at)
			)
			if can_resume_shipment:
				shipment_payload = json.loads(existing_shipment.raw_json)
			else:
				shipment_payload = get_shipment(store, plan.inbound_plan_id, shipment_id)
				shipment_payload = shipment_payload.get("shipment") if isinstance(shipment_payload.get("shipment"), dict) else shipment_payload
			shipment, config, shipment_store = _store_shipment(master, configs, stores, plan, shipment_payload)
			item_count += _sync_shipment_items(master, plan, shipment, config, shipment_store, batch_id, sync_mode)
		frappe.db.set_value(
			PLAN_DOCTYPE,
			plan.name,
			{"sync_stage": "Completed", "sync_status": "Completed", "last_error": "", "retry_count": 0},
			update_modified=False,
		)
		frappe.db.commit()
		return {"shipments": len(shipments), "items": item_count}
	except Exception as exc:
		retry_count = cint(frappe.db.get_value(PLAN_DOCTYPE, plan_name, "retry_count")) + 1
		frappe.db.set_value(
			PLAN_DOCTYPE,
			plan_name,
			{
				"sync_status": "Failed",
				"sync_stage": "Failed",
				"retry_count": retry_count,
				"next_retry_at": now_datetime() + timedelta(minutes=min(5 * retry_count, 60)),
				"last_error": str(exc)[:4000],
			},
			update_modified=False,
		)
		frappe.db.commit()
		raise


def _range(master, mode, days):
	now_utc = datetime.now(timezone.utc)
	if mode == "history":
		if not master.history_start_datetime or not master.history_end_datetime:
			frappe.throw(_("请先填写入库货件历史开始时间和结束时间。"))
		return _to_utc(master.history_start_datetime), _to_utc(master.history_end_datetime), "CREATION_TIME"
	if mode == "recheck":
		if cint(days) not in RECHECK_DAYS:
			frappe.throw(_("不支持的入库货件核对周期。"))
		return now_utc - timedelta(days=cint(days)), now_utc, "LAST_UPDATED_TIME"
	lookback = max(cint(master.incremental_lookback_hours), 1)
	return now_utc - timedelta(hours=lookback), now_utc, "LAST_UPDATED_TIME"


def _plan_time(plan, sort_by):
	field = "createdAt" if sort_by == "CREATION_TIME" else "lastUpdatedAt"
	return amazon_datetime(plan.get(field))


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
	return get_datetime(now).replace(hour=2, minute=47, second=0, microsecond=0) + timedelta(days=max(cint(interval_days), 1))


def _first_recheck(now):
	now = get_datetime(now)
	candidate = now.replace(hour=2, minute=47, second=0, microsecond=0)
	return candidate if candidate > now else candidate + timedelta(days=1)


def _is_quota(exc):
	return (isinstance(exc, AmazonAPIError) and exc.status_code == 429) or "http 429" in str(exc).lower()


def execute_inbound_sync(master_name, mode="incremental", days=0):
	master, configs, stores = _enabled_group(master_name)
	mode = str(mode or "incremental").strip().lower()
	if mode not in {"history", "incremental", "recheck"}:
		frappe.throw(_("不支持的入库货件同步类型。"))
	start, end, sort_by = _range(master, mode, days)
	lock = _task_lock(master.name)
	if not lock.acquire(blocking=False):
		return {"status": "busy", "message": "FBA inbound shipment task is already running"}
	started_at = now_datetime()
	batch_id = f"fba-inbound-{frappe.generate_hash(length=12)}"
	try:
		for config in configs:
			_update_configuration(config.name, current_status="Running", current_execution_type=mode, started_at=started_at, last_error="", history_status="Running" if mode == "history" else config.history_status)
		stats = {"batch_id": batch_id, "mode": mode, "plans": 0, "shipments": 0, "items": 0, "skipped": 0, "range_start": start.isoformat(), "range_end": end.isoformat()}
		seen_plans = set()
		stop = False
		for plans, _next_token in iter_inbound_plan_pages(stores[0], sort_by=sort_by, sort_order="DESC"):
			for summary in plans:
				plan_id = str(summary.get("inboundPlanId") or "")
				if not plan_id or plan_id in seen_plans:
					continue
				seen_plans.add(plan_id)
				stamp = _plan_time(summary, sort_by)
				if not stamp:
					continue
				if stamp > end:
					continue
				if stamp < start:
					stop = True
					break
				if mode == "history":
					existing = frappe.db.get_value(PLAN_DOCTYPE, plan_id, ["sync_status", "amazon_updated_at"], as_dict=True)
					if existing and existing.sync_status == "Completed" and existing.amazon_updated_at:
						stored_updated = _to_utc(existing.amazon_updated_at)
						remote_updated = amazon_datetime(summary.get("lastUpdatedAt"))
						if remote_updated and stored_updated >= remote_updated:
							stats["skipped"] += 1
							continue
				result = _process_plan(master, configs, stores, summary, batch_id, mode)
				stats["plans"] += 1
				stats["shipments"] += result["shipments"]
				stats["items"] += result["items"]
			if stop:
				break
		finished_at = now_datetime()
		stats["expired_history_deleted"] = _delete_expired_history(master)
		for config in configs:
			values = {
				"current_status": "Completed",
				"completed_at": finished_at,
				"last_success_at": finished_at,
				"last_sync_result": _json(stats),
				"last_error": "",
			}
			if mode == "history":
				values.update({"history_status": "Completed", "history_completed": 1, "history_checkpoint": frappe_datetime(end), "history_last_error": "", "incremental_next_at": finished_at + timedelta(minutes=max(cint(master.sync_interval_minutes), 5))})
			elif mode == "incremental":
				values.update({"incremental_last_at": finished_at, "incremental_checkpoint": frappe_datetime(end), "incremental_next_at": finished_at + timedelta(minutes=max(cint(master.sync_interval_minutes), 5)), "incremental_summary": _json(stats)})
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
			values = {"current_status": "Waiting" if _is_quota(exc) else "Failed", "completed_at": now_datetime(), "last_error": str(exc)[:2000], "incremental_next_at": retry_at}
			if mode == "history":
				values.update({"history_status": "Waiting" if _is_quota(exc) else "Failed", "history_last_error": str(exc)[:2000]})
			if mode == "recheck":
				values[f"recheck_{cint(days)}_next_at"] = retry_at
			_update_configuration(config.name, **values)
		frappe.logger("amazon_fba_inbound", allow_site=True).exception("FBA inbound shipment sync failed: master=%s mode=%s", master.name, mode)
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
			values.update({
				"history_status": "Waiting",
				"history_completed": 0,
				"history_checkpoint": None,
				"history_last_error": "",
			})
		_update_configuration(config.name, **values)
	_enqueue(master.name, mode, days)
	return {"status": "queued", "master_name": master.name, "mode": mode, "days": cint(days), "message": _("FBA入库货件任务已进入后台队列。")}


def start_country_sync(name, mode, days=0):
	config = _configuration(name)
	config.check_permission("write")
	if not config.master_configuration:
		frappe.throw(_("请先选择入库货件总配置。"))
	return start_group_sync(config.master_configuration, mode, days)


def _run_scheduled_master(master_name, now):
	master, configs, _stores = _enabled_group(master_name)
	for config in configs:
		if config.current_status != "Running" or not config.started_at:
			continue
		if (now - get_datetime(config.started_at)).total_seconds() <= TASK_TIMEOUT:
			return
		_update_configuration(config.name, current_status="Failed", last_error="上一次入库货件任务超时，已由调度器释放。")
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
			frappe.logger("amazon_fba_inbound", allow_site=True).exception("FBA inbound shipment scheduler failed: master=%s", master_name)
