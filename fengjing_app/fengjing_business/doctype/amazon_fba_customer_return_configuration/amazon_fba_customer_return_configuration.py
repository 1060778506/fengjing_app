# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""FBA 客户退货报告：历史回填、每日增量与分层核对。"""

import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import frappe
from dateutil.parser import parse as parse_datetime
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_datetime, get_system_timezone, now_datetime

from fengjing_app.fengjing_business.doctype.amazon_store_configuration.amazon_store_configuration import (
	create_amazon_report,
	download_amazon_report,
	ensure_database_connection,
	get_region,
	get_store,
	wait_for_amazon_report,
)


DOCTYPE = "Amazon FBA Customer Return Configuration"
RETURN_DOCTYPE = "Amazon FBA Customer Return"
REPORT_TYPE = "GET_FBA_FULFILLMENT_CUSTOMER_RETURNS_DATA"
RECHECK_DAYS = (7, 14, 30, 90, 180)
HISTORY_WINDOW_DAYS = 30
TASK_TIMEOUT = 6 * 60 * 60
HISTORY_WINDOWS_PER_JOB = 3
LATEST_LOOKBACK_DAYS = 7


class AmazonFBACustomerReturnConfiguration(Document):
	def validate(self):
		store = get_store(self.amazon_store, require_enabled=False) if self.amazon_store else None
		if store:
			self.cost_center = store.cost_center
			self.marketplace_id = store.marketplace_id
			self.api_region = get_region(store)
			if cint(self.enabled) and not cint(store.enabled):
				frappe.throw(_("请先启用关联的亚马逊店铺。"))
		self.incremental_interval_minutes = max(cint(self.incremental_interval_minutes), 60)
		self.active_start_hour = min(max(cint(self.active_start_hour), 0), 23)
		self.active_end_hour = min(max(cint(self.active_end_hour), 1), 24)
		if self.active_start_hour >= self.active_end_hour:
			frappe.throw(_("每日结束小时必须晚于开始小时。"))
		for days in RECHECK_DAYS:
			fieldname = f"recheck_{days}_interval_days"
			self.set(fieldname, max(cint(self.get(fieldname)), 1))
		if self.history_start_datetime and self.history_end_datetime:
			if _to_utc(self.history_end_datetime) <= _to_utc(self.history_start_datetime):
				frappe.throw(_("历史结束时间必须晚于开始时间。"))
			if _to_utc(self.history_end_datetime) > datetime.now(timezone.utc) - timedelta(minutes=5):
				frappe.throw(_("历史结束时间须早于当前时间至少 5 分钟；近期数据由增量与核对任务抓取。"))
		if not self.is_new() and (
			self.has_value_changed("history_start_datetime")
			or self.has_value_changed("history_end_datetime")
		):
			self.history_checkpoint = None
			self.history_progress = 0
			self.history_status = "Not Started"
			self.history_summary = ""


def _to_utc(value):
	dt = get_datetime(value)
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=ZoneInfo(get_system_timezone()))
	return dt.astimezone(timezone.utc)


def _to_system(value):
	return value.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)


def _amazon_time(value):
	return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _configuration(name):
	ensure_database_connection()
	return frappe.get_doc(DOCTYPE, name)


def _update_configuration(name, **values):
	ensure_database_connection()
	frappe.db.set_value(DOCTYPE, name, values, update_modified=False)
	frappe.db.commit()


def _enqueue(name, sync_type):
	frappe.enqueue(
		execute_return_sync,
		queue="long",
		timeout=TASK_TIMEOUT,
		enqueue_after_commit=True,
		job_id=f"amazon-fba-returns-{name}-{sync_type}",
		deduplicate=True,
		configuration_name=name,
		sync_type=sync_type,
	)


def _prepare_manual(name):
	config = _configuration(name)
	config.check_permission("write")
	if not cint(config.enabled):
		frappe.throw(_("请先启用 FBA 客户退货配置。"))
	get_store(config.amazon_store)
	return config


@frappe.whitelist()
def start_history_sync(name):
	config = _prepare_manual(name)
	if not config.history_start_datetime or not config.history_end_datetime:
		frappe.throw(_("请先填写历史同步的开始和结束时间。"))
	values = {"history_status": "Waiting", "history_last_error": "", "last_error": ""}
	if config.history_status == "Completed":
		values.update({"history_checkpoint": None, "history_progress": 0, "history_summary": ""})
	_update_configuration(name, **values)
	_enqueue(name, "history")
	return {"status": "queued", "message": _("FBA 客户退货历史任务已加入队列。")}


