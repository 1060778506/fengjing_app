# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""Amazon Finances API synchronization driven by Amazon Financial Configuration."""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe.model.document import Document
from frappe.utils import cint, get_datetime, get_system_timezone, now_datetime

from fengjing_app.fengjing_business.doctype.amazon_store_configuration.amazon_store_configuration import (
	amazon_api_request,
	ensure_database_connection,
	get_storage_region,
	get_store,
)


DOCTYPE = "Amazon Financial Configuration"
TASK_TIMEOUT = 6 * 60 * 60
SAFE_DELAY_MINUTES = 3
OVERLAP_MINUTES = 10
MAX_HISTORY_SEGMENT_DAYS = 179
RECHECK_DAYS = (7, 14, 30, 90, 180)

SYNC_TYPE_LABELS = {
	"history": "历史交易",
	"incremental": "15分钟增量",
	"recheck_7": "7天核对",
	"recheck_14": "14天核对",
	"recheck_30": "30天核对",
	"recheck_90": "90天核对",
	"recheck_180": "180天核对",
}


class AmazonFinancialConfiguration(Document):
	def validate(self):
		if self.amazon_store:
			get_store(self.amazon_store, require_enabled=False)
		self.incremental_interval_minutes = max(cint(self.incremental_interval_minutes), 1)
		self.incremental_lookback_hours = max(cint(self.incremental_lookback_hours), 1)
		self.history_segment_days = min(
			max(cint(self.history_segment_days), 1), MAX_HISTORY_SEGMENT_DAYS
		)
		for days in RECHECK_DAYS:
			fieldname = f"recheck_{days}_interval_days"
			self.set(fieldname, max(cint(self.get(fieldname)), 1))
		if self.history_start_datetime and self.history_end_datetime:
			if get_datetime(self.history_end_datetime) <= get_datetime(self.history_start_datetime):
				frappe.throw("History end datetime must be later than history start datetime")


def _to_utc(value):
	if not value:
		return None
	dt = get_datetime(value)
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=ZoneInfo(get_system_timezone()))
	return dt.astimezone(timezone.utc)


def _to_system(value):
	if not value:
		return None
	if value.tzinfo is None:
		value = value.replace(tzinfo=timezone.utc)
	return value.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)


def _amazon_datetime(value):
	return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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


def _summary_text(status, summary):
	return json.dumps({"status": status, **summary}, ensure_ascii=False, default=str)


def _plan_history(config, current_utc=None):
	if not config.history_start_datetime or not config.history_end_datetime:
		raise ValueError("History start datetime and history end datetime are required")
	start = _to_utc(config.history_start_datetime)
	target_end = _to_utc(config.history_end_datetime)
	if target_end <= start:
		raise ValueError("History end datetime must be later than history start datetime")
	now_utc = current_utc or datetime.now(timezone.utc)
	safe_end = now_utc.astimezone(timezone.utc) - timedelta(minutes=SAFE_DELAY_MINUTES)
	available_end = min(target_end, safe_end)
	cursor = _to_utc(config.history_checkpoint) or start
	cursor = max(cursor, start)
	total_seconds = max((target_end - start).total_seconds(), 1)
	progress = min(max((cursor - start).total_seconds() / total_seconds * 100, 0), 100)
	if cursor >= available_end:
		return {
			"status": "waiting" if target_end > safe_end else "complete",
			"progress": progress,
			"cursor": cursor,
			"target_end": target_end,
		}
	segment_days = min(max(cint(config.history_segment_days), 1), MAX_HISTORY_SEGMENT_DAYS)
	window_end = min(available_end, cursor + timedelta(days=segment_days))
	return {
		"status": "ready",
		"start": cursor,
		"end": window_end,
		"target_start": start,
		"target_end": target_end,
		"progress": progress,
	}


def _query_window(config, store, sync_type, start_utc, end_utc):
	from fengjing_app.fengjing_business.doctype.amazon_financial_transaction.amazon_financial_transaction import (
		保存亚马逊财务交易,
	)

	marketplace_id = str(store.marketplace_id or "").strip().upper()
	base_params = {
		"postedAfter": _amazon_datetime(start_utc),
		"postedBefore": _amazon_datetime(end_utc),
		"marketplaceId": marketplace_id,
	}
	next_token = None
	seen_tokens = set()
	summary = {"created": 0, "updated": 0, "unchanged": 0, "transactions": 0, "pages": 0}
	while True:
		params = dict(base_params)
		if next_token:
			params["nextToken"] = next_token
		response = amazon_api_request(
			store,
			"finances",
			"GET",
			"/finances/2024-06-19/transactions",
			params=params,
			timeout=60,
		)
		ensure_database_connection()
		if response is None or response.status_code != 200:
			status = response.status_code if response is not None else "no response"
			message = response.text[:1200] if response is not None else "Amazon returned no response"
			if status == 403:
				message += "; verify that Finance and Accounting role is approved"
			raise RuntimeError(f"Finances API failed (HTTP {status}): {message}")
		body = response.json() or {}
		payload = body.get("payload") if isinstance(body.get("payload"), dict) else body
		for transaction in payload.get("transactions") or []:
			result = 保存亚马逊财务交易(
				transaction,
				store.cost_center,
				marketplace_id,
				get_storage_region(store),
				SYNC_TYPE_LABELS[sync_type],
			)
			summary["created"] += result["created"]
			summary["updated"] += result["updated"]
			summary["unchanged"] += result["unchanged"]
			summary["transactions"] += 1
		frappe.db.commit()
		summary["pages"] += 1
		next_token = payload.get("nextToken")
		if not next_token:
			break
		if next_token in seen_tokens:
			raise RuntimeError("Finances API returned a repeated nextToken")
		seen_tokens.add(next_token)
	return summary


