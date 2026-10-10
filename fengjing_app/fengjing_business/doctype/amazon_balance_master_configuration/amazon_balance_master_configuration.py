# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""Amazon Finances listBalances 历史与当前余额同步。"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, get_system_timezone, getdate, get_datetime, now_datetime

from fengjing_app.fengjing_business.doctype.amazon_store_configuration.amazon_store_configuration import (
	amazon_api_json,
	ensure_database_connection,
	get_region,
	get_store,
)


MASTER_DOCTYPE = "Amazon Balance Master Configuration"
COUNTRY_DOCTYPE = "Amazon Balance Country Configuration"
SNAPSHOT_DOCTYPE = "Amazon Balance Snapshot"
TASK_TIMEOUT = 6 * 60 * 60
MAX_HISTORY_SEGMENT_DAYS = 366
SYNC_TYPE_LABELS = {
	"history": "历史余额",
	"current": "当前余额",
	"routine": "当前余额",
	"recheck": "近期复核",
}


class AmazonBalanceMasterConfiguration(Document):
	def validate(self):
		self.configuration_name = str(self.configuration_name or "").strip()
		self.history_segment_days = min(max(cint(self.history_segment_days), 1), MAX_HISTORY_SEGMENT_DAYS)
		self.record_retention_days = max(cint(self.record_retention_days), 0)
		self.sync_interval_hours = max(cint(self.sync_interval_hours), 1)
		self.recent_recheck_days = min(max(cint(self.recent_recheck_days), 0), 366)
		store = get_store(self.credential_store, require_enabled=False) if self.credential_store else None
		if store:
			self.api_region = get_region(store)
			self.seller_id = str(store.seller_id or "").strip().upper()
			if cint(self.enabled) and not cint(store.enabled):
				frappe.throw(_("启用余额总配置前，请先启用请求凭证店铺。"))
		if cint(self.enabled) and not store:
			frappe.throw(_("启用余额总配置前，请选择请求凭证店铺。"))
		if cint(self.enabled) and (not self.history_start_date or not self.history_end_date):
			frappe.throw(_("启用余额总配置前，请填写历史开始日期和历史结束日期。"))
		if self.history_start_date and self.history_end_date:
			if getdate(self.history_end_date) < getdate(self.history_start_date):
				frappe.throw(_("历史结束日期不能早于历史开始日期。"))


def _master(name):
	ensure_database_connection()
	return frappe.get_doc(MASTER_DOCTYPE, name)


def _country(name):
	ensure_database_connection()
	return frappe.get_doc(COUNTRY_DOCTYPE, name)


def _update(doctype, name, **values):
	ensure_database_connection()
	valid = set(frappe.get_meta(doctype).get_valid_columns())
	values = {key: value for key, value in values.items() if key in valid}
	if values:
		frappe.db.set_value(doctype, name, values, update_modified=False)
		frappe.db.commit()


def _update_countries(configurations, **values):
	for configuration in configurations:
		_update(COUNTRY_DOCTYPE, configuration.name, **values)


def _enabled_group(master_name):
	master = _master(master_name)
	if not cint(master.enabled):
		frappe.throw(_("亚马逊余额总配置尚未启用。"))
	credential_store = get_store(master.credential_store)
	names = frappe.get_all(
		COUNTRY_DOCTYPE,
		filters={"master_configuration": master.name, "enabled": 1},
		pluck="name",
		order_by="name asc",
	)
	configurations = [_country(name) for name in names]
	if not configurations:
		frappe.throw(_("当前余额总配置没有已启用的国家配置。"))
	stores = [get_store(configuration.amazon_store) for configuration in configurations]
	seller_ids = {str(store.seller_id or "").strip().upper() for store in stores}
	seller_ids.add(str(credential_store.seller_id or "").strip().upper())
	regions = {get_region(store) for store in stores}
	regions.add(get_region(credential_store))
	marketplace_ids = [str(store.marketplace_id or "").strip().upper() for store in stores]
	if "" in seller_ids or len(seller_ids) != 1:
		frappe.throw(_("同一个余额总配置只能关联同一个Amazon卖家的店铺。"))
	if len(regions) != 1:
		frappe.throw(_("同一个余额总配置只能关联同一个SP-API区域的店铺。"))
	if len(set(marketplace_ids)) != len(marketplace_ids):
		frappe.throw(_("同一个余额总配置中存在重复的Marketplace ID。"))
	return master, configurations, stores, credential_store


