# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""FBA Inventory API 实时库存快照同步。"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, get_datetime, get_system_timezone, now_datetime

from fengjing_app.fengjing_business.doctype.amazon_store_configuration.amazon_store_configuration import (
	amazon_api_json,
	ensure_database_connection,
	get_store,
)


DOCTYPE = "Amazon FBA Inventory Configuration"
SNAPSHOT_DOCTYPE = "Amazon FBA Inventory Snapshot"
TASK_TIMEOUT = 2 * 60 * 60


class AmazonFBAInventoryConfiguration(Document):
	def validate(self):
		store = get_store(self.amazon_store, require_enabled=False) if self.amazon_store else None
		if store:
			self.cost_center = store.cost_center
			self.marketplace_id = store.marketplace_id
			self.api_region = store.api_region
		self.sync_interval_minutes = max(cint(self.sync_interval_minutes), 1)
		self.snapshot_retention_days = max(cint(self.snapshot_retention_days), 0)
		self.incremental_lookback_minutes = max(cint(self.incremental_lookback_minutes), 0)


def _configuration(name):
	ensure_database_connection()
	return frappe.get_doc(DOCTYPE, name)


def _update_configuration(name, **values):
	ensure_database_connection()
	valid = set(frappe.get_meta(DOCTYPE).get_valid_columns())
	values = {key: value for key, value in values.items() if key in valid}
	if values:
		frappe.db.set_value(DOCTYPE, name, values, update_modified=False)
		frappe.db.commit()


def _task_lock(name):
	return frappe.cache().lock(
		frappe.cache().make_key(f"fengjing:amazon-fba-inventory:{name}"),
		timeout=TASK_TIMEOUT,
		blocking_timeout=0,
	)


def _enqueue(name):
	frappe.enqueue(
		execute_inventory_sync,
		queue="long",
		timeout=TASK_TIMEOUT,
		enqueue_after_commit=True,
		job_id=f"amazon-fba-inventory-{name}",
		deduplicate=True,
		configuration_name=name,
	)


def _number(value):
	try:
		return int(float(value or 0))
	except (TypeError, ValueError):
		return 0


def _system_datetime(value):
	if not value:
		return None
	try:
		dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
		if dt.tzinfo is None:
			dt = dt.replace(tzinfo=timezone.utc)
		return dt.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)
	except (TypeError, ValueError):
		return None


def _amazon_datetime(value):
	dt = get_datetime(value)
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=ZoneInfo(get_system_timezone()))
	return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _item_mapping(store, row):
	from fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log import (
		获取平台映射物料,
	)

	item_code = 获取平台映射物料(
		store.cost_center,
		store.marketplace_id,
		row.get("asin"),
		row.get("sellerSku"),
	)
	item_name = frappe.db.get_value("Item", item_code, "item_name") if item_code else None
	return item_code, item_name