@frappe.whitelist()
def start_incremental_sync(name):
	_prepare_manual(name)
	_enqueue(name, "incremental")
	return {"status": "queued", "message": _("FBA 客户退货增量任务已加入队列。")}


@frappe.whitelist()
def start_recheck_sync(name, days):
	days = cint(days)
	if days not in RECHECK_DAYS:
		frappe.throw(_("不支持该核对天数。"))
	_prepare_manual(name)
	_enqueue(name, f"recheck_{days}")
	return {"status": "queued", "message": _("FBA 客户退货核对任务已加入队列。")}


def _history_window(config, now=None):
	if not config.history_start_datetime or not config.history_end_datetime:
		raise ValueError("History start and end are required")
	start = _to_utc(config.history_start_datetime)
	end = min(_to_utc(config.history_end_datetime), now or datetime.now(timezone.utc))
	cursor = max(_to_utc(config.history_checkpoint), start) if config.history_checkpoint else start
	if cursor >= end:
		return None
	return cursor, min(cursor + timedelta(days=HISTORY_WINDOW_DAYS), end)


def _normalise_header(value):
	return re.sub(r"[^a-z0-9]+", "", str(value or "").lstrip("\ufeff").strip().lower())


def _parse_rows(text):
	if not str(text or "").strip():
		return []
	value = str(text).lstrip("\ufeff")
	delimiter = "\t" if "\t" in value[:4096] else ","
	reader = csv.DictReader(io.StringIO(value), delimiter=delimiter)
	if not reader.fieldnames:
		return []
	return [
		{_normalise_header(key): str(item or "").strip() for key, item in row.items() if key}
		for row in reader if row and any(str(item or "").strip() for item in row.values())
	]


def _row_value(row, name):
	return row.get(_normalise_header(name), "")


def _return_datetime(value):
	if not value:
		return None
	try:
		dt = parse_datetime(value)
	except (TypeError, ValueError, OverflowError):
		return None
	return _to_system(dt) if dt.tzinfo else dt


def _return_key(store, row, occurrence):
	# 不用报告 ID、数量、原因或状态做键；核对时这些值可能变化。
	identity = [
		store.name,
		_row_value(row, "return-date"),
		_row_value(row, "order-id"),
		_row_value(row, "sku"),
		_row_value(row, "fulfillment-center-id"),
		occurrence,
	]
	return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()


def _save_rows(store, rows, report_id, fetched_at):
	seen = defaultdict(int)
	stats = {"rows": 0, "created": 0, "updated": 0, "skipped": 0}
	# 相同订单、SKU、日期可能有多条记录；稳定排序后再给重复行编号。
	for row in sorted(rows, key=lambda item: (
		_row_value(item, "return-date"),
		_row_value(item, "order-id"),
		_row_value(item, "sku"),
		_row_value(item, "fulfillment-center-id"),
		_row_value(item, "license-plate-number"),
		json.dumps(item, ensure_ascii=False, sort_keys=True),
	)):
		order_id = _row_value(row, "order-id")
		sku = _row_value(row, "sku")
		return_date = _row_value(row, "return-date")
		if not sku or not return_date:
			stats["skipped"] += 1
			continue
		base = (return_date, order_id, sku, _row_value(row, "fulfillment-center-id"))
		seen[base] += 1
		key = _return_key(store, row, seen[base])
		try:
			quantity = int(float(_row_value(row, "quantity") or 0))
		except ValueError:
			quantity = 0
		values = {
			"amazon_store": store.name,
			"marketplace_id": store.marketplace_id,
			"cost_center": store.cost_center,
			"order_id": order_id,
			"seller_sku": sku,
			"asin": _row_value(row, "asin"),
			"fnsku": _row_value(row, "fnsku"),
			"product_name": _row_value(row, "product-name"),
			"return_at": _return_datetime(return_date),
			"return_date_raw": return_date,
			"quantity": quantity,
			"fulfillment_center_id": _row_value(row, "fulfillment-center-id"),
			"detailed_disposition": _row_value(row, "detailed-disposition"),
			"reason": _row_value(row, "reason"),
			"status": _row_value(row, "status"),
			"license_plate_number": _row_value(row, "license-plate-number"),
			"customer_comments": _row_value(row, "customer-comments"),
			"report_id": report_id,
			"fetched_at": fetched_at,
			"raw_json": json.dumps(row, ensure_ascii=False),
		}
		existing = frappe.db.get_value(RETURN_DOCTYPE, {"return_key": key}, "name")
		if existing:
			frappe.db.set_value(RETURN_DOCTYPE, existing, values, update_modified=False)
			stats["updated"] += 1
		else:
			frappe.get_doc({"doctype": RETURN_DOCTYPE, "return_key": key, **values}).insert(ignore_permissions=True)
			stats["created"] += 1
		stats["rows"] += 1
	return stats


