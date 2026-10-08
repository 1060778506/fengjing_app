# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""FBA 库存分类账汇总与明细报告同步。"""

import calendar
import csv
import hashlib
import io
import json
import re
from datetime import date, datetime, time, timedelta, timezone

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_months, cint, getdate, get_datetime, now_datetime

from fengjing_app.fengjing_business.doctype.amazon_store_configuration.amazon_store_configuration import (
	create_amazon_report,
	download_amazon_report,
	ensure_database_connection,
	get_store,
	wait_for_amazon_report,
)


DOCTYPE = "Amazon FBA Inventory Ledger Configuration"
SUMMARY_DOCTYPE = "Amazon FBA Inventory Ledger Summary"
DETAIL_DOCTYPE = "Amazon FBA Inventory Ledger Detail"
TASK_TIMEOUT = 6 * 60 * 60
SUMMARY_REPORT_TYPE = "GET_LEDGER_SUMMARY_VIEW_DATA"
DETAIL_REPORT_TYPE = "GET_LEDGER_DETAIL_VIEW_DATA"
TIME_AGGREGATIONS = {"DAILY", "WEEKLY", "MONTHLY"}
LOCATION_AGGREGATIONS = {"COUNTRY", "FC"}


class AmazonFBAInventoryLedgerConfiguration(Document):
	def validate(self):
		store = get_store(self.amazon_store, require_enabled=False) if self.amazon_store else None
		if store:
			self.cost_center = store.cost_center
			self.marketplace_id = store.marketplace_id
			self.api_region = store.api_region
		self.history_segment_days = min(max(cint(self.history_segment_days), 1), 366)
		self.sync_interval_hours = max(cint(self.sync_interval_hours), 1)
		self.snapshot_retention_days = max(cint(self.snapshot_retention_days), 0)
		self.summary_time_aggregation = str(self.summary_time_aggregation or "DAILY").upper()
		self.summary_location_aggregation = str(self.summary_location_aggregation or "COUNTRY").upper()
		if self.summary_time_aggregation not in TIME_AGGREGATIONS:
			frappe.throw(_("汇总时间粒度必须是每日、每周或每月。"))
		if self.summary_location_aggregation not in LOCATION_AGGREGATIONS:
			frappe.throw(_("汇总位置粒度必须是国家或配送中心。"))
		if self.history_start_date and self.history_end_date:
			start_date = getdate(self.history_start_date)
			end_date = getdate(self.history_end_date)
			if end_date < start_date:
				frappe.throw(_("历史结束日期不能早于历史开始日期。"))
			if cint(self.detail_enabled) and start_date < getdate(add_months(getdate(now_datetime()), -18)):
				frappe.throw(_("Amazon 库存分类账明细最多只能查询最近18个月。"))
			if cint(self.summary_enabled) and self.summary_time_aggregation == "WEEKLY":
				if start_date.weekday() != 0 or end_date.weekday() != 6:
					frappe.throw(_("按周汇总时，开始日期必须是周一，结束日期必须是周日。"))
			if cint(self.summary_enabled) and self.summary_time_aggregation == "MONTHLY":
				last_day = calendar.monthrange(end_date.year, end_date.month)[1]
				if start_date.day != 1 or end_date.day != last_day:
					frappe.throw(_("按月汇总时，开始日期必须是月初，结束日期必须是月末。"))
		if cint(self.enabled) and not cint(self.summary_enabled) and not cint(self.detail_enabled):
			frappe.throw(_("请至少启用一种库存分类账报告。"))
		if not self.is_new() and (
			self.has_value_changed("history_start_date") or self.has_value_changed("history_end_date")
		):
			self.history_checkpoint = None
			self.summary_checkpoint = None
			self.detail_checkpoint = None
			self.summary_status = "Not Started"
			self.detail_status = "Not Started"


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
		frappe.cache().make_key(f"fengjing:amazon-fba-ledger:{name}"),
		timeout=TASK_TIMEOUT,
		blocking_timeout=0,
	)


def _enqueue(name, reset=False, recheck_days=0):
	recheck_days = cint(recheck_days)
	job_suffix = f"-recheck-{recheck_days}" if recheck_days else ""
	frappe.enqueue(
		execute_ledger_sync,
		queue="long",
		timeout=TASK_TIMEOUT,
		enqueue_after_commit=True,
		job_id=f"amazon-fba-ledger-{name}{job_suffix}",
		deduplicate=True,
		configuration_name=name,
		reset=reset,
		recheck_days=recheck_days,
	)