def _json(value, *, pretty=False):
	return json.dumps(
		value,
		ensure_ascii=False,
		sort_keys=True,
		indent=2 if pretty else None,
		separators=None if pretty else (",", ":"),
		default=str,
	)


def _hash(value):
	return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _amount(value):
	if not isinstance(value, dict):
		return None
	amount = value.get("currencyAmount")
	return flt(amount) if amount is not None else None


def _currency(value):
	if not isinstance(value, dict):
		return ""
	return str(value.get("currencyCode") or "").strip().upper()


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


def _flatten_balances(balance, amounts=None):
	amounts = amounts if amounts is not None else {}
	balance_type = str(balance.get("balanceType") or "").strip().upper()
	if balance_type:
		amounts[balance_type] = _amount(balance.get("amount"))
	for child in balance.get("balances") or []:
		if isinstance(child, dict):
			_flatten_balances(child, amounts)
	return amounts


def _response_payload(body):
	return body["payload"] if isinstance(body.get("payload"), dict) else body


def _request_balance_pages(store, marketplace_ids, as_of_date=None):
	# Finances v2024-06-19 follows the OpenAPI CSV collection format for query arrays.
	base_params = {"marketplaceIds": ",".join(marketplace_ids)}
	if as_of_date:
		base_params["asOfDate"] = getdate(as_of_date).isoformat()
	next_token = None
	seen_tokens = set()
	rows = []
	pages = 0
	while True:
		params = dict(base_params)
		if next_token:
			params["nextToken"] = next_token
		body = amazon_api_json(
			store,
			"finances",
			"GET",
			"/finances/2024-06-19/balances",
			params=params,
			timeout=60,
		)
		payload = _response_payload(body)
		rows.extend(row for row in (payload.get("balances") or []) if isinstance(row, dict))
		pages += 1
		next_token = payload.get("nextToken")
		if not next_token:
			break
		if next_token in seen_tokens:
			raise RuntimeError("Finances listBalances返回了重复的nextToken")
		seen_tokens.add(next_token)
	return rows, pages


def _snapshot_identity(master, configuration, as_of_date, account_type, currency_code):
	identity = "|".join(
		(
			master.name,
			configuration.name,
			getdate(as_of_date).isoformat(),
			str(account_type or "").strip(),
			str(currency_code or "").strip().upper(),
		)
	)
	return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _save_snapshot(master, configuration, store, balance, as_of_date, sync_type, fetched_at):
	metadata = balance.get("partnerMetadata") or balance.get("sellingPartnerMetadata") or {}
	account_type = str(metadata.get("accountType") or "").strip()
	amounts = _flatten_balances(balance)
	currency_code = _currency(balance.get("amount"))
	if not currency_code:
		for child in balance.get("balances") or []:
			currency_code = _currency(child.get("amount")) if isinstance(child, dict) else ""
			if currency_code:
				break
	if not currency_code:
		raise RuntimeError(
			f"余额响应缺少币种：marketplace={store.marketplace_id}, account={account_type or '-'}"
		)
	raw_hash = _hash(balance)
	key = _snapshot_identity(master, configuration, as_of_date, account_type, currency_code)
	values = {
		"snapshot_key": key,
		"master_configuration": master.name,
		"country_configuration": configuration.name,
		"amazon_store": store.name,
		"company": store.company,
		"cost_center": store.cost_center,
		"country": store.country,
		"seller_id": str(store.seller_id or "").strip().upper(),
		"marketplace_id": str(store.marketplace_id or "").strip().upper(),
		"api_region": get_region(store),
		"account_type": account_type,
		"as_of_date": getdate(as_of_date),
		"last_updated_time": _system_datetime(balance.get("lastUpdatedTime")),
		"fetched_at": fetched_at,
		"sync_type": SYNC_TYPE_LABELS[sync_type],
		"currency_code": currency_code,
		"total_balance": amounts.get("TOTAL"),
		"available_balance": amounts.get("AVAILABLE"),
		"reserved_balance": amounts.get("RESERVED"),
		"deferred_balance": amounts.get("DEFERRED"),
		"account_level_reserve": amounts.get("ACCOUNT_LEVEL_RESERVE"),
		"raw_json_hash": raw_hash,
		"raw_json": _json(balance, pretty=True),
	}
	existing = frappe.db.get_value(
		SNAPSHOT_DOCTYPE, {"snapshot_key": key}, ["name", "raw_json_hash"], as_dict=True
	)
	if existing:
		if existing.raw_json_hash == raw_hash:
			frappe.db.set_value(
				SNAPSHOT_DOCTYPE,
				existing.name,
				{"fetched_at": fetched_at, "sync_type": SYNC_TYPE_LABELS[sync_type]},
				update_modified=False,
			)
			return "unchanged"
		frappe.db.set_value(SNAPSHOT_DOCTYPE, existing.name, values, update_modified=False)
		return "updated"
	frappe.get_doc({"doctype": SNAPSHOT_DOCTYPE, **values}).insert(ignore_permissions=True)
	return "created"