def _fetch_window(store, start, end):
	report_id = create_amazon_report(
		store,
		REPORT_TYPE,
		marketplace_ids=[store.marketplace_id],
		data_start_time=_amazon_time(start),
		data_end_time=_amazon_time(end),
	)
	metadata = wait_for_amazon_report(store, report_id, timeout_seconds=3600, poll_seconds=15)
	if str(metadata.get("processingStatus") or "").upper() == "CANCELLED":
		return report_id, {"rows": 0, "created": 0, "updated": 0, "skipped": 0, "no_data": True}
	text, _ = download_amazon_report(store, metadata["reportDocumentId"])
	ensure_database_connection()
	rows = _parse_rows(text)
	stats = _save_rows(store, rows, report_id, now_datetime())
	if rows and stats["skipped"] == len(rows):
		raise ValueError("FBA customer returns report had rows but no valid sku and return-date columns")
	return report_id, stats


def _task_lock(name):
	return frappe.cache().lock(
		frappe.cache().make_key(f"fengjing:amazon-fba-returns:{name}"),
		timeout=TASK_TIMEOUT,
		blocking_timeout=0,
	)


def _next_recheck(now, interval_days):
	return get_datetime(now).replace(hour=2, minute=59, second=0, microsecond=0) + timedelta(
		days=max(cint(interval_days), 1)
	)


def _first_recheck(now):
	candidate = get_datetime(now).replace(hour=2, minute=59, second=0, microsecond=0)
	return candidate if candidate > now else candidate + timedelta(days=1)