def _report_datetime(value, end=False):
	d = getdate(value)
	dt = datetime.combine(d, time.max if end else time.min, tzinfo=timezone.utc)
	return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _configured_range(config):
	if not config.history_start_date or not config.history_end_date:
		frappe.throw(_("请先填写历史开始日期和历史结束日期。"))
	start = getdate(config.history_start_date)
	end = getdate(config.history_end_date)
	if end < start:
		frappe.throw(_("可同步的历史结束日期不能早于开始日期。"))
	return start, end


def _effective_range(config, kind):
	"""返回 Amazon 能接受的完整日期范围，避免提交尚未结束的日、周或月。"""
	start, configured_end = _configured_range(config)
	end = min(configured_end, getdate(now_datetime()) - timedelta(days=1))
	if kind == "summary" and config.summary_time_aggregation == "WEEKLY":
		end -= timedelta(days=(end.weekday() + 1) % 7)
	elif kind == "summary" and config.summary_time_aggregation == "MONTHLY":
		last_day = calendar.monthrange(end.year, end.month)[1]
		if end.day != last_day:
			end = end.replace(day=1) - timedelta(days=1)
	if end < start:
		label = _("汇总报告") if kind == "summary" else _("明细报告")
		frappe.throw(_("{0}目前没有已经完整结束的可同步日期范围。").format(label))
	return start, end


def _recent_recheck_range(config, kind, days):
	"""按最近天数生成核对范围；周、月汇总自动对齐完整周期。"""
	days = cint(days)
	if days not in {7, 14, 30, 90, 180}:
		frappe.throw(_("不支持的核对周期。"))
	end = getdate(now_datetime()) - timedelta(days=1)
	start = end - timedelta(days=days - 1)
	if kind == "summary" and config.summary_time_aggregation == "WEEKLY":
		end -= timedelta(days=(end.weekday() + 1) % 7)
		start -= timedelta(days=start.weekday())
	elif kind == "summary" and config.summary_time_aggregation == "MONTHLY":
		last_day = calendar.monthrange(end.year, end.month)[1]
		if end.day != last_day:
			end = end.replace(day=1) - timedelta(days=1)
		start = min(start.replace(day=1), end.replace(day=1))
	if end < start:
		label = _("汇总报告") if kind == "summary" else _("明细报告")
		frappe.throw(_("{0}目前没有已经完整结束的可核对日期范围。").format(label))
	return start, end


def _normalise_header(value):
	return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _read_report_rows(text):
	if not str(text or "").strip():
		return []
	sample = str(text)[:4096]
	delimiter = "\t" if "\t" in sample else ","
	reader = csv.DictReader(io.StringIO(str(text)), delimiter=delimiter)
	return [dict(row) for row in reader if row and any(str(value or "").strip() for value in row.values())]


def _row_value(row, *aliases):
	values = {_normalise_header(key): value for key, value in (row or {}).items()}
	for alias in aliases:
		key = _normalise_header(alias)
		if key in values and str(values[key] or "").strip() != "":
			return str(values[key]).strip()
	return ""


def _number(value):
	text = str(value or "0").strip().replace(",", "")
	try:
		return int(float(text))
	except (TypeError, ValueError):
		return 0


def _row_date(value, fallback):
	text = str(value or "").strip()
	if not text:
		return getdate(fallback)
	for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
		try:
			return datetime.strptime(text[:10], pattern).date()
		except ValueError:
			continue
	try:
		return getdate(text)
	except Exception:
		return getdate(fallback)


def _row_datetime(value, fallback):
	text = str(value or "").strip()
	if not text:
		return datetime.combine(getdate(fallback), time.min)
	try:
		return get_datetime(text)
	except Exception:
		return datetime.combine(_row_date(text, fallback), time.min)


def _item_mapping(store, sku, asin=None):
	from fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log import (
		获取平台映射物料,
	)

	item_code = 获取平台映射物料(
		store.cost_center,
		store.marketplace_id,
		asin=asin,
		sku=sku,
	)
	item_name = frappe.db.get_value("Item", item_code, "item_name") if item_code else None
	return item_code, item_name


