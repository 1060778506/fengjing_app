# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""Ozon financial synchronization driven by the independent configuration."""

import hashlib
import json
from datetime import date, timedelta

import frappe
from frappe.model.document import Document
from frappe.utils import cint, get_datetime, getdate, now_datetime

from fengjing_app.fengjing_business.doctype.ozon_store_configuration.ozon_store_configuration import (
	ensure_database_connection,
	get_store,
	ozon_seller_request,
	response_error_summary,
)


DOCTYPE = "Ozon Financial Configuration"
TASK_TIMEOUT = 12 * 60 * 60
MAX_PAGES = 10000
RECHECK_DAYS = (7, 14, 30, 90, 180)

SYNC_TYPE_LABELS = {
	"history": "历史财务",
	"incremental": "最新财务增量",
	"recheck_7": "7天财务核对",
	"recheck_14": "14天财务核对",
	"recheck_30": "30天财务核对",
	"recheck_90": "90天财务核对",
	"recheck_180": "180天财务核对",
}


class OzonFinancialConfiguration(Document):
	def validate(self):
		if self.ozon_store:
			get_store(self.ozon_store, require_enabled=False)
		self.incremental_interval_minutes = max(cint(self.incremental_interval_minutes), 1)
		self.incremental_lookback_days = max(cint(self.incremental_lookback_days), 1)
		for days in RECHECK_DAYS:
			fieldname = f"finance_recheck_{days}_interval_days"
			self.set(fieldname, max(cint(self.get(fieldname)), 1))
		if self.history_start_date and getdate(self.history_start_date) < date(2022, 1, 1):
			frappe.throw("Ozon financial accrual history starts from 2022-01-01")
		if self.history_start_date and self.history_end_date:
			if getdate(self.history_start_date) > getdate(self.history_end_date):
				frappe.throw("History start date cannot be later than history end date")


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


def _storage_context(store):
	return frappe._dict({"店铺选项": store.cost_center, "ozon_id": store.ozon_id})


def _get_accrual_types(store):
	payload = _request_json(store, "/v1/finance/accrual/types", {}, "financial accrual types API")
	rows = payload.get("accrual_types") or (payload.get("result") or {}).get("accrual_types") or []
	result = {}
	for row in rows:
		if not isinstance(row, dict):
			continue
		type_id = row.get("id", row.get("type_id"))
		if type_id is not None:
			result[str(type_id)] = {
				"name": row.get("name") or "",
				"description": row.get("description") or row.get("name") or "",
			}
	return result


def _empty_summary():
	return {"created": 0, "updated": 0, "archived": 0, "records": 0, "pages": 0, "days": 0}


def _add_save_result(summary, result):
	summary["created"] += cint(result.get("created"))
	summary["updated"] += cint(result.get("updated"))
	summary["archived"] += cint(result.get("archived"))


def _fetch_accrual_day(store, context, target_date, sync_label, type_map):
	from fengjing_app.fengjing_business.doctype.ozon_financial_storage.ozon_financial_storage import (
		保存ozon财务交易,
	)

	summary = _empty_summary()
	last_id = ""
	seen_ids = set()
	for _ in range(MAX_PAGES):
		payload = _request_json(
			store,
			"/v1/finance/accrual/by-day",
			{"date": getdate(target_date).isoformat(), "last_id": last_id},
			"daily financial accrual API",
		)
		accruals = payload.get("accruals") or (payload.get("result") or {}).get("accruals") or []
		if not isinstance(accruals, list):
			raise RuntimeError("Ozon daily financial accrual API accruals field is not a list")
		for accrual in accruals:
			_add_save_result(summary, 保存ozon财务交易(accrual, context, type_map, sync_label))
		summary["records"] += len(accruals)
		summary["pages"] += 1
		frappe.db.commit()
		next_id = str(
			payload.get("last_id") or (payload.get("result") or {}).get("last_id") or ""
		).strip()
		if not next_id or not accruals:
			break
		if next_id == last_id or next_id in seen_ids:
			raise RuntimeError(f"Ozon financial pagination cursor did not advance for {target_date}")
		seen_ids.add(next_id)
		last_id = next_id
	else:
		raise RuntimeError(f"Ozon financial API exceeded the maximum page count for {target_date}")
	return summary