def _task_lock(name):
	return frappe.cache().lock(
		frappe.cache().make_key(f"fengjing:amazon-financial-config:{name}"),
		timeout=TASK_TIMEOUT,
		blocking_timeout=0,
	)


def _enqueue(name, sync_type):
	frappe.enqueue(
		execute_financial_sync,
		queue="long",
		timeout=TASK_TIMEOUT,
		enqueue_after_commit=True,
		job_id=f"amazon-financial-config-{name}-{sync_type}",
		deduplicate=True,
		configuration_name=name,
		sync_type=sync_type,
	)


@frappe.whitelist()
def start_history_sync(name):
	config = _get_configuration(name)
	if not config.history_start_datetime or not config.history_end_datetime:
		frappe.throw("Please set the history start and end datetime first")
	get_store(config.amazon_store)
	values = {
		"history_status": "Waiting",
		"current_execution_type": "history",
		"last_error": "",
		"history_last_error": "",
	}
	# Clicking again after completion intentionally rechecks the full configured range.
	if config.history_status == "Completed":
		values.update({"history_checkpoint": None, "history_progress": 0, "history_summary": ""})
	_update_configuration(name, **values)
	_enqueue(name, "history")
	return {"status": "queued", "message": "Amazon financial history sync has been queued"}


@frappe.whitelist()
def start_incremental_sync(name):
	config = _get_configuration(name)
	if not cint(config.enabled):
		frappe.throw("Please enable financial sync first")
	get_store(config.amazon_store)
	_enqueue(name, "incremental")
	return {"status": "queued", "message": "Amazon financial incremental sync has been queued"}


def _next_recheck(now, interval_days):
	return get_datetime(now).replace(hour=2, minute=59, second=0, microsecond=0) + timedelta(
		days=max(cint(interval_days), 1)
	)


def _first_recheck(now):
	now = get_datetime(now)
	candidate = now.replace(hour=2, minute=59, second=0, microsecond=0)
	return candidate if candidate > now else candidate + timedelta(days=1)