def _upsert(doctype, key_field, key, values, raw_hash):
	name = frappe.db.get_value(doctype, {key_field: key}, "name")
	values.update({key_field: key, "raw_json_hash": raw_hash})
	if not name:
		frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)
		return "created"
	current_hash = frappe.db.get_value(doctype, name, "raw_json_hash")
	frappe.db.set_value(doctype, name, values, update_modified=False)
	return "unchanged" if current_hash == raw_hash else "updated"


def _save_summary_rows(config, store, rows, report_id, batch_id, start, end):
	stats = {"created": 0, "updated": 0, "unchanged": 0, "rows": 0}
	for row in rows:
		sku = _row_value(row, "MSKU", "Seller SKU", "SKU")
		fnsku = _row_value(row, "FNSKU")
		asin = _row_value(row, "ASIN").upper()
		if not sku and not fnsku and not asin:
			continue
		period_date = _row_date(_row_value(row, "Date", "Period Date"), start)
		location = _row_value(row, "Location", "Fulfillment Center")
		disposition = _row_value(row, "Disposition")
		item_code, item_name = _item_mapping(store, sku, asin)
		raw_text = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
		identity = "|".join(
			(
				store.name,
				str(store.marketplace_id or ""),
				str(period_date),
				sku,
				fnsku,
				asin,
				disposition,
				location,
				str(config.summary_time_aggregation or ""),
			)
		)
		transfer = _number(_row_value(row, "Warehouse Transfer In/Out", "Warehouse Transfer"))
		values = {
			"amazon_store": store.name,
			"cost_center": store.cost_center,
			"marketplace_id": store.marketplace_id,
			"country": store.country,
			"seller_sku": sku or fnsku or asin,
			"fnsku": fnsku,
			"asin": asin,
			"product_name": _row_value(row, "Title", "Product Name"),
			"corresponding_item": item_code,
			"corresponding_item_name": item_name,
			"disposition": disposition,
			"period_date": period_date,
			"report_start_date": start,
			"report_end_date": end,
			"time_aggregation": config.summary_time_aggregation,
			"location_type": config.summary_location_aggregation,
			"location_id": location,
			"report_id": report_id,
			"starting_warehouse_balance": _number(_row_value(row, "Starting Warehouse Balance")),
			"receipts": _number(_row_value(row, "Receipts")),
			"customer_shipments": _number(_row_value(row, "Customer Shipments")),
			"customer_returns": _number(_row_value(row, "Customer Returns")),
			"vendor_returns": _number(_row_value(row, "Vendor Returns")),
			"ending_warehouse_balance": _number(_row_value(row, "Ending Warehouse Balance")),
			"in_transit_between_warehouses": _number(_row_value(row, "In Transit Between Warehouses")),
			"warehouse_transfer_in": transfer if transfer > 0 else 0,
			"warehouse_transfer_out": abs(transfer) if transfer < 0 else 0,
			"found_quantity": _number(_row_value(row, "Found")),
			"lost_quantity": _number(_row_value(row, "Lost")),
			"damaged_quantity": _number(_row_value(row, "Damaged")),
			"disposed_quantity": _number(_row_value(row, "Disposed")),
			"other_events_quantity": _number(_row_value(row, "Other Events")),
			"unknown_events_quantity": _number(_row_value(row, "Unknown Events")),
			"sync_batch_id": batch_id,
			"fetched_at": now_datetime(),
			"raw_json": json.dumps(row, ensure_ascii=False, indent=2),
		}
		action = _upsert(
			SUMMARY_DOCTYPE,
			"summary_key",
			hashlib.sha256(identity.encode("utf-8")).hexdigest(),
			values,
			hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
		)
		stats[action] += 1
		stats["rows"] += 1
	return stats