def _group_balance_rows(rows, configured_marketplaces):
	grouped = {}
	for balance in rows:
		metadata = balance.get("partnerMetadata") or balance.get("sellingPartnerMetadata") or {}
		marketplace_id = str(metadata.get("marketplaceId") or "").strip().upper()
		if marketplace_id not in configured_marketplaces:
			raise RuntimeError(f"余额响应包含未配置的Marketplace ID：{marketplace_id or '空值'}")
		account_type = str(metadata.get("accountType") or "").strip()
		currency_code = _currency(balance.get("amount"))
		key = (marketplace_id, account_type, currency_code)
		if key in grouped:
			raise RuntimeError(f"余额响应包含重复账户：{marketplace_id}/{account_type}/{currency_code}")
		grouped[key] = balance
	return list(grouped.values())


def _sync_date(master, configurations, stores, credential_store, as_of_date, sync_type):
	marketplace_ids = [str(store.marketplace_id or "").strip().upper() for store in stores]
	rows, pages = _request_balance_pages(
		credential_store,
		marketplace_ids,
		as_of_date=as_of_date if sync_type != "current" else None,
	)
	store_by_marketplace = {
		str(store.marketplace_id or "").strip().upper(): (configuration, store)
		for configuration, store in zip(configurations, stores)
	}
	rows = _group_balance_rows(rows, set(store_by_marketplace))
	fetched_at = now_datetime()
	result = {"created": 0, "updated": 0, "unchanged": 0, "balances": len(rows), "pages": pages}
	per_country = {marketplace_id: 0 for marketplace_id in store_by_marketplace}
	for balance in rows:
		metadata = balance.get("partnerMetadata") or balance.get("sellingPartnerMetadata") or {}
		marketplace_id = str(metadata.get("marketplaceId") or "").strip().upper()
		configuration, store = store_by_marketplace[marketplace_id]
		response_date = balance.get("asOfDate") or as_of_date or fetched_at.date()
		outcome = _save_snapshot(master, configuration, store, balance, response_date, sync_type, fetched_at)
		result[outcome] += 1
		per_country[marketplace_id] += 1
	frappe.db.commit()
	snapshot_date = getdate(as_of_date or fetched_at.date())
	for configuration, store in zip(configurations, stores):
		marketplace_id = str(store.marketplace_id or "").strip().upper()
		# Historical/recheck requests run from old to new dates.  Do not let an
		# older response replace the date/count of the latest saved snapshot.
		if configuration.last_snapshot_date and snapshot_date < getdate(configuration.last_snapshot_date):
			continue
		_update(
			COUNTRY_DOCTYPE,
			configuration.name,
			last_snapshot_date=snapshot_date,
			last_snapshot_count=per_country.get(marketplace_id, 0),
		)
	return result


def _task_lock(master_name):
	identity = hashlib.sha256(str(master_name).encode("utf-8")).hexdigest()[:24]
	return frappe.cache().lock(
		frappe.cache().make_key(f"fengjing:amazon-balance:{identity}"),
		timeout=TASK_TIMEOUT,
		blocking_timeout=0,
	)


def _enqueue(master_name, mode, days=0):
	identity = hashlib.sha256(str(master_name).encode("utf-8")).hexdigest()[:16]
	frappe.enqueue(
		execute_balance_sync,
		queue="long",
		timeout=TASK_TIMEOUT,
		enqueue_after_commit=True,
		job_id=f"amazon-balance-{identity}-{mode}-{cint(days)}",
		deduplicate=True,
		master_name=master_name,
		mode=mode,
		days=cint(days),
	)