def _sync_date_range(store, context, start_date, end_date, sync_label, type_map, daily_callback=None):
	summary = _empty_summary()
	cursor = getdate(start_date)
	end = getdate(end_date)
	while cursor <= end:
		day_summary = _fetch_accrual_day(store, context, cursor, sync_label, type_map)
		for key in ("created", "updated", "archived", "records", "pages"):
			summary[key] += day_summary[key]
		summary["days"] += 1
		if daily_callback:
			daily_callback(cursor, summary)
		cursor += timedelta(days=1)
	return summary


def _history_progress(config, completed_date):
	start = getdate(config.history_start_date)
	end = getdate(config.history_end_date)
	total_days = max((end - start).days + 1, 1)
	completed_days = max(min((getdate(completed_date) - start).days + 1, total_days), 0)
	return round(completed_days / total_days * 100, 2)


def _next_recheck(now, interval_days):
	return get_datetime(now).replace(hour=2, minute=59, second=0, microsecond=0) + timedelta(
		days=max(cint(interval_days), 1)
	)


def _first_recheck(now):
	now = get_datetime(now)
	candidate = now.replace(hour=2, minute=59, second=0, microsecond=0)
	return candidate if candidate > now else candidate + timedelta(days=1)


def _task_lock(name):
	return frappe.cache().lock(
		frappe.cache().make_key(f"fengjing:ozon-financial-config:{name}"),
		timeout=TASK_TIMEOUT,
		blocking_timeout=0,
	)


def _enqueue(name, sync_type):
	digest = hashlib.sha256(f"{name}|{sync_type}".encode("utf-8")).hexdigest()[:20]
	frappe.enqueue(
		execute_financial_sync,
		queue="long",
		timeout=TASK_TIMEOUT,
		enqueue_after_commit=True,
		job_id=f"ozon-financial-config-{digest}",
		deduplicate=True,
		configuration_name=name,
		sync_type=sync_type,
	)


@frappe.whitelist()
def start_history_sync(name):
	config = _get_configuration(name)
	if not cint(config.enabled):
		frappe.throw("Please enable financial sync first")
	if not config.history_start_date or not config.history_end_date:
		frappe.throw("Please set the history start and end dates first")
	get_store(config.ozon_store)
	values = {
		"history_status": "Waiting",
		"current_task_status": "Waiting",
		"current_execution_type": "history",
		"history_last_error": "",
		"last_error": "",
	}
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
	return {"status": "queued", "message": "Ozon financial history sync has been queued"}


@frappe.whitelist()
def start_incremental_sync(name):
	config = _get_configuration(name)
	if not cint(config.enabled):
		frappe.throw("Please enable financial sync first")
	get_store(config.ozon_store)
	_update_configuration(
		name,
		current_task_status="Waiting",
		current_execution_type="incremental",
		last_error="",
	)
	_enqueue(name, "incremental")
	return {"status": "queued", "message": "Ozon financial incremental sync has been queued"}


@frappe.whitelist()
def start_recheck_sync(name, days):
	days = cint(days)
	if days not in RECHECK_DAYS:
		frappe.throw("Unsupported Ozon financial recheck range")
	config = _get_configuration(name)
	if not cint(config.enabled):
		frappe.throw("Please enable financial sync first")
	get_store(config.ozon_store)
	_update_configuration(
		name,
		current_task_status="Waiting",
		current_execution_type=f"recheck_{days}",
		last_error="",
	)
	_enqueue(name, f"recheck_{days}")
	return {"status": "queued", "message": f"Ozon financial {days}-day recheck has been queued"}


