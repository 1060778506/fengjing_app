# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""Amazon Warehousing and Distribution API 库存快照同步。"""

import hashlib
import json
from datetime import timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, get_datetime, now_datetime

from fengjing_app.fengjing_business.doctype.amazon_store_configuration.amazon_store_configuration import (
	amazon_api_json,
	ensure_database_connection,
	get_store,
)


DOCTYPE = "Amazon AWD Inventory Configuration"
SNAPSHOT_DOCTYPE = "Amazon AWD Inventory Snapshot"
TASK_TIMEOUT = 2 * 60 * 60


class AmazonAWDInventoryConfiguration(Document):
	def validate(self):
		store = get_store(self.amazon_store, require_enabled=False) if self.amazon_store else None
		if store:
			self.cost_center = store.cost_center
			self.marketplace_id = store.marketplace_id
			self.api_region = store.api_region
		self.sync_interval_minutes = max(cint(self.sync_interval_minutes), 1)
		self.snapshot_retention_days = max(cint(self.snapshot_retention_days), 0)


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
		frappe.cache().make_key(f"fengjing:amazon-awd-inventory:{name}"),
		timeout=TASK_TIMEOUT,
		blocking_timeout=0,
	)


def _enqueue(name):
	frappe.enqueue(
		execute_inventory_sync,
		queue="long",
		timeout=TASK_TIMEOUT,
		enqueue_after_commit=True,
		job_id=f"amazon-awd-inventory-{name}",
		deduplicate=True,
		configuration_name=name,
	)


def _number(value):
	try:
		return int(float(value or 0))
	except (TypeError, ValueError):
		return 0


def _item_mapping(store, sku):
	from fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log import (
		获取平台映射物料,
	)

	item_code = 获取平台映射物料(
		store.cost_center,
		store.marketplace_id,
		sku=sku,
	)
	item_name = frappe.db.get_value("Item", item_code, "item_name") if item_code else None
	return item_code, item_name


def _save_snapshot(store, row, batch_id, fetched_at):
	sku = str(row.get("sku") or "").strip()
	if not sku:
		return None
	item_code, item_name = _item_mapping(store, sku)
	details = row.get("inventoryDetails") or {}
	expiration = row.get("expirationDetails") or []
	raw_text = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
	identity = "|".join((batch_id, store.name, str(store.marketplace_id or ""), sku))
	doc = frappe.get_doc(
		{
			"doctype": SNAPSHOT_DOCTYPE,
			"snapshot_key": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
			"amazon_store": store.name,
			"cost_center": store.cost_center,
			"marketplace_id": store.marketplace_id,
			"country": store.country,
			"inventory_scope": "AWD Network Aggregate",
			"warehouse_granularity": "Network Aggregate",
			"seller_sku": sku,
			"product_name": item_name,
			"corresponding_item": item_code,
			"corresponding_item_name": item_name,
			"snapshot_at": fetched_at,
			"snapshot_date": fetched_at.date(),
			"expiration_details_json": json.dumps(expiration, ensure_ascii=False, indent=2, default=str),
			"total_onhand_quantity": _number(row.get("totalOnhandQuantity")),
			"total_inbound_quantity": _number(row.get("totalInboundQuantity")),
			"available_distributable_quantity": _number(details.get("availableDistributableQuantity")),
			"reserved_distributable_quantity": _number(details.get("reservedDistributableQuantity")),
			"replenishment_quantity": _number(details.get("replenishmentQuantity")),
			"inventory_details_json": json.dumps(details, ensure_ascii=False, indent=2, default=str),
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
	batch_id = f"awd-{frappe.generate_hash(length=12)}"
	base_params = {
		"sortOrder": "ASCENDING",
		"details": "SHOW" if cint(config.include_inventory_details) else "HIDE",
		"maxResults": 200,
	}
	next_token = None
	seen_tokens = set()
	summary = {"snapshots": 0, "pages": 0, "batch_id": batch_id}
	while True:
		params = dict(base_params)
		if next_token:
			params["nextToken"] = next_token
		body = amazon_api_json(
			store,
			"awd-inventory",
			"GET",
			"/awd/2024-05-09/inventory",
			params=params,
			timeout=60,
		)
		ensure_database_connection()
		for row in body.get("inventory") or []:
			if _save_snapshot(store, row, batch_id, fetched_at):
				summary["snapshots"] += 1
		frappe.db.commit()
		summary["pages"] += 1
		next_token = body.get("nextToken")
		if not next_token:
			break
		if next_token in seen_tokens:
			raise RuntimeError("AWD Inventory API returned a repeated nextToken")
		seen_tokens.add(next_token)
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
		frappe.throw(_("请先启用 AWD 库存同步。"))
	get_store(config.amazon_store)
	_update_configuration(name, current_status="Waiting", last_error="")
	_enqueue(name)
	return {"status": "queued", "message": _("AWD 库存同步已进入后台队列。")}


def execute_inventory_sync(configuration_name):
	lock = _task_lock(configuration_name)
	if not lock.acquire(blocking=False):
		return {"status": "busy", "message": "AWD inventory configuration is already running"}
	started_at = now_datetime()
	try:
		_update_configuration(
			configuration_name,
			current_status="Running",
			last_sync_at=started_at,
			last_permission_check_at=started_at,
			last_error="",
		)
		config = _configuration(configuration_name)
		store = get_store(config.amazon_store)
		summary = _fetch_inventory(config, store)
		summary["expired_deleted"] = _delete_expired_snapshots(config, store)
		finished_at = now_datetime()
		_update_configuration(
			configuration_name,
			permission_status="Available",
			current_status="Completed",
			last_success_at=finished_at,
			next_sync_at=finished_at + timedelta(minutes=max(cint(config.sync_interval_minutes), 1)),
			last_sync_result=json.dumps(summary, ensure_ascii=False, default=str),
			last_error="",
		)
		return {"status": "success", "summary": summary}
	except Exception as exc:
		message = str(exc)
		values = {
			"current_status": "Failed",
			"next_sync_at": now_datetime() + timedelta(minutes=15),
			"last_error": message[:2000],
			"last_sync_result": f"Failed: {message[:1800]}",
		}
		if "HTTP 403" in message:
			values.update({"permission_status": "Unavailable", "last_permission_check_at": now_datetime()})
		_update_configuration(configuration_name, **values)
		frappe.logger("amazon_awd_inventory", allow_site=True).exception(
			"AWD inventory sync failed: configuration=%s", configuration_name
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
			frappe.logger("amazon_awd_inventory", allow_site=True).exception(
				"AWD inventory scheduler failed: configuration=%s", name
			)
