# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""Independent Ozon settlement-statement synchronization configuration."""

import hashlib
import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe.model.document import Document
from frappe.utils import cint, get_datetime, get_system_timezone, getdate, now_datetime

from fengjing_app.fengjing_business.doctype.ozon_settlement_statement.ozon_settlement_statement import (
	save_settlement_statement,
)
from fengjing_app.fengjing_business.doctype.ozon_store_configuration.ozon_store_configuration import (
	ensure_database_connection,
	get_store,
	ozon_seller_request,
	response_error_summary,
)


DOCTYPE = "Ozon Settlement Statement Configuration"
TASK_TIMEOUT = 6 * 60 * 60
MAX_PAGES = 10000
HISTORY_WINDOW_DAYS = 366


class OzonSettlementStatementConfiguration(Document):
	def validate(self):
		if self.ozon_store:
			get_store(self.ozon_store, require_enabled=False)
		self.sync_interval_hours = max(cint(self.sync_interval_hours), 1)
		self.latest_lookback_periods = max(cint(self.latest_lookback_periods or 6), 1)
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


def _utc_boundary(value, end_of_day=False):
	day = getdate(value)
	local_time = time.max if end_of_day else time.min
	local = datetime.combine(day, local_time).replace(tzinfo=ZoneInfo(get_system_timezone()))
	return local.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _request_json(store, body):
	response = ozon_seller_request(
		store,
		"POST",
		"/v1/finance/cash-flow-statement/list",
		json_data=body,
		timeout=90,
	)
	ensure_database_connection()
	if response is None or response.status_code != 200:
		status = response.status_code if response is not None else "no response"
		raise RuntimeError(
			f"Ozon cash flow statement API failed (HTTP {status}): {response_error_summary(response, 1800)}"
		)
	try:
		payload = response.json()
	except ValueError as exc:
		raise RuntimeError("Ozon cash flow statement API returned invalid JSON") from exc
	if not isinstance(payload, dict):
		raise RuntimeError("Ozon cash flow statement API returned an unexpected structure")
	return payload


def _empty_summary():
	return {"created": 0, "updated": 0, "archived": 0, "reports": 0, "pages": 0, "windows": 0}


def _merge_summary(target, source):
	for key in target:
		target[key] += cint(source.get(key))


def _sync_range(store, start_date, end_date, sync_type):
	summary = _empty_summary()
	for page in range(1, MAX_PAGES + 1):
		payload = _request_json(
			store,
			{
				"page": page,
				"page_size": 100,
				"date": {
					"from": _utc_boundary(start_date),
					"to": _utc_boundary(end_date, end_of_day=True),
				},
				"with_details": True,
			},
		)
		result = payload.get("result") or {}
		details = result.get("details") or []
		if isinstance(details, dict):
			details = [details]
		if not isinstance(details, list):
			raise RuntimeError("Ozon cash flow statement API details field is not a list")
		for row in details:
			saved = save_settlement_statement(row, store, sync_type)
			summary["created"] += cint(saved.get("created"))
			summary["updated"] += cint(saved.get("updated"))
			summary["archived"] += cint(saved.get("archived"))
		summary["reports"] += len(details)
		summary["pages"] += 1
		frappe.db.commit()
		page_count = cint(result.get("page_count"))
		if not details or page_count <= page:
			break
	else:
		raise RuntimeError("Ozon cash flow statement API exceeded the maximum page count")
	summary["windows"] = 1
	return summary


def _history_progress(config, completed_date):
	start = getdate(config.history_start_date)
	end = getdate(config.history_end_date)
	total_days = max((end - start).days + 1, 1)
	completed_days = max(min((getdate(completed_date) - start).days + 1, total_days), 0)
	return round(completed_days / total_days * 100, 2)


def _task_lock(name):
	return frappe.cache().lock(
		frappe.cache().make_key(f"fengjing:ozon-settlement-config:{name}"),
		timeout=TASK_TIMEOUT,
		blocking_timeout=0,
	)


def _enqueue(name, sync_type):
	digest = hashlib.sha256(f"{name}|{sync_type}".encode("utf-8")).hexdigest()[:20]
	frappe.enqueue(
		execute_statement_sync,
		queue="long",
		timeout=TASK_TIMEOUT,
		enqueue_after_commit=True,
		job_id=f"ozon-settlement-config-{digest}",
		deduplicate=True,
		configuration_name=name,
		sync_type=sync_type,
	)


@frappe.whitelist()
def start_latest_sync(name):
	config = _get_configuration(name)
	if not cint(config.enabled):
		frappe.throw("Please enable settlement statement sync first")
	get_store(config.ozon_store)
	_update_configuration(
		name,
		current_task_status="Waiting",
		current_execution_type="latest",
		last_error="",
	)
	_enqueue(name, "latest")
	return {"status": "queued", "message": "Ozon latest settlement statements have been queued"}


