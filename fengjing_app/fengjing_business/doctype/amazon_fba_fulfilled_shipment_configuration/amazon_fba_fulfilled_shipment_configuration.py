# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""Amazon-Fulfilled Shipments：18 个月历史回填、增量与分层核对。"""

import csv
import hashlib
import io
import json
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import frappe
from dateutil.parser import parse as parse_datetime
from dateutil.relativedelta import relativedelta
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, get_system_timezone, now_datetime

from fengjing_app.fengjing_business.doctype.amazon_store_configuration.amazon_store_configuration import (
    AmazonAPIError, create_amazon_report, download_amazon_report, ensure_database_connection,
    get_region, get_store, wait_for_amazon_report,
)

DOCTYPE = "Amazon FBA Fulfilled Shipment Configuration"
RECORD_DOCTYPE = "Amazon FBA Fulfilled Shipment"
REPORT_TYPE = "GET_AMAZON_FULFILLED_SHIPMENTS_DATA_GENERAL"
RECHECK_DAYS = (7, 14, 30, 90, 180)
WINDOW_DAYS = 30
HISTORY_WINDOWS_PER_JOB = 2
TASK_TIMEOUT = 6 * 60 * 60
LATEST_LOOKBACK_DAYS = 7
# Amazon 允许每卖家 8 小时最多 12 次（包括 Seller Central 手动请求）；自动抓取最多占 10 次。
REQUEST_LIMIT = 10
REQUEST_WINDOW_SECONDS = 8 * 60 * 60

class QuotaDeferred(Exception):
    def __init__(self, retry_at):
        self.retry_at = retry_at
        super().__init__(f"FBA 发货报告请求额度暂满，预计 {retry_at} 后续跑")


class AmazonFBAFulfilledShipmentConfiguration(Document):
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
            name = f"recheck_{days}_interval_days"
            self.set(name, max(cint(self.get(name)), 1))
        if cint(self.history_18_months) and (self.is_new() or self.has_value_changed("history_18_months")):
            self.history_checkpoint = None
            self.history_target_at = None
            self.history_status = "Waiting"
            self.history_progress = 0
            self.history_summary = ""
            self.history_last_error = ""
        elif not cint(self.history_18_months) and not self.is_new() and self.has_value_changed("history_18_months"):
            self.history_status = "Not Started"


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
        execute_shipment_sync, queue="long", timeout=TASK_TIMEOUT,
        enqueue_after_commit=True,
        job_id=f"amazon-fba-shipments-{name}-{sync_type}", deduplicate=True,
        configuration_name=name, sync_type=sync_type,
    )


def _prepare_manual(name):
    config = _configuration(name)
    config.check_permission("write")
    if not cint(config.enabled):
        frappe.throw(_("请先启用 FBA 发货报告配置。"))
    get_store(config.amazon_store)
    return config


@frappe.whitelist()
def start_history_sync(name):
    config = _prepare_manual(name)
    if not cint(config.history_18_months):
        frappe.throw(_("请先勾选“倒推18个月抓取历史发货记录”并保存。"))
    _update_configuration(name, history_status="Waiting", history_checkpoint=None,
                          history_target_at=None, history_progress=0,
                          history_summary="", history_last_error="", last_error="")
    _enqueue(name, "history")
    return {"status": "queued", "message": _("18 个月历史发货任务已加入队列。")}


@frappe.whitelist()
def start_incremental_sync(name):
    _prepare_manual(name)
    _enqueue(name, "incremental")
    return {"status": "queued"}


@frappe.whitelist()
def start_recheck_sync(name, days):
    days = cint(days)
    if days not in RECHECK_DAYS:
        frappe.throw(_("不支持该核对天数。"))
    _prepare_manual(name)
    _enqueue(name, f"recheck_{days}")
    return {"status": "queued"}