def execute_financial_sync(configuration_name, sync_type):
	if sync_type not in SYNC_TYPE_LABELS:
		raise ValueError(f"Unsupported Ozon financial sync type: {sync_type}")
	lock = _task_lock(configuration_name)
	if not lock.acquire(blocking=False):
		return {"status": "busy", "message": "This Ozon financial configuration is already running"}

	try:
		_update_configuration(
			configuration_name,
			current_task_status="Running",
			current_execution_type=sync_type,
			current_task_started_at=now_datetime(),
			last_error="",
			**({"history_status": "Running", "history_last_error": ""} if sync_type == "history" else {}),
		)
		config = _get_configuration(configuration_name)
		if not cint(config.enabled):
			raise ValueError("Ozon financial synchronization is disabled")
		store = get_store(config.ozon_store)
		context = _storage_context(store)

		type_map = _get_accrual_types(store)
		if sync_type == "history":
			if not config.history_start_date or not config.history_end_date:
				raise ValueError("History start and end dates are required")
			start = getdate(config.history_start_date)
			end = getdate(config.history_end_date)
			cursor = getdate(config.history_checkpoint) + timedelta(days=1) if config.history_checkpoint else start
			available_end = min(end, getdate())
			if cursor > available_end:
				completed = cursor > end
				_update_configuration(
					configuration_name,
					history_status="Completed" if completed else "Waiting",
					history_progress=100 if completed else config.history_progress,
					current_task_status="Success" if completed else "Waiting",
					current_execution_type="",
					current_task_completed_at=now_datetime(),
				)
				return {"status": "complete" if completed else "waiting", "summary": _empty_summary()}

			summary = _empty_summary()
			summary["created"] = cint(config.history_inserted_count)
			summary["updated"] = cint(config.history_updated_count)
			summary["archived"] = cint(config.get("history_archived_count"))

			def daily_completed(completed_date, running_summary):
				_update_configuration(
					configuration_name,
					history_checkpoint=completed_date,
					history_progress=_history_progress(config, completed_date),
					history_inserted_count=summary["created"] + running_summary["created"],
					history_updated_count=summary["updated"] + running_summary["updated"],
					history_archived_count=summary["archived"] + running_summary["archived"],
					history_summary=json.dumps(running_summary, ensure_ascii=False),
				)

			run_summary = _sync_date_range(
				store,
				context,
				cursor,
				available_end,
				SYNC_TYPE_LABELS[sync_type],
				type_map,
				daily_callback=daily_completed,
			)
			for key in ("created", "updated", "archived", "records", "pages", "days"):
				summary[key] += run_summary[key]
			completed = available_end >= end
			text = json.dumps({"status": "Success", **summary}, ensure_ascii=False)
			_update_configuration(
				configuration_name,
				history_status="Completed" if completed else "Waiting",
				history_progress=100 if completed else _history_progress(config, available_end),
				history_inserted_count=summary["created"],
				history_updated_count=summary["updated"],
				history_archived_count=summary["archived"],
				history_summary=text,
				current_task_status="Success" if completed else "Waiting",
				current_execution_type="",
				current_task_completed_at=now_datetime(),
				last_run_result=text,
				history_last_error="",
				last_error="",
			)
			return {"status": "success", "summary": summary}

		today_date = getdate()
		if sync_type == "incremental":
			days = max(cint(config.incremental_lookback_days), 1)
		else:
			days = int(sync_type.rsplit("_", 1)[1])
		start_date = today_date - timedelta(days=days - 1)
		summary = _sync_date_range(
			store,
			context,
			start_date,
			today_date,
			SYNC_TYPE_LABELS[sync_type],
			type_map,
		)
		finished_at = now_datetime()
		text = json.dumps({"status": "Success", **summary}, ensure_ascii=False)
		values = {
			"current_task_status": "Success",
			"current_execution_type": "",
			"current_task_completed_at": finished_at,
			"last_run_result": text,
			"last_error": "",
		}
		if sync_type == "incremental":
			values.update(
				{
					"incremental_checkpoint": today_date,
					"incremental_last_at": finished_at,
					"incremental_next_at": finished_at
					+ timedelta(minutes=max(cint(config.incremental_interval_minutes), 1)),
					"incremental_summary": text,
				}
			)
		else:
			days = int(sync_type.rsplit("_", 1)[1])
			values.update(
				{
					f"finance_recheck_{days}_last_at": finished_at,
					f"finance_recheck_{days}_next_at": _next_recheck(
						finished_at, config.get(f"finance_recheck_{days}_interval_days")
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
			"last_run_result": f"Failed: {str(exc)[:1800]}",
			"last_error": str(exc)[:2000],
		}
		if sync_type == "history":
			values.update({"history_status": "Failed", "history_last_error": str(exc)[:2000]})
		elif sync_type == "incremental":
			values.update(
				{
					"incremental_last_at": now,
					"incremental_next_at": now + timedelta(minutes=15),
					"incremental_summary": f"Failed: {str(exc)[:1800]}",
				}
			)
		else:
			days = int(sync_type.rsplit("_", 1)[1])
			values[f"finance_recheck_{days}_next_at"] = now + timedelta(minutes=30)
		_update_configuration(configuration_name, **values)
		frappe.logger("ozon_finance_v2", allow_site=True).exception(
			"Ozon financial sync failed: configuration=%s type=%s", configuration_name, sync_type
		)
		raise
	finally:
		try:
			lock.release()
		except Exception:
			pass


def run_scheduled_financial_sync():
	"""Queue due jobs after this function is explicitly connected to hooks later."""
	now = now_datetime()
	for name in frappe.get_all(DOCTYPE, filters={"enabled": 1}, pluck="name"):
		try:
			config = _get_configuration(name)
			get_store(config.ozon_store)
			if config.current_task_status == "Waiting":
				waiting_type = str(config.current_execution_type or "").strip()
				if waiting_type in SYNC_TYPE_LABELS:
					_enqueue(name, waiting_type)
					continue
				_update_configuration(name, current_task_status="Idle", current_execution_type="")
				config = _get_configuration(name)
			if config.current_task_status == "Running":
				started = get_datetime(config.current_task_started_at) if config.current_task_started_at else None
				if not started or (now - started).total_seconds() < TASK_TIMEOUT:
					continue
				_update_configuration(
					name,
					current_task_status="Failed",
					current_execution_type="",
					current_task_completed_at=now,
					last_error="Previous task exceeded twelve hours and was released by the scheduler",
				)
				config = _get_configuration(name)

			if (
				config.history_status in {"Waiting", "Running"}
				and config.history_start_date
				and config.history_end_date
			):
				cursor = (
					getdate(config.history_checkpoint) + timedelta(days=1)
					if config.history_checkpoint
					else getdate(config.history_start_date)
				)
				configured_end = getdate(config.history_end_date)
				if cursor <= min(configured_end, getdate()) or cursor > configured_end:
					_enqueue(name, "history")
					continue

			if (
				not config.incremental_next_at or get_datetime(config.incremental_next_at) <= now
			):
				_enqueue(name, "incremental")
				continue

			initialized = {}
			for days in RECHECK_DAYS:
				if cint(config.get(f"finance_enable_recheck_{days}")) and not config.get(
					f"finance_recheck_{days}_next_at"
				):
					initialized[f"finance_recheck_{days}_next_at"] = _first_recheck(now)
			if initialized:
				_update_configuration(name, **initialized)
				continue

			queued = False
			for days in RECHECK_DAYS:
				if not cint(config.get(f"finance_enable_recheck_{days}")):
					continue
				next_at = config.get(f"finance_recheck_{days}_next_at")
				if next_at and get_datetime(next_at) <= now:
					_enqueue(name, f"recheck_{days}")
					queued = True
					break
			if queued:
				continue

		except Exception:
			frappe.logger("ozon_finance_v2", allow_site=True).exception(
				"Ozon financial scheduler failed: configuration=%s", name
			)


启动Ozon历史财务同步 = start_history_sync
启动Ozon最新财务同步 = start_incremental_sync
定时执行Ozon财务同步 = run_scheduled_financial_sync