def execute_return_sync(configuration_name, sync_type):
	if sync_type not in {"history", "incremental", *(f"recheck_{days}" for days in RECHECK_DAYS)}:
		raise ValueError(f"Unsupported FBA customer return task: {sync_type}")
	lock = _task_lock(configuration_name)
	if not lock.acquire(blocking=False):
		return {"status": "busy"}
	summary = {"windows": 0, "rows": 0, "created": 0, "updated": 0, "skipped": 0, "empty_windows": 0}
	try:
		_update_configuration(
			configuration_name,
			current_task_status="Running",
			current_execution_type=sync_type,
			current_task_started_at=now_datetime(),
			last_error="",
			**({"history_status": "Running", "history_last_error": ""} if sync_type == "history" else {}),
		)
		config = _configuration(configuration_name)
		store = get_store(config.amazon_store)
		if not cint(config.enabled):
			raise ValueError("FBA customer return configuration is disabled")
		if sync_type == "history":
			for _ in range(HISTORY_WINDOWS_PER_JOB):
				config = _configuration(configuration_name)
				window = _history_window(config)
				if not window:
					break
				start, end = window
				report_id, stats = _fetch_window(store, start, end)
				for key in ("rows", "created", "updated", "skipped"):
					summary[key] += stats[key]
				summary["windows"] += 1
				summary["empty_windows"] += int(stats.get("no_data", False))
				target_start = _to_utc(config.history_start_datetime)
				target_end = _to_utc(config.history_end_datetime)
				progress = min(100, max(0, (end - target_start).total_seconds() / (target_end - target_start).total_seconds() * 100))
				_update_configuration(
					configuration_name,
					history_checkpoint=_to_system(end),
					history_progress=progress,
					history_summary=json.dumps(summary, ensure_ascii=False),
					last_report_id=report_id,
				)
			config = _configuration(configuration_name)
			completed = _history_window(config) is None
			finished_at = now_datetime()
			_update_configuration(
				configuration_name,
				history_status="Completed" if completed else "Waiting",
				history_progress=100 if completed else config.history_progress,
				current_task_status="Success",
				current_execution_type="",
				current_task_completed_at=finished_at,
				last_run_result=json.dumps(summary, ensure_ascii=False),
				last_error="",
			)
			return {"status": "completed" if completed else "waiting", "summary": summary}

		end = datetime.now(timezone.utc)
		if sync_type == "incremental":
			# 报告每日更新；重读最近一周以接住延迟入库的退货。
			start = end - timedelta(days=LATEST_LOOKBACK_DAYS)
		else:
			start = end - timedelta(days=int(sync_type.rsplit("_", 1)[1]))
		# 核对区间可达 180 天，但每个 Amazon 报告请求只覆盖 30 天。
		cursor = start
		last_report_id = ""
		while cursor < end:
			window_end = min(cursor + timedelta(days=HISTORY_WINDOW_DAYS), end)
			last_report_id, stats = _fetch_window(store, cursor, window_end)
			for key in ("rows", "created", "updated", "skipped"):
				summary[key] += stats[key]
			summary["windows"] += 1
			summary["empty_windows"] += int(stats.get("no_data", False))
			frappe.db.commit()
			cursor = window_end
		finished_at = now_datetime()
		values = {
			"current_task_status": "Success",
			"current_execution_type": "",
			"current_task_completed_at": finished_at,
			"last_run_result": json.dumps(summary, ensure_ascii=False),
			"last_report_id": last_report_id,
			"last_error": "",
		}
		if sync_type == "incremental":
			values.update({
				"incremental_checkpoint": _to_system(end),
				"incremental_last_at": finished_at,
				"incremental_next_at": finished_at + timedelta(minutes=max(cint(config.incremental_interval_minutes), 60)),
				"incremental_summary": json.dumps(summary, ensure_ascii=False),
			})
		else:
			days = int(sync_type.rsplit("_", 1)[1])
			values.update({
				f"recheck_{days}_last_at": finished_at,
				f"recheck_{days}_next_at": _next_recheck(finished_at, config.get(f"recheck_{days}_interval_days")),
			})
		_update_configuration(configuration_name, **values)
		return {"status": "success", "summary": summary}
	except Exception as exc:
		frappe.db.rollback()
		values = {
			"current_task_status": "Failed",
			"current_execution_type": "",
			"current_task_completed_at": now_datetime(),
			"last_error": str(exc)[:2000],
			"last_run_result": f"Failed: {str(exc)[:1800]}",
		}
		if sync_type == "history":
			values.update({"history_status": "Failed", "history_last_error": str(exc)[:2000]})
		elif sync_type == "incremental":
			values["incremental_next_at"] = now_datetime() + timedelta(hours=1)
		else:
			days = int(sync_type.rsplit("_", 1)[1])
			values[f"recheck_{days}_next_at"] = now_datetime() + timedelta(hours=1)
		_update_configuration(configuration_name, **values)
		frappe.logger("amazon_fba_customer_returns", allow_site=True).exception(
			"FBA customer returns sync failed: configuration=%s type=%s", configuration_name, sync_type
		)
		raise
	finally:
		lock.release()


def run_scheduled_return_sync():
	now = now_datetime()
	for name in frappe.get_all(DOCTYPE, filters={"enabled": 1}, pluck="name"):
		try:
			config = _configuration(name)
			get_store(config.amazon_store)
			if config.current_task_status == "Running":
				started = get_datetime(config.current_task_started_at) if config.current_task_started_at else None
				if not started or (now - started).total_seconds() <= TASK_TIMEOUT:
					continue
				_update_configuration(name, current_task_status="Failed", current_execution_type="", last_error="Previous task timed out")
				config = _configuration(name)
			if config.history_status in {"Waiting", "Running"} and config.history_start_datetime and config.history_end_datetime:
				if _history_window(config):
					_enqueue(name, "history")
					continue
			if not config.incremental_next_at or get_datetime(config.incremental_next_at) <= now:
				if cint(config.active_start_hour) <= now.hour < (cint(config.active_end_hour) or 24):
					_enqueue(name, "incremental")
					continue
			initialised = {}
			for days in RECHECK_DAYS:
				if cint(config.get(f"enable_recheck_{days}")) and not config.get(f"recheck_{days}_next_at"):
					initialised[f"recheck_{days}_next_at"] = _first_recheck(now)
			if initialised:
				_update_configuration(name, **initialised)
			for days in RECHECK_DAYS:
				if not cint(config.get(f"enable_recheck_{days}")):
					continue
				next_at = config.get(f"recheck_{days}_next_at")
				if next_at and get_datetime(next_at) <= now:
					_enqueue(name, f"recheck_{days}")
					break
		except Exception:
			frappe.logger("amazon_fba_customer_returns", allow_site=True).exception(
				"FBA customer returns scheduler failed: configuration=%s", name
			)