def _summary_text(status, summary):
	return json.dumps({"status": status, **summary}, ensure_ascii=False, default=str)


def _accumulate(total, current):
	for key in ("created", "updated", "unchanged", "balances", "pages"):
		total[key] += cint(current.get(key))
	total["dates"] += 1


def execute_balance_sync(master_name, mode="current", days=0):
	if mode not in {"history", "current", "routine", "recheck"}:
		raise ValueError(f"Unsupported Amazon balance sync mode: {mode}")
	lock = _task_lock(master_name)
	if not lock.acquire(blocking=False):
		return {"status": "busy", "message": "该余额总配置已有任务正在运行"}
	started_at = now_datetime()
	summary = {"created": 0, "updated": 0, "unchanged": 0, "balances": 0, "pages": 0, "dates": 0}
	try:
		master, configurations, stores, credential_store = _enabled_group(master_name)
		_update(
			MASTER_DOCTYPE,
			master.name,
			current_status="运行中",
			current_execution_type=mode,
			started_at=started_at,
			completed_at=None,
			last_error="",
			**({"history_status": "运行中", "history_last_error": ""} if mode == "history" else {}),
		)
		_update_countries(
			configurations,
			current_status="运行中",
			current_execution_type=mode,
			started_at=started_at,
			completed_at=None,
			last_error="",
			**({"history_status": "运行中", "history_last_error": ""} if mode == "history" else {}),
		)
		if mode == "history":
			start_date = getdate(master.history_start_date)
			end_date = getdate(master.history_end_date)
			cursor = getdate(master.history_checkpoint) if master.history_checkpoint else start_date
			cursor = max(cursor, start_date)
			total_days = max((end_date - start_date).days + 1, 1)
			segment_days = min(max(cint(master.history_segment_days), 1), MAX_HISTORY_SEGMENT_DAYS)
			while cursor <= end_date:
				segment_end = min(end_date, cursor + timedelta(days=segment_days - 1))
				while cursor <= segment_end:
					result = _sync_date(master, configurations, stores, credential_store, cursor, "history")
					_accumulate(summary, result)
					cursor += timedelta(days=1)
					progress = min(((cursor - start_date).days / total_days) * 100, 100)
					_update(
						MASTER_DOCTYPE,
						master.name,
						history_checkpoint=cursor,
						history_progress=progress,
						history_summary=json.dumps(summary, ensure_ascii=False),
					)
					_update_countries(
						configurations,
						history_checkpoint=cursor,
						history_last_at=now_datetime(),
						history_summary=json.dumps(summary, ensure_ascii=False),
					)
		else:
			today = getdate(now_datetime())
			result = _sync_date(master, configurations, stores, credential_store, today, "current")
			_accumulate(summary, result)
			if mode in {"routine", "recheck"}:
				lookback_days = cint(days) if mode == "recheck" else cint(master.recent_recheck_days)
				for offset in range(max(lookback_days, 0), 0, -1):
					result = _sync_date(
						master,
						configurations,
						stores,
						credential_store,
						today - timedelta(days=offset),
						"recheck",
					)
					_accumulate(summary, result)
		finished_at = now_datetime()
		text = _summary_text("已完成", summary)
		next_sync_at = finished_at + timedelta(hours=max(cint(master.sync_interval_hours), 1))
		master_values = {
			"current_status": "已完成",
			"current_execution_type": "",
			"completed_at": finished_at,
			"last_success_at": finished_at,
			"next_sync_at": next_sync_at,
			"last_sync_result": text,
			"last_error": "",
		}
		country_values = {
			"current_status": "已完成",
			"current_execution_type": "",
			"completed_at": finished_at,
			"last_success_at": finished_at,
			"current_balance_last_at": finished_at,
			"current_balance_next_at": next_sync_at,
			"last_sync_result": text,
			"last_error": "",
		}
		if mode == "history":
			master_values.update({"history_status": "已完成", "history_progress": 100, "history_summary": text})
			country_values.update(
				{"history_status": "已完成", "history_completed": 1, "history_summary": text}
			)
		_update(MASTER_DOCTYPE, master.name, **master_values)
		_update_countries(configurations, **country_values)
		return {"status": "success", "summary": summary}
	except Exception as exc:
		finished_at = now_datetime()
		message = str(exc)[:2000]
		master = _master(master_name)
		configurations = [
			_country(name)
			for name in frappe.get_all(
				COUNTRY_DOCTYPE,
				filters={"master_configuration": master_name, "enabled": 1},
				pluck="name",
			)
		]
		master_values = {
			"current_status": "失败",
			"current_execution_type": "",
			"completed_at": finished_at,
			"last_error": message,
			"last_sync_result": f"失败：{message}",
		}
		country_values = dict(master_values)
		if mode == "history":
			master_values.update({"history_status": "失败", "history_last_error": message})
			country_values.update({"history_status": "失败", "history_last_error": message})
		_update(MASTER_DOCTYPE, master.name, **master_values)
		_update_countries(configurations, **country_values)
		frappe.logger("amazon_balances", allow_site=True).exception(
			"Amazon balance sync failed: master=%s mode=%s", master_name, mode
		)
		raise
	finally:
		try:
			lock.release()
		except Exception:
			pass