def _save_detail_rows(config, store, rows, report_id, batch_id, start, end):
	stats = {"created": 0, "updated": 0, "unchanged": 0, "rows": 0}
	for row in rows:
		sku = _row_value(row, "MSKU", "Seller SKU", "SKU")
		fnsku = _row_value(row, "FNSKU")
		asin = _row_value(row, "ASIN").upper()
		event_type = _row_value(row, "Event Type", "Event")
		if not sku and not fnsku and not asin:
			continue
		event_at = _row_datetime(_row_value(row, "Date", "Event Date", "Event Time"), start)
		reference_id = _row_value(row, "Reference ID", "Reference")
		fulfillment_center = _row_value(row, "Fulfillment Center", "Location")
		disposition = _row_value(row, "Disposition")
		reason = _row_value(row, "Reason")
		quantity = _number(_row_value(row, "Quantity"))
		item_code, item_name = _item_mapping(store, sku, asin)
		raw_text = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
		identity = "|".join(
			(
				store.name,
				str(store.marketplace_id or ""),
				str(event_at),
				event_type,
				reference_id,
				sku,
				fnsku,
				asin,
				fulfillment_center,
				disposition,
				reason,
				str(quantity),
			)
		)
		values = {
			"amazon_store": store.name,
			"cost_center": store.cost_center,
			"marketplace_id": store.marketplace_id,
			"country": store.country,
			"seller_sku": sku or fnsku or asin,
			"fnsku": fnsku,
			"asin": asin,
			"product_name": _row_value(row, "Title", "Product Name"),
			"corresponding_item": item_code,
			"corresponding_item_name": item_name,
			"event_at": event_at,
			"event_type": event_type or "UNKNOWN",
			"reference_id": reference_id,
			"quantity": quantity,
			"fulfillment_center": fulfillment_center,
			"disposition": disposition,
			"reason": reason,
			"report_id": report_id,
			"reconciled_quantity": _number(_row_value(row, "Reconciled Quantity")),
			"unreconciled_quantity": _number(_row_value(row, "Unreconciled Quantity")),
			"sync_batch_id": batch_id,
			"fetched_at": now_datetime(),
			"raw_json": json.dumps(row, ensure_ascii=False, indent=2),
		}
		action = _upsert(
			DETAIL_DOCTYPE,
			"detail_key",
			hashlib.sha256(identity.encode("utf-8")).hexdigest(),
			values,
			hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
		)
		stats[action] += 1
		stats["rows"] += 1
	return stats


def _request_report(config, store, kind, start, end, batch_id):
	if kind == "summary":
		report_type = SUMMARY_REPORT_TYPE
		options = {
			"aggregatedByTimePeriod": config.summary_time_aggregation,
			"aggregateByLocation": config.summary_location_aggregation,
		}
	else:
		report_type = DETAIL_REPORT_TYPE
		options = {}
		if str(config.detail_event_type or "").strip():
			options["eventType"] = str(config.detail_event_type).strip()
	report_id = create_amazon_report(
		store,
		report_type,
		marketplace_ids=[store.marketplace_id],
		data_start_time=_report_datetime(start),
		data_end_time=_report_datetime(end, end=True),
		report_options=options,
	)
	_update_configuration(
		config.name,
		**{
			f"{kind}_last_report_id": report_id,
			f"{kind}_status": "Running",
			f"{kind}_last_error": "",
		},
	)
	metadata = wait_for_amazon_report(store, report_id, timeout_seconds=3600, poll_seconds=15)
	if str(metadata.get("processingStatus") or "").upper() == "CANCELLED":
		return report_id, {"created": 0, "updated": 0, "unchanged": 0, "rows": 0, "no_data": True}
	text, _document = download_amazon_report(store, metadata.get("reportDocumentId"))
	ensure_database_connection()
	rows = _read_report_rows(text)
	if kind == "summary":
		stats = _save_summary_rows(config, store, rows, report_id, batch_id, start, end)
	else:
		stats = _save_detail_rows(config, store, rows, report_id, batch_id, start, end)
	frappe.db.commit()
	return report_id, stats