@frappe.whitelist()
def start_history_sync(name):
	config = _get_configuration(name)
	if not cint(config.enabled):
		frappe.throw("Please enable settlement statement sync first")
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
	return {"status": "queued", "message": "Ozon settlement statement history has been queued"}


def execute_statement_sync(configuration_name, sync_type):
	if sync_type not in {"latest", "history"}:
		raise ValueError(f"Unsupported Ozon settlement statement sync type: {sync_type}")
	lock = _task_lock(configuration_name)
	if not lock.acquire(blocking=False):
		return {"status": "busy", "message": "This Ozon settlement configuration is already running"}
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
			raise ValueError("Ozon settlement statement synchronization is disabled")
		store = get_store(config.ozon_store)

		if sync_type == "latest":
			end_date = getdate()
			start_date = end_date - timedelta(
				days=max(cint(config.latest_lookback_periods or 6), 1) * 16 + 2
			)
			summary = _sync_range(store, start_date, end_date, "Latest Statement")
			finished_at = now_datetime()
			text = json.dumps({"status": "Success", **summary}, ensure_ascii=False)
			_update_configuration(
				configuration_name,
				last_sync_at=finished_at,
				next_sync_at=finished_at + timedelta(hours=max(cint(config.sync_interval_hours), 1)),
				last_sync_result=text,
				current_task_status="Success",
				current_execution_type="",
				current_task_completed_at=finished_at,
				last_run_result=text,
				last_error="",
			)
			return {"status": "success", "summary": summary}

		start = getdate(config.history_start_date)
		end = getdate(config.history_end_date)
		cursor = getdate(config.history_checkpoint) + timedelta(days=1) if config.history_checkpoint else start
		available_end = min(end, getdate())
		summary = _empty_summary()
		summary["created"] = cint(config.history_inserted_count)
		summary["updated"] = cint(config.history_updated_count)
		summary["archived"] = cint(config.history_archived_count)
		while cursor <= available_end:
			window_end = min(cursor + timedelta(days=HISTORY_WINDOW_DAYS - 1), available_end)
			window = _sync_range(store, cursor, window_end, "Historical Statement")
			_merge_summary(summary, window)
			_update_configuration(
				configuration_name,
				history_checkpoint=window_end,
				history_progress=_history_progress(config, window_end),
				history_inserted_count=summary["created"],
				history_updated_count=summary["updated"],
				history_archived_count=summary["archived"],
				history_summary=json.dumps(summary, ensure_ascii=False),
			)
			cursor = window_end + timedelta(days=1)

		completed = cursor > end
		text = json.dumps({"status": "Success", **summary}, ensure_ascii=False)
		_update_configuration(
			configuration_name,
			history_status="Completed" if completed else "Waiting",
			history_progress=100 if completed else _history_progress(config, available_end),
			history_summary=text,
			history_last_error="",
			current_task_status="Success" if completed else "Waiting",
			current_execution_type="",
			current_task_completed_at=now_datetime(),
			last_run_result=text,
			last_error="",
		)
		return {"status": "complete" if completed else "waiting", "summary": summary}
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
		else:
			values.update(
				{
					"last_sync_at": now,
					"next_sync_at": now + timedelta(minutes=30),
					"last_sync_result": f"Failed: {str(exc)[:1800]}",
				}
			)
		_update_configuration(configuration_name, **values)
		frappe.logger("ozon_settlement_v2", allow_site=True).exception(
			"Ozon settlement statement sync failed: configuration=%s type=%s",
			configuration_name,
			sync_type,
		)
		raise
	finally:
		try:
			lock.release()
		except Exception:
			pass


def run_scheduled_statement_sync():
	"""Queue due jobs after this function is explicitly connected to hooks later."""
	now = now_datetime()
	for name in frappe.get_all(DOCTYPE, filters={"enabled": 1}, pluck="name"):
		try:
			config = _get_configuration(name)
			get_store(config.ozon_store)
			if config.current_task_status == "Waiting":
				waiting_type = str(config.current_execution_type or "").strip()
				if waiting_type in {"latest", "history"}:
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
					last_error="Previous task exceeded six hours and was released by the scheduler",
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
			if not config.next_sync_at or get_datetime(config.next_sync_at) <= now:
				_enqueue(name, "latest")
		except Exception:
			frappe.logger("ozon_settlement_v2", allow_site=True).exception(
				"Ozon settlement scheduler failed: configuration=%s", name
			)


启动Ozon最新结算报告同步 = start_latest_sync
启动Ozon历史结算报告同步 = start_history_sync
定时执行Ozon结算报告同步 = run_scheduled_statement_sync