def _history_window(config):
    if not cint(config.history_18_months):
        return None
    now = datetime.now(timezone.utc)
    target = _to_utc(config.history_target_at) if config.history_target_at else now
    # 报告在创建后仍需排队生成。若从恰好 18 个月前请求，生成时就已越过
    # Amazon 的历史边界并返回 FATAL；留出一天余量，续跑时也保持这个余量。
    earliest = now - relativedelta(months=18) + timedelta(days=1)
    start = max(target - relativedelta(months=18) + timedelta(days=1), earliest)
    cursor = max(_to_utc(config.history_checkpoint), start) if config.history_checkpoint else start
    if cursor >= target:
        return None
    return cursor, min(cursor + timedelta(days=WINDOW_DAYS), target), start, target


def _normalise_header(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lstrip("\ufeff").strip().lower())


def _parse_rows(text):
    if not str(text or "").strip():
        return []
    value = str(text)
    delimiter = "\t" if "\t" in value[:4096] else ","
    reader = csv.DictReader(io.StringIO(value, newline=""), delimiter=delimiter,
                            restkey="__extra_columns__")
    if not reader.fieldnames:
        return []
    return [
        {
            "source": dict(row),
            "indexed": {_normalise_header(key): str(item or "").strip()
                        for key, item in row.items() if key != "__extra_columns__"},
        }
        for row in reader if row and any(str(item or "").strip() for item in row.values())
    ]


def _value(row, key):
    return row["indexed"].get(_normalise_header(key), "")


def _datetime(value):
    if not value:
        return None
    try:
        dt = parse_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return _to_system(dt) if dt.tzinfo else dt


def _identity(store, row, occurrence):
    shipment_id = _value(row, "shipment-id")
    shipment_item_id = _value(row, "shipment-item-id")
    if shipment_id and shipment_item_id:
        parts = (store.name, shipment_id, shipment_item_id)
    else:
        parts = (store.name, shipment_id, _value(row, "amazon-order-id"),
                 _value(row, "amazon-order-item-id"), _value(row, "sku"),
                 _value(row, "shipment-date"), occurrence)
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def _save_rows(store, rows, report_id, fetched_at):
    stats = {"rows": 0, "created": 0, "updated": 0, "skipped": 0}
    seen = {}
    for row in sorted(rows, key=lambda item: (
        _value(item, "shipment-id"), _value(item, "shipment-item-id"),
        _value(item, "amazon-order-id"), _value(item, "sku"),
        json.dumps(item["source"], sort_keys=True, ensure_ascii=False),
    )):
        order_id = _value(row, "amazon-order-id")
        sku = _value(row, "sku")
        shipment_id = _value(row, "shipment-id")
        base = (shipment_id, _value(row, "shipment-item-id"), order_id,
                _value(row, "amazon-order-item-id"), sku, _value(row, "shipment-date"))
        seen[base] = seen.get(base, 0) + 1
        key = _identity(store, row, seen[base])
        values = {
            "amazon_store": store.name, "marketplace_id": store.marketplace_id,
            "cost_center": store.cost_center, "order_id": order_id,
            "merchant_order_id": _value(row, "merchant-order-id"),
            "shipment_id": shipment_id,
            "shipment_item_id": _value(row, "shipment-item-id"),
            "amazon_order_item_id": _value(row, "amazon-order-item-id"),
            "seller_sku": sku, "product_name": _value(row, "product-name"),
            "quantity_shipped": cint(_value(row, "quantity-shipped")),
            "fulfillment_center_id": _value(row, "fulfillment-center-id"),
            "purchase_at": _datetime(_value(row, "purchase-date")),
            "shipment_at": _datetime(_value(row, "shipment-date")),
            "reporting_at": _datetime(_value(row, "reporting-date")),
            "fulfillment_channel": _value(row, "fulfillment-channel"),
            "sales_channel": _value(row, "sales-channel"),
            "currency": _value(row, "currency"),
            "item_price": flt(_value(row, "item-price")),
            "shipping_price": flt(_value(row, "shipping-price")),
            "carrier": _value(row, "carrier"),
            "tracking_number": _value(row, "tracking-number"),
            "report_id": report_id, "fetched_at": fetched_at,
            "raw_json": json.dumps(row["source"], ensure_ascii=False),
        }
        existing = frappe.db.get_value(RECORD_DOCTYPE, {"shipment_key": key}, "name")
        if existing:
            frappe.db.set_value(RECORD_DOCTYPE, existing, values, update_modified=False)
            stats["updated"] += 1
        else:
            frappe.get_doc({"doctype": RECORD_DOCTYPE, "shipment_key": key, **values}).insert(ignore_permissions=True)
            stats["created"] += 1
        stats["rows"] += 1
    return stats