def _save_snapshot(config, store, row, granularity, batch_id, fetched_at):
	details = row.get("inventoryDetails") or {}
	reserved = details.get("reservedQuantity") or {}
	unfulfillable = details.get("unfulfillableQuantity") or {}
	researching = details.get("researchingQuantity") or {}
	item_code, item_name = _item_mapping(store, row)
	raw_text = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
	identity = "|".join(
		(
			batch_id,
			store.name,
			str(store.marketplace_id or ""),
			str(row.get("sellerSku") or ""),
			str(row.get("fnSku") or ""),
			str(row.get("condition") or ""),
		)
	)
	doc = frappe.get_doc(
		{
			"doctype": SNAPSHOT_DOCTYPE,
			"snapshot_key": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
			"amazon_store": store.name,
			"cost_center": store.cost_center,
			"marketplace_id": store.marketplace_id,
			"country": store.country,
			"seller_sku": row.get("sellerSku") or row.get("fnSku") or row.get("asin"),
			"fnsku": row.get("fnSku"),
			"asin": row.get("asin"),
			"product_name": row.get("productName"),
			"corresponding_item": item_code,
			"corresponding_item_name": item_name,
			"condition": row.get("condition"),
			"snapshot_at": fetched_at,
			"snapshot_date": fetched_at.date(),
			"source_updated_at": _system_datetime(row.get("lastUpdatedTime")),
			"granularity_type": granularity.get("granularityType"),
			"granularity_id": granularity.get("granularityId"),
			"stores_json": json.dumps(row.get("stores") or [], ensure_ascii=False),
			"total_quantity": _number(row.get("totalQuantity")),
			"fulfillable_quantity": _number(details.get("fulfillableQuantity")),
			"total_reserved_quantity": _number(reserved.get("totalReservedQuantity")),
			"total_unfulfillable_quantity": _number(unfulfillable.get("totalUnfulfillableQuantity")),
			"inbound_working_quantity": _number(details.get("inboundWorkingQuantity")),
			"inbound_shipped_quantity": _number(details.get("inboundShippedQuantity")),
			"inbound_receiving_quantity": _number(details.get("inboundReceivingQuantity")),
			"total_researching_quantity": _number(researching.get("totalResearchingQuantity")),
			"pending_customer_order_quantity": _number(reserved.get("pendingCustomerOrderQuantity")),
			"pending_transshipment_quantity": _number(reserved.get("pendingTransshipmentQuantity")),
			"fc_processing_quantity": _number(reserved.get("fcProcessingQuantity")),
			"reserved_future_supply_quantity": _number(details.get("reservedFutureSupplyQuantity")),
			"future_supply_buyable_quantity": _number(details.get("futureSupplyBuyableQuantity")),
			"customer_damaged_quantity": _number(unfulfillable.get("customerDamagedQuantity")),
			"warehouse_damaged_quantity": _number(unfulfillable.get("warehouseDamagedQuantity")),
			"distributor_damaged_quantity": _number(unfulfillable.get("distributorDamagedQuantity")),
			"carrier_damaged_quantity": _number(unfulfillable.get("carrierDamagedQuantity")),
			"defective_quantity": _number(unfulfillable.get("defectiveQuantity")),
			"expired_quantity": _number(unfulfillable.get("expiredQuantity")),
			"researching_breakdown_json": json.dumps(
				researching.get("researchingQuantityBreakdown") or [], ensure_ascii=False
			),
			"sync_batch_id": batch_id,
			"raw_json_hash": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
			"fetched_at": fetched_at,
			"raw_json": json.dumps(row, ensure_ascii=False, indent=2, default=str),
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _fetch_inventory(config, store):
	fetched_at = now_datetime()
	batch_id = f"fba-{frappe.generate_hash(length=12)}"
	last_result = {}
	try:
		last_result = json.loads(config.last_sync_result or "{}")
	except (TypeError, ValueError):
		last_result = {}
	try:
		last_full_at = get_datetime(last_result.get("last_full_at")) if last_result.get("last_full_at") else None
	except (TypeError, ValueError):
		last_full_at = None
	full_snapshot = not cint(config.incremental_lookback_minutes) or not last_full_at
	if last_full_at and (fetched_at - last_full_at).total_seconds() >= 24 * 60 * 60:
		full_snapshot = True
	base_params = {
		"details": "true" if cint(config.include_inventory_details) else "false",
		"granularityType": "Marketplace",
		"granularityId": store.marketplace_id,
		"marketplaceIds": store.marketplace_id,
	}
	if not full_snapshot and config.last_success_at and cint(config.incremental_lookback_minutes):
		start = get_datetime(config.last_success_at) - timedelta(
			minutes=cint(config.incremental_lookback_minutes)
		)
		base_params["startDateTime"] = _amazon_datetime(start)
	next_token = None
	seen_tokens = set()
	pages = []
	summary = {
		"snapshots": 0,
		"pages": 0,
		"batch_id": batch_id,
		"mode": "full" if full_snapshot else "incremental",
		"last_full_at": str(fetched_at if full_snapshot else last_full_at),
	}
	# FBA 的 nextToken 只有约30秒有效期；先连续取完分页，再写入数据库。
	while True:
		params = dict(base_params)
		if next_token:
			params["nextToken"] = next_token
		body = amazon_api_json(
			store,
			"fba-inventory",
			"GET",
			"/fba/inventory/v1/summaries",
			params=params,
			timeout=60,
		)
		ensure_database_connection()
		payload = body.get("payload") or {}
		granularity = payload.get("granularity") or {
			"granularityType": "Marketplace",
			"granularityId": store.marketplace_id,
		}
		pages.append((payload.get("inventorySummaries") or [], granularity))
		summary["pages"] += 1
		next_token = (body.get("pagination") or {}).get("nextToken")
		if not next_token:
			break
		if next_token in seen_tokens:
			raise RuntimeError("FBA Inventory API returned a repeated nextToken")
		seen_tokens.add(next_token)
	for rows, granularity in pages:
		for row in rows:
			_save_snapshot(config, store, row, granularity, batch_id, fetched_at)
			summary["snapshots"] += 1
		frappe.db.commit()
	return summary


def _delete_expired_snapshots(config, store):
	days = cint(config.snapshot_retention_days)
	if days <= 0:
		return 0
	cutoff = add_days(now_datetime(), -days)
	names = frappe.get_all(
		SNAPSHOT_DOCTYPE,
		filters={"amazon_store": store.name, "snapshot_at": ["<", cutoff]},
		pluck="name",
		limit_page_length=0,
	)
	if names:
		frappe.db.delete(SNAPSHOT_DOCTYPE, {"name": ["in", names]})
		frappe.db.commit()
	return len(names)


@frappe.whitelist()
def start_inventory_sync(name):
	config = _configuration(name)
	config.check_permission("write")
	if not cint(config.enabled):
		frappe.throw(_("请先启用 FBA 库存同步。"))
	get_store(config.amazon_store)
	_update_configuration(name, current_status="Waiting", last_error="")
	_enqueue(name)
	return {"status": "queued", "message": _("FBA 库存同步已进入后台队列。")}


def execute_inventory_sync(configuration_name):
	lock = _task_lock(configuration_name)
	if not lock.acquire(blocking=False):
		return {"status": "busy", "message": "FBA inventory configuration is already running"}
	started_at = now_datetime()
	try:
		_update_configuration(
			configuration_name,
			current_status="Running",
			last_sync_at=started_at,
			last_error="",
		)
		config = _configuration(configuration_name)
		store = get_store(config.amazon_store)
		summary = _fetch_inventory(config, store)
		summary["expired_deleted"] = _delete_expired_snapshots(config, store)
		finished_at = now_datetime()
		result = json.dumps(summary, ensure_ascii=False, default=str)
		_update_configuration(
			configuration_name,
			current_status="Completed",
			last_success_at=finished_at,
			next_sync_at=finished_at + timedelta(minutes=max(cint(config.sync_interval_minutes), 1)),
			last_sync_result=result,
			last_error="",
		)
		return {"status": "success", "summary": summary}
	except Exception as exc:
		_update_configuration(
			configuration_name,
			current_status="Failed",
			next_sync_at=now_datetime() + timedelta(minutes=15),
			last_error=str(exc)[:2000],
			last_sync_result=f"Failed: {str(exc)[:1800]}",
		)
		frappe.logger("amazon_fba_inventory", allow_site=True).exception(
			"FBA inventory sync failed: configuration=%s", configuration_name
		)
		raise
	finally:
		try:
			lock.release()
		except Exception:
			pass


def run_scheduled_inventory_sync():
	now = now_datetime()
	for name in frappe.get_all(DOCTYPE, filters={"enabled": 1}, pluck="name"):
		try:
			config = _configuration(name)
			get_store(config.amazon_store)
			if config.current_status == "Running" and config.last_sync_at:
				if (now - get_datetime(config.last_sync_at)).total_seconds() <= TASK_TIMEOUT:
					continue
				_update_configuration(name, current_status="Failed", last_error="上一次任务超时，已由调度器释放。")
				config = _configuration(name)
			if not config.next_sync_at or get_datetime(config.next_sync_at) <= now:
				_enqueue(name)
		except Exception:
			frappe.logger("amazon_fba_inventory", allow_site=True).exception(
				"FBA inventory scheduler failed: configuration=%s", name
			)