def _process_kind(config, store, kind, start, end, batch_id, force_start=False):
	checkpoint_field = f"{kind}_checkpoint"
	last_sync_field = f"{kind}_last_sync_at"
	status_field = f"{kind}_status"
	error_field = f"{kind}_last_error"
	cursor = start if force_start else (
		getdate(config.get(checkpoint_field)) if config.get(checkpoint_field) else start
	)
	cursor = max(cursor, start)
	result = {"created": 0, "updated": 0, "unchanged": 0, "rows": 0, "reports": 0}
	_update_configuration(config.name, **{status_field: "Running", error_field: ""})
	while cursor <= end:
		if kind == "summary" and config.summary_time_aggregation == "WEEKLY":
			segment_end = min(end, cursor + timedelta(days=6))
		elif kind == "summary" and config.summary_time_aggregation == "MONTHLY":
			month_end = date(cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1])
			segment_end = min(end, month_end)
		else:
			segment_end = min(end, cursor + timedelta(days=max(cint(config.history_segment_days), 1) - 1))
		_report_id, stats = _request_report(config, store, kind, cursor, segment_end, batch_id)
		for key in ("created", "updated", "unchanged", "rows"):
			result[key] += cint(stats.get(key))
		result["reports"] += 1
		cursor = segment_end + timedelta(days=1)
		_update_configuration(
			config.name,
			**{
				checkpoint_field: cursor,
				last_sync_field: now_datetime(),
				status_field: "Completed" if cursor > end else "Running",
				error_field: "",
			},
		)
	return result


def _delete_expired_records(config, store):
	days = cint(config.snapshot_retention_days)
	if days <= 0:
		return 0
	cutoff = add_days(getdate(now_datetime()), -days)
	deleted = 0
	for doctype, date_field in ((SUMMARY_DOCTYPE, "period_date"), (DETAIL_DOCTYPE, "event_at")):
		names = frappe.get_all(
			doctype,
			filters={"amazon_store": store.name, date_field: ["<", cutoff]},
			pluck="name",
			limit_page_length=0,
		)
		if names:
			frappe.db.delete(doctype, {"name": ["in", names]})
			deleted += len(names)
	if deleted:
		frappe.db.commit()
	return deleted


@frappe.whitelist()
def start_ledger_sync(name):
	config = _configuration(name)
	config.check_permission("write")
	if not cint(config.enabled):
		frappe.throw(_("请先启用 FBA 库存分类账同步。"))
	if cint(config.summary_enabled):
		_effective_range(config, "summary")
	if cint(config.detail_enabled):
		_effective_range(config, "detail")
	get_store(config.amazon_store)
	values = {"current_status": "Waiting", "last_error": "", "history_checkpoint": None}
	if cint(config.summary_enabled):
		values.update({"summary_checkpoint": None, "summary_status": "Waiting", "summary_last_error": ""})
	if cint(config.detail_enabled):
		values.update({"detail_checkpoint": None, "detail_status": "Waiting", "detail_last_error": ""})
	_update_configuration(name, **values)
	_enqueue(name, reset=True)
	return {"status": "queued", "message": _("FBA 库存分类账历史同步已进入后台队列。")}


@frappe.whitelist()
def start_recheck_sync(name, days):
	config = _configuration(name)
	config.check_permission("write")
	days = cint(days)
	if days not in {7, 14, 30, 90, 180}:
		frappe.throw(_("不支持的核对周期。"))
	if not cint(config.enabled):
		frappe.throw(_("请先启用 FBA 库存分类账同步。"))
	if cint(config.summary_enabled):
		_recent_recheck_range(config, "summary", days)
	if cint(config.detail_enabled):
		_recent_recheck_range(config, "detail", days)
	get_store(config.amazon_store)
	values = {"current_status": "Waiting", "last_error": ""}
	if cint(config.summary_enabled):
		values.update({"summary_status": "Waiting", "summary_last_error": ""})
	if cint(config.detail_enabled):
		values.update({"detail_status": "Waiting", "detail_last_error": ""})
	_update_configuration(name, **values)
	_enqueue(name, recheck_days=days)
	return {
		"status": "queued",
		"message": _("FBA 库存分类账最近{0}天核对已进入后台队列。").format(days),
	}