def _reserve_report_request(store):
    cache = frappe.cache()
    seller = str(store.seller_id or store.name)
    key = f"fengjing:amazon-fba-shipments:requests:{seller}"
    with cache.lock(cache.make_key(f"{key}:lock"), timeout=30, blocking_timeout=30):
        now = time.time()
        requests = [float(t) for t in (cache.get_value(key) or [])
                    if now - float(t) < REQUEST_WINDOW_SECONDS]
        if len(requests) >= REQUEST_LIMIT:
            retry_at = datetime.fromtimestamp(requests[0] + REQUEST_WINDOW_SECONDS + 60, timezone.utc)
            raise QuotaDeferred(_to_system(retry_at))
        # 在调用 createReport 前预留名额，失败的请求也计入安全上限。
        requests.append(now)
        cache.set_value(key, requests, expires_in_sec=REQUEST_WINDOW_SECONDS + 120)


def _fetch_window(store, start, end):
    _reserve_report_request(store)
    try:
        report_id = create_amazon_report(
            store, REPORT_TYPE, marketplace_ids=[store.marketplace_id],
            data_start_time=_amazon_time(start), data_end_time=_amazon_time(end),
        )
    except AmazonAPIError as exc:
        if exc.status_code == 429:
            delay = max(cint(exc.retry_after_seconds), REQUEST_WINDOW_SECONDS)
            raise QuotaDeferred(_to_system(datetime.now(timezone.utc) + timedelta(seconds=delay))) from exc
        raise
    metadata = wait_for_amazon_report(store, report_id, timeout_seconds=3600, poll_seconds=15)
    if str(metadata.get("processingStatus") or "").upper() == "CANCELLED":
        return report_id, {"rows": 0, "created": 0, "updated": 0, "skipped": 0, "no_data": True}
    text, _ = download_amazon_report(store, metadata["reportDocumentId"])
    ensure_database_connection()
    rows = _parse_rows(text)
    if rows and not {"amazonorderid", "sku", "shipmentid"}.issubset(rows[0]["indexed"]):
        raise ValueError("FBA fulfilled shipments report is missing expected columns")
    stats = _save_rows(store, rows, report_id, now_datetime())
    return report_id, stats


def _task_lock(name):
    cache = frappe.cache()
    return cache.lock(cache.make_key(f"fengjing:amazon-fba-shipments:{name}"),
                      timeout=TASK_TIMEOUT, blocking_timeout=0)


def restore_full_source_rows(configuration_name):
    """Re-read existing reports to restore columns omitted from previously saved rows."""
    config = _configuration(configuration_name)
    store = get_store(config.amazon_store)
    lock = _task_lock(configuration_name)
    if not lock.acquire(blocking=False):
        return {"status": "busy"}
    result = {"reports": 0, "rows": 0, "errors": []}
    try:
        report_ids = [row[0] for row in frappe.db.sql(
            "SELECT DISTINCT report_id FROM `tabAmazon FBA Fulfilled Shipment` "
            "WHERE amazon_store = %s AND report_id IS NOT NULL AND report_id != ''",
            (store.name,),
        )]
        for report_id in report_ids:
            try:
                metadata = wait_for_amazon_report(store, report_id, timeout_seconds=60)
                if str(metadata.get("processingStatus") or "").upper() != "DONE":
                    result["errors"].append(f"{report_id}: report unavailable")
                    continue
                text, _ = download_amazon_report(store, metadata["reportDocumentId"])
                rows = _parse_rows(text)
                stats = _save_rows(store, rows, report_id, now_datetime())
                frappe.db.commit()
                result["reports"] += 1
                result["rows"] += stats["rows"]
            except Exception as exc:
                frappe.db.rollback()
                result["errors"].append(f"{report_id}: {type(exc).__name__}: {str(exc)[:120]}")
        return result
    finally:
        lock.release()