@frappe.whitelist()
def start_history_sync(name):
	master, configurations, stores, credential_store = _enabled_group(name)
	if not master.history_start_date or not master.history_end_date:
		frappe.throw(_("请先填写历史开始日期和历史结束日期。"))
	values = {
		"history_status": "等待中",
		"current_status": "等待中",
		"current_execution_type": "history",
		"history_last_error": "",
		"last_error": "",
	}
	if master.history_status == "已完成":
		values.update({"history_checkpoint": None, "history_progress": 0, "history_summary": ""})
		_update_countries(
			configurations,
			history_checkpoint=None,
			history_completed=0,
			history_status="等待中",
			history_summary="",
			history_last_error="",
		)
	_update(MASTER_DOCTYPE, master.name, **values)
	_enqueue(master.name, "history")
	return {"status": "queued", "message": "亚马逊历史余额任务已进入后台队列"}


@frappe.whitelist()
def start_current_sync(name):
	master, configurations, stores, credential_store = _enabled_group(name)
	_update(MASTER_DOCTYPE, master.name, current_status="等待中", current_execution_type="current", last_error="")
	_update_countries(configurations, current_status="等待中", current_execution_type="current", last_error="")
	_enqueue(master.name, "current")
	return {"status": "queued", "message": "亚马逊当前余额任务已进入后台队列"}


@frappe.whitelist()
def start_recheck_sync(name, days=None):
	master, configurations, stores, credential_store = _enabled_group(name)
	days = cint(days) if days is not None else cint(master.recent_recheck_days)
	if days < 1 or days > 366:
		frappe.throw(_("余额复核天数必须在1至366天之间。"))
	_update(MASTER_DOCTYPE, master.name, current_status="等待中", current_execution_type="recheck", last_error="")
	_update_countries(configurations, current_status="等待中", current_execution_type="recheck", last_error="")
	_enqueue(master.name, "recheck", days)
	return {"status": "queued", "message": f"亚马逊最近{days}天余额复核任务已进入后台队列"}


def run_scheduled_balance_sync():
	now = now_datetime()
	for name in frappe.get_all(MASTER_DOCTYPE, filters={"enabled": 1}, pluck="name"):
		try:
			master = _master(name)
			if master.current_status == "运行中" and master.started_at:
				if (now - get_datetime(master.started_at)).total_seconds() <= TASK_TIMEOUT:
					continue
				_update(
					MASTER_DOCTYPE,
					master.name,
					current_status="失败",
					current_execution_type="",
					completed_at=now,
					last_error="上一次余额任务超过六小时，已由调度器释放。",
				)
				master = _master(name)
			if master.history_status in {"等待中", "运行中"}:
				_enqueue(master.name, "history")
				continue
			if not master.next_sync_at or get_datetime(master.next_sync_at) <= now:
				_enqueue(master.name, "routine")
		except Exception:
			frappe.logger("amazon_balances", allow_site=True).exception(
				"Amazon balance scheduler failed: master=%s", name
			)


启动亚马逊历史余额同步 = start_history_sync
启动亚马逊当前余额同步 = start_current_sync
启动亚马逊余额近期复核 = start_recheck_sync
定时执行亚马逊余额同步 = run_scheduled_balance_sync