def execute_ledger_sync(configuration_name, reset=False, recheck_days=0):
	lock = _task_lock(configuration_name)
	if not lock.acquire(blocking=False):
		return {"status": "busy", "message": "FBA inventory ledger configuration is already running"}
	started_at = now_datetime()
	active_kind = None
	try:
		_update_configuration(
			configuration_name,
			current_status="Running",
			last_sync_at=started_at,
			last_error="",
		)
		config = _configuration(configuration_name)
		store = get_store(config.amazon_store)
		if reset:
			reset_values = {"history_checkpoint": None}
			if cint(config.summary_enabled):
				reset_values["summary_checkpoint"] = None
			if cint(config.detail_enabled):
				reset_values["detail_checkpoint"] = None
			_update_configuration(configuration_name, **reset_values)
			config = _configuration(configuration_name)
		batch_id = f"ledger-{frappe.generate_hash(length=12)}"
		recheck_days = cint(recheck_days)
		summary = {"batch_id": batch_id, "mode": "recheck" if recheck_days else "history"}
		if recheck_days:
			summary["recheck_days"] = recheck_days
		completed_ends = []
		if cint(config.summary_enabled):
			active_kind = "summary"
			start, end = (
				_recent_recheck_range(config, "summary", recheck_days)
				if recheck_days
				else _effective_range(config, "summary")
			)
			summary["summary_range"] = {"start": str(start), "end": str(end)}
			summary["summary"] = _process_kind(
				config, store, "summary", start, end, batch_id, force_start=bool(recheck_days)
			)
			completed_ends.append(end)
			config = _configuration(configuration_name)
		if cint(config.detail_enabled):
			active_kind = "detail"
			start, end = (
				_recent_recheck_range(config, "detail", recheck_days)
				if recheck_days
				else _effective_range(config, "detail")
			)
			summary["detail_range"] = {"start": str(start), "end": str(end)}
			summary["detail"] = _process_kind(
				config, store, "detail", start, end, batch_id, force_start=bool(recheck_days)
			)
			completed_ends.append(end)
			config = _configuration(configuration_name)
		summary["expired_deleted"] = _delete_expired_records(config, store)
		finished_at = now_datetime()
		_update_configuration(
			configuration_name,
			current_status="Completed",
			history_checkpoint=max(completed_ends),
			last_success_at=finished_at,
			next_sync_at=finished_at + timedelta(hours=max(cint(config.sync_interval_hours), 1)),
			last_sync_result=json.dumps(summary, ensure_ascii=False, default=str),
			last_error="",
		)
		return {"status": "success", "summary": summary}
	except Exception as exc:
		message = str(exc)
		values = {
			"current_status": "Failed",
			"next_sync_at": now_datetime() + timedelta(minutes=30),
			"last_error": message[:2000],
			"last_sync_result": f"Failed: {message[:1800]}",
		}
		if active_kind:
			values.update({f"{active_kind}_status": "Failed", f"{active_kind}_last_error": message[:2000]})
		_update_configuration(configuration_name, **values)
		frappe.logger("amazon_fba_inventory_ledger", allow_site=True).exception(
			"FBA inventory ledger sync failed: configuration=%s", configuration_name
		)
		raise
	finally:
		try:
			lock.release()
		except Exception:
			pass


def run_scheduled_ledger_sync():
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
			if config.next_sync_at and get_datetime(config.next_sync_at) > now:
				continue
			# 历史已完成时只重新核对最近一个分段，避免每次重跑全部历史。
			if config.current_status == "Completed":
				starts = []
				values = {"current_status": "Waiting"}
				if cint(config.summary_enabled):
					summary_range_start, summary_end = _effective_range(config, "summary")
					if config.summary_time_aggregation == "WEEKLY":
						summary_start = max(summary_range_start, summary_end - timedelta(days=6))
					elif config.summary_time_aggregation == "MONTHLY":
						summary_start = max(summary_range_start, summary_end.replace(day=1))
					else:
						summary_start = max(
							summary_range_start,
							summary_end - timedelta(days=max(cint(config.history_segment_days), 1) - 1),
						)
					starts.append(summary_start)
					values.update({"summary_checkpoint": summary_start, "summary_status": "Waiting"})
				if cint(config.detail_enabled):
					detail_range_start, detail_end = _effective_range(config, "detail")
					detail_start = max(
						detail_range_start,
						detail_end - timedelta(days=max(cint(config.history_segment_days), 1) - 1),
					)
					starts.append(detail_start)
					values.update({"detail_checkpoint": detail_start, "detail_status": "Waiting"})
				values["history_checkpoint"] = min(starts)
				_update_configuration(name, **values)
			_enqueue(name)
		except Exception:
			frappe.logger("amazon_fba_inventory_ledger", allow_site=True).exception(
				"FBA inventory ledger scheduler failed: configuration=%s", name
			)