def _next_recheck(now, interval_days):
    return get_datetime(now).replace(hour=2, minute=59, second=0, microsecond=0) + timedelta(days=max(cint(interval_days), 1))


def _first_recheck(now):
    candidate = get_datetime(now).replace(hour=2, minute=59, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def execute_shipment_sync(configuration_name, sync_type):
    if sync_type not in {"history", "incremental", *(f"recheck_{days}" for days in RECHECK_DAYS)}:
        raise ValueError(f"Unsupported FBA shipment task: {sync_type}")
    lock = _task_lock(configuration_name)
    if not lock.acquire(blocking=False):
        return {"status": "busy"}
    summary = {"windows": 0, "rows": 0, "created": 0, "updated": 0, "skipped": 0, "empty_windows": 0}
    try:
        _update_configuration(configuration_name, current_task_status="Running",
                              current_execution_type=sync_type, current_task_started_at=now_datetime(),
                              last_error="", **({"history_status": "Running", "history_last_error": ""} if sync_type == "history" else {}))
        config = _configuration(configuration_name)
        store = get_store(config.amazon_store)
        if not cint(config.enabled):
            raise ValueError("FBA shipment configuration is disabled")
        if sync_type == "history":
            if not cint(config.history_18_months):
                raise ValueError("18-month history checkbox is not enabled")
            if not config.history_target_at:
                _update_configuration(configuration_name, history_target_at=now_datetime())
            for _ in range(HISTORY_WINDOWS_PER_JOB):
                config = _configuration(configuration_name)
                window = _history_window(config)
                if not window:
                    break
                start, end, target_start, target_end = window
                report_id, stats = _fetch_window(store, start, end)
                for key in ("rows", "created", "updated", "skipped"):
                    summary[key] += stats[key]
                summary["windows"] += 1
                summary["empty_windows"] += int(stats.get("no_data", False))
                progress = min(100, max(0, (end - target_start).total_seconds() /
                                       (target_end - target_start).total_seconds() * 100))
                _update_configuration(configuration_name, history_checkpoint=_to_system(end),
                                      history_progress=progress, history_summary=json.dumps(summary, ensure_ascii=False),
                                      last_report_id=report_id)
            completed = _history_window(_configuration(configuration_name)) is None
            _update_configuration(configuration_name, history_status="Completed" if completed else "Waiting",
                                  history_progress=100 if completed else _configuration(configuration_name).history_progress,
                                  current_task_status="Success", current_execution_type="",
                                  current_task_completed_at=now_datetime(), last_run_result=json.dumps(summary, ensure_ascii=False))
            return {"status": "completed" if completed else "waiting", "summary": summary}

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=LATEST_LOOKBACK_DAYS if sync_type == "incremental" else int(sync_type.rsplit("_", 1)[1]))
        checkpoint_field = f"recheck_{sync_type.rsplit('_', 1)[1]}_checkpoint" if sync_type.startswith("recheck_") else None
        checkpoint = config.get(checkpoint_field) if checkpoint_field else None
        cursor = max(start, _to_utc(checkpoint)) if checkpoint else start
        last_report_id = ""
        while cursor < end:
            window_end = min(cursor + timedelta(days=WINDOW_DAYS), end)
            last_report_id, stats = _fetch_window(store, cursor, window_end)
            for key in ("rows", "created", "updated", "skipped"):
                summary[key] += stats[key]
            summary["windows"] += 1
            summary["empty_windows"] += int(stats.get("no_data", False))
            frappe.db.commit()
            cursor = window_end
            if checkpoint_field:
                _update_configuration(configuration_name, **{checkpoint_field: _to_system(cursor)})
        finished_at = now_datetime()
        values = {"current_task_status": "Success", "current_execution_type": "",
                  "current_task_completed_at": finished_at, "last_run_result": json.dumps(summary, ensure_ascii=False),
                  "last_report_id": last_report_id, "last_error": "", "rate_limit_until": None}
        if sync_type == "incremental":
            values.update(incremental_checkpoint=_to_system(end), incremental_last_at=finished_at,
                          incremental_next_at=finished_at + timedelta(minutes=max(cint(config.incremental_interval_minutes), 60)),
                          incremental_summary=json.dumps(summary, ensure_ascii=False))
        else:
            days = int(sync_type.rsplit("_", 1)[1])
            values.update({f"recheck_{days}_last_at": finished_at,
                           f"recheck_{days}_checkpoint": None,
                           f"recheck_{days}_next_at": _next_recheck(finished_at, config.get(f"recheck_{days}_interval_days"))})
        _update_configuration(configuration_name, **values)
        return {"status": "success", "summary": summary}
    except QuotaDeferred as exc:
        # 保留已完成历史时间片的 checkpoint，等窗口释放后自动续跑。
        frappe.db.rollback()
        values = {"current_task_status": "Waiting", "current_execution_type": "",
                  "current_task_completed_at": now_datetime(), "rate_limit_until": exc.retry_at,
                  "last_run_result": str(exc), "last_error": ""}
        if sync_type == "history":
            values["history_status"] = "Waiting"
        _update_configuration(configuration_name, **values)
        return {"status": "waiting", "retry_at": str(exc.retry_at)}
    except Exception as exc:
        frappe.db.rollback()
        values = {"current_task_status": "Failed", "current_execution_type": "",
                  "current_task_completed_at": now_datetime(), "last_error": str(exc)[:2000],
                  "last_run_result": f"Failed: {str(exc)[:1800]}"}
        if sync_type == "history":
            values.update(history_status="Failed", history_last_error=str(exc)[:2000])
        elif sync_type == "incremental":
            values["incremental_next_at"] = now_datetime() + timedelta(hours=1)
        else:
            days = int(sync_type.rsplit("_", 1)[1])
            values[f"recheck_{days}_next_at"] = now_datetime() + timedelta(hours=1)
        _update_configuration(configuration_name, **values)
        frappe.logger("amazon_fba_fulfilled_shipments", allow_site=True).exception(
            "FBA shipment sync failed: configuration=%s type=%s", configuration_name, sync_type)
        raise
    finally:
        lock.release()