def execute_financial_sync(configuration_name, sync_type):
	if sync_type not in SYNC_TYPE_LABELS:
		raise ValueError(f"Unsupported Amazon financial sync type: {sync_type}")
	lock = _task_lock(configuration_name)
	if not lock.acquire(blocking=False):
		return {"status": "busy", "message": "This Amazon financial configuration is already running"}
	started_at = now_datetime()
	summary = {
		"created": 0,
		"updated": 0,
		"unchanged": 0,
		"transactions": 0,
		"pages": 0,
		"windows": 0,
	}
	try:
		_update_configuration(
			configuration_name,
			current_task_status="Running",
			current_execution_type=sync_type,
			current_task_started_at=started_at,
			last_error="",
			**({"history_status": "Running", "history_last_error": ""} if sync_type == "history" else {}),
		)
		config = _get_configuration(configuration_name)
		store = get_store(config.amazon_store)

		if sync_type == "history":
			while True:
				config = _get_configuration(configuration_name)
				plan = _plan_history(config)
				if plan["status"] != "ready":
					status = "Completed" if plan["status"] == "complete" else "Waiting"
					text = _summary_text(status, summary)
					_update_configuration(
						configuration_name,
						history_status=status,
						history_progress=100 if status == "Completed" else plan["progress"],
						history_summary=text,
						current_task_status="Success" if status == "Completed" else "Waiting",
						current_execution_type="",
						current_task_completed_at=now_datetime(),
						last_run_result=text,
					)
					return {"status": plan["status"], "summary": summary}
				window = _query_window(config, store, sync_type, plan["start"], plan["end"])
				for key in ("created", "updated", "unchanged", "transactions", "pages"):
					summary[key] += window[key]
				summary["windows"] += 1
				total = max((plan["target_end"] - plan["target_start"]).total_seconds(), 1)
				progress = min(max((plan["end"] - plan["target_start"]).total_seconds() / total * 100, 0), 100)
				_update_configuration(
					configuration_name,
					history_checkpoint=_to_system(plan["end"]),
					history_progress=progress,
					history_summary=json.dumps(summary, ensure_ascii=False),
				)

		config = _get_configuration(configuration_name)
		end_utc = datetime.now(timezone.utc) - timedelta(minutes=SAFE_DELAY_MINUTES)
		if sync_type == "incremental":
			if config.incremental_checkpoint:
				start_utc = _to_utc(config.incremental_checkpoint) - timedelta(minutes=OVERLAP_MINUTES)
			else:
				start_utc = end_utc - timedelta(hours=max(cint(config.incremental_lookback_hours), 1))
		else:
			days = int(sync_type.rsplit("_", 1)[1])
			start_utc = end_utc - timedelta(days=days)
		window = _query_window(config, store, sync_type, start_utc, end_utc)
		summary.update(window)
		summary["windows"] = 1
		finished_at = now_datetime()
		values = {
			"current_task_status": "Success",
			"current_execution_type": "",
			"current_task_completed_at": finished_at,
			"last_run_result": _summary_text("Success", summary),
			"last_error": "",
		}
		if sync_type == "incremental":
			values.update(
				{
					"incremental_checkpoint": _to_system(end_utc),
					"incremental_last_at": finished_at,
					"incremental_next_at": finished_at
					+ timedelta(minutes=max(cint(config.incremental_interval_minutes), 1)),
					"incremental_summary": json.dumps(summary, ensure_ascii=False),
				}
			)
		else:
			days = int(sync_type.rsplit("_", 1)[1])
			values.update(
				{
					f"recheck_{days}_last_at": finished_at,
					f"recheck_{days}_next_at": _next_recheck(
						finished_at, config.get(f"recheck_{days}_interval_days")
					),
				}
			)
		_update_configuration(configuration_name, **values)
		return {"status": "success", "summary": summary}
	except Exception as exc:
		values = {
			"current_task_status": "Failed",
			"current_execution_type": "",
			"current_task_completed_at": now_datetime(),
			"last_error": str(exc)[:2000],
			"last_run_result": f"Failed: {str(exc)[:1800]}",
		}
		if sync_type == "history":
			values.update({"history_status": "Failed", "history_last_error": str(exc)[:2000]})
		_update_configuration(configuration_name, **values)
		frappe.logger("amazon_finances", allow_site=True).exception(
			"Amazon financial sync failed: configuration=%s type=%s", configuration_name, sync_type
		)
		raise
	finally:
		try:
			lock.release()
		except Exception:
			pass


def run_scheduled_financial_sync():
	now = now_datetime()
	for name in frappe.get_all(DOCTYPE, filters={"enabled": 1}, pluck="name"):
		try:
			config = _get_configuration(name)
			get_store(config.amazon_store)
			if config.current_task_status == "Running":
				started_at = get_datetime(config.current_task_started_at) if config.current_task_started_at else None
				if started_at and (now - started_at).total_seconds() > TASK_TIMEOUT:
					_update_configuration(
						name,
						current_task_status="Failed",
						current_execution_type="",
						current_task_completed_at=now,
						last_error="Previous task exceeded six hours and was released by the scheduler",
					)
					config = _get_configuration(name)
				else:
					continue
			if config.history_status in {"Waiting", "Running"} and config.history_start_datetime and config.history_end_datetime:
				plan = _plan_history(config)
				if plan["status"] in {"ready", "complete"}:
					_enqueue(name, "history")
					continue
				# A future history end boundary must not block daily incremental/recheck tasks.
			if not config.incremental_next_at or get_datetime(config.incremental_next_at) <= now:
				_enqueue(name, "incremental")
				continue
			initialized = {}
			for days in RECHECK_DAYS:
				if cint(config.get(f"enable_recheck_{days}")) and not config.get(f"recheck_{days}_next_at"):
					initialized[f"recheck_{days}_next_at"] = _first_recheck(now)
			if initialized:
				_update_configuration(name, **initialized)
				continue
			for days in RECHECK_DAYS:
				if not cint(config.get(f"enable_recheck_{days}")):
					continue
				next_at = config.get(f"recheck_{days}_next_at")
				if next_at and get_datetime(next_at) <= now:
					_enqueue(name, f"recheck_{days}")
					break
		except Exception:
			frappe.logger("amazon_finances", allow_site=True).exception(
				"Amazon financial scheduler failed: configuration=%s", name
			)


# Chinese aliases retained for later custom configuration-page buttons.
启动亚马逊历史财务交易同步 = start_history_sync
启动亚马逊最新财务交易同步 = start_incremental_sync
定时执行亚马逊财务交易同步 = run_scheduled_financial_sync