def run_scheduled_shipment_sync():
    now = now_datetime()
    for name in frappe.get_all(DOCTYPE, filters={"enabled": 1}, pluck="name"):
        try:
            config = _configuration(name)
            get_store(config.amazon_store)
            if config.current_task_status == "Running":
                started = get_datetime(config.current_task_started_at) if config.current_task_started_at else None
                if not started or (now - started).total_seconds() <= TASK_TIMEOUT:
                    continue
                _update_configuration(name, current_task_status="Failed", current_execution_type="",
                                      last_error="Previous task timed out")
                config = _configuration(name)
            if config.rate_limit_until and get_datetime(config.rate_limit_until) > now:
                continue
            # 先补齐首次 90 天核对，取得近期完整月份；完成后恢复原有调度顺序。
            if (cint(config.enable_recheck_90) and not config.recheck_90_last_at
                    and config.recheck_90_next_at
                    and get_datetime(config.recheck_90_next_at) <= now):
                _enqueue(name, "recheck_90")
                continue
            # 近期发货优先；历史回填按每次最多两片逐步补齐。
            if not config.incremental_next_at or get_datetime(config.incremental_next_at) <= now:
                if cint(config.active_start_hour) <= now.hour < (cint(config.active_end_hour) or 24):
                    _enqueue(name, "incremental")
                    continue
            if cint(config.history_18_months) and config.history_status in {"Not Started", "Waiting", "Running"}:
                if _history_window(config):
                    _enqueue(name, "history")
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
                next_at = config.get(f"recheck_{days}_next_at") or initialised.get(f"recheck_{days}_next_at")
                if next_at and get_datetime(next_at) <= now:
                    _enqueue(name, f"recheck_{days}")
                    break
        except Exception:
            frappe.logger("amazon_fba_fulfilled_shipments", allow_site=True).exception(
                "FBA shipment scheduler failed: configuration=%s", name)
