import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import frappe
from frappe.model.document import Document
from frappe.utils import cint, get_datetime, getdate, now_datetime

from .tiktok_aftersales_sync import sync_aftersales
from .tiktok_analytics_sync import INVALID_DATE_RANGE, find_earliest_performance_date, sync_shop_performance
from .tiktok_api import STORE_DOCTYPE, TikTokAPIError, TikTokClient
from .tiktok_finance_sync import sync_payouts, sync_statement_transactions, sync_statements
from .tiktok_order_sync import sync_orders


JOB_TIMEOUT = 30 * 60
WINDOW_DAYS = 7
PERFORMANCE_DATE_CACHE = "tiktok_performance_earliest"
BUSY = {"排队中", "运行中"}
CONTINUING = "待续跑"
RECHECK_DAYS = (7, 14, 30, 180)
RECHECK_MODES = {f"核对{days}天": days for days in RECHECK_DAYS}
RECHECK_ACTIONS = {f"recheck_{days}": mode for mode, days in RECHECK_MODES.items()}
HISTORY = "历史"
INCREMENTAL = "增量"
CONFIGURATION_FIELDS = (
	"enabled", "interval_minutes", "lookback_days", "history_start_date", "history_end_date",
	*(field for days in RECHECK_DAYS for field in (f"enable_recheck_{days}", f"recheck_{days}_interval_days")),
)
STATEMENT_HISTORY_START = date(2023, 7, 1)
HISTORY_NOTES = {
	"orders": "官方未公布固定回溯天数，最早可查日期以店铺实际数据为准。",
	"aftersales": "官方未公布固定回溯天数，按售后创建日期查询。",
	"statements": "按结算账单日期查询。",
	"statement_transactions": "按账单日期查询。美国跨境店仅支持 2025-04-30 起的数据。",
	"payouts": "官方未公布固定回溯天数，起点取决于店铺最早的打款记录。",
	"shop_performance": "按店铺时区查询日数据。开始日期超出平台允许范围时，自动从可查的最早日期开始。",
}


def _history_minimum(kind):
	return STATEMENT_HISTORY_START if kind in {"statements", "statement_transactions"} else None


def _history_limit(kind, today, store=None):
	earliest = _history_minimum(kind)
	verified_earliest = None
	if kind == "shop_performance" and store:
		verified_earliest = frappe.cache.get_value(f"{PERFORMANCE_DATE_CACHE}:{store}")
	return {
		"earliest": earliest,
		"verified_earliest": verified_earliest,
		"days": (today - earliest).days if today and earliest else None,
		"latest": today - timedelta(days=1) if today else None,
		"note": HISTORY_NOTES[kind],
	}


@dataclass(frozen=True)
class SyncType:
	name: str
	label: str
	fetch: object

	@property
	def configuration(self):
		return f"TikTok {self.name} Sync Configuration"

	@property
	def storage(self):
		return f"TikTok {self.name} Record"


SYNC_TYPES = {
	"orders": SyncType("Order", "订单", sync_orders),
	"aftersales": SyncType("Aftersales", "退货退款", sync_aftersales),
	"statements": SyncType("Statement", "结算账单", sync_statements),
	"statement_transactions": SyncType("Statement Transaction", "账单交易明细", sync_statement_transactions),
	"payouts": SyncType("Payout", "打款记录", sync_payouts),
	"shop_performance": SyncType("Shop Performance", "店铺表现", sync_shop_performance),
}


@dataclass(frozen=True)
class Window:
	start_date: date
	end_date: date
	start_timestamp: int
	end_timestamp: int
	historical: bool
	recheck: bool = False


class TikTokSyncConfiguration(Document):
	def validate(self):
		if cint(self.interval_minutes) < 1 or cint(self.lookback_days) < 1:
			frappe.throw("同步间隔和增量回看天数必须大于 0。")
		if any(cint(self.get(f"recheck_{days}_interval_days")) < 1 for days in RECHECK_DAYS):
			frappe.throw("核对间隔必须大于 0 天。")
		if self.history_start_date and self.history_end_date and getdate(self.history_start_date) > getdate(self.history_end_date):
			frappe.throw("历史开始日期不能晚于结束日期。")
		if self.is_new():
			return
		for days in RECHECK_DAYS:
			interval = f"recheck_{days}_interval_days"
			last = self.get(f"recheck_{days}_last_at")
			if self.has_value_changed(interval) and last:
				self.set(f"recheck_{days}_next_at", _next_recheck(get_datetime(last), cint(self.get(interval))))


def _shop_timezone(store):
	try:
		return ZoneInfo(store.shop_timezone or "America/Los_Angeles")
	except ZoneInfoNotFoundError:
		raise TikTokAPIError("店铺时区无效，请填写正确的 IANA 时区。") from None


def _set(config, **values):
	frappe.db.set_value(config.doctype, config.name, values, update_modified=False)
	config.update(values)


def _expire_abandoned_task(config, now):
	if config.task_status not in BUSY:
		return
	started_at = config.task_started_at or config.task_requested_at
	if started_at and (now - get_datetime(started_at)).total_seconds() <= JOB_TIMEOUT + 300:
		return
	values = {"task_status": "失败", "last_error": "上次任务超时，请重试。"}
	if config.task_mode == HISTORY:
		values["history_status"] = "失败"
	_set(config, **values)


def _queue(kind, config, mode):
	task_id = uuid4().hex
	_set(config, task_id=task_id, task_status="排队中", task_mode=mode,
		task_requested_at=now_datetime(), task_started_at=None, last_error=None)
	frappe.enqueue(
		execute_sync, queue="long", timeout=JOB_TIMEOUT, enqueue_after_commit=True,
		job_id=f"tiktok-sync-{task_id}", kind=kind, name=config.name, task_id=task_id,
	)


@frappe.whitelist(methods=["POST"])
def request_sync(kind, configuration, action):
	if kind not in SYNC_TYPES or action not in {"incremental", "history", "retry", *RECHECK_ACTIONS}:
		frappe.throw("无效的 TikTok 同步任务。")
	config = frappe.get_doc(SYNC_TYPES[kind].configuration, configuration, for_update=True)
	config.check_permission("write")
	_expire_abandoned_task(config, now_datetime())
	if config.task_status in BUSY or config.task_status == CONTINUING:
		frappe.throw("当前任务正在执行，请等待完成。")
	if action == "retry" and config.task_status != "失败":
		frappe.throw("当前没有可以重试的失败任务。")

	if action in RECHECK_ACTIONS:
		try:
			_start_recheck(kind, config, RECHECK_ACTIONS[action])
		except TikTokAPIError as error:
			frappe.throw(str(error))
		return

	mode = config.task_mode if action == "retry" else HISTORY if action == "history" else INCREMENTAL
	if action == "history":
		if not config.history_start_date or not config.history_end_date:
			frappe.throw("请先填写历史开始日期和结束日期。")
		start, end = getdate(config.history_start_date), getdate(config.history_end_date)
		shop = frappe.get_doc(STORE_DOCTYPE, config.tiktok_store)
		try:
			today = datetime.now(_shop_timezone(shop)).date()
		except TikTokAPIError as error:
			frappe.throw(str(error))
		if start > end or end >= today:
			frappe.throw("历史补抓请选择已经结束的店铺日期，当天数据使用增量同步。")
		earliest = _history_minimum(kind)
		if earliest and start < earliest:
			frappe.throw(f"该接口最早支持 {earliest}，请调整历史开始日期。")
		_set(config, history_origin=start, history_cursor=start, history_until=end, history_status="待抓取")
	elif mode == HISTORY:
		if not config.history_cursor or not config.history_until:
			frappe.throw("无历史进度可继续，请重新开始历史补抓。")
		_set(config, history_status="待抓取")
	elif mode in RECHECK_MODES and (not config.recheck_cursor or not config.recheck_until):
		frappe.throw("无核对任务可继续，请重新发起核对。")
	_queue(kind, config, mode)


def _start_recheck(kind, config, mode):
	store = frappe.get_doc(STORE_DOCTYPE, config.tiktok_store)
	today = datetime.now(_shop_timezone(store)).date()
	start = today - timedelta(days=RECHECK_MODES[mode])
	earliest = _history_minimum(kind)
	if earliest:
		start = max(start, earliest)
	_set(config, recheck_cursor=start, recheck_until=today - timedelta(days=1), last_result=None)
	_queue(kind, config, mode)


def _next_recheck(now, days):
	return now.replace(hour=2, minute=59, second=0, microsecond=0) + timedelta(days=days)


def _windows(kind, config, mode, zone, now, earliest=None):
	recheck = mode in RECHECK_MODES
	historical = mode == HISTORY or recheck
	if historical:
		start = getdate(config.recheck_cursor if recheck else config.history_cursor)
		until = getdate(config.recheck_until if recheck else config.history_until)
	else:
		start = now.date() - timedelta(days=cint(config.lookback_days))
		if config.last_incremental_at:
			from frappe.utils import get_system_timezone

			cached = get_datetime(config.last_incremental_at).replace(tzinfo=ZoneInfo(get_system_timezone()))
			start = min(start, cached.astimezone(zone).date())
		# Performance represents daily totals in the shop's registered time zone.
		limit = now.date() if kind == "shop_performance" else now.date() + timedelta(days=1)
	if earliest:
		start = max(start, earliest)
	if historical:
		limit = min(start + timedelta(days=WINDOW_DAYS), until + timedelta(days=1))
	while start < limit:
		end = min(start + timedelta(days=WINDOW_DAYS), limit)
		start_stamp = int(datetime.combine(start, time.min, zone).timestamp())
		end_stamp = min(int(datetime.combine(end, time.min, zone).timestamp()), int(now.timestamp()))
		if start_stamp < end_stamp:
			yield Window(start, end, start_stamp, end_stamp, historical, recheck)
		start = end


def _fetch_sync_windows(kind, config, zone, now, client, counts):
	fetch = SYNC_TYPES[kind].fetch
	windows = _windows(kind, config, config.task_mode, zone, now)
	first = next(windows, None)
	if not first:
		return None, None

	adjusted_start = None
	try:
		fetch(client, first, counts)
	except TikTokAPIError as error:
		if kind != "shop_performance" or error.code != INVALID_DATE_RANGE:
			raise
		adjusted_start = find_earliest_performance_date(client, first.start_date, now.date())
		if not adjusted_start:
			raise
		frappe.cache.set_value(
			f"{PERFORMANCE_DATE_CACHE}:{client.store.name}", adjusted_start.isoformat(), expires_in_sec=24 * 60 * 60,
		)
		requested_start = first.start_date
		windows = _windows(kind, config, config.task_mode, zone, now, earliest=adjusted_start)
		first = next(windows, None)
		if not first:
			explanation = f"店铺表现目前最早可查 {adjusted_start}，所选结束日期更早，请重新选择补抓范围。"
			raise TikTokAPIError(explanation, INVALID_DATE_RANGE) from None
		fetch(client, first, counts)
		counts["dates_outside_platform_range"] += (adjusted_start - requested_start).days

	end_date = first.end_date
	for window in windows:
		fetch(client, window, counts)
		end_date = window.end_date
	return end_date, adjusted_start


def execute_sync(kind, name, task_id):
	spec = SYNC_TYPES[kind]
	config = frappe.get_doc(spec.configuration, name, for_update=True)
	if config.task_id != task_id or config.task_status != "排队中":
		return
	historical = config.task_mode == HISTORY
	recheck_days = RECHECK_MODES.get(config.task_mode)
	updates = {"task_status": "运行中", "task_started_at": now_datetime()}
	if historical:
		updates["history_status"] = "进行中"
	_set(config, **updates)
	frappe.db.commit()

	try:
		store = frappe.get_doc(STORE_DOCTYPE, config.tiktok_store)
		zone = _shop_timezone(store)
		now = datetime.now(zone)
		client = TikTokClient(store)
		counts = Counter(created=0, updated=0, unchanged=0, older=0, pages=0)
		if recheck_days and config.last_result:
			counts.update(json.loads(config.last_result))
		end_date, adjusted_start = _fetch_sync_windows(kind, config, zone, now, client, counts)
		if (historical or recheck_days) and end_date is None:
			raise TikTokAPIError("任务没有有效的日期区间。")
		finished = now_datetime()
		updates = {"task_status": "成功", "last_success_at": finished, "last_result": json.dumps(counts, ensure_ascii=False)}
		if historical:
			updates.update(
				history_cursor=end_date,
				history_status="完成" if end_date > getdate(config.history_until) else "待抓取",
			)
			if adjusted_start and getdate(config.history_origin) == getdate(config.history_cursor):
				updates["history_origin"] = adjusted_start
		elif recheck_days:
			updates["recheck_cursor"] = end_date
			if end_date <= getdate(config.recheck_until):
				updates["task_status"] = CONTINUING
				updates.pop("last_success_at")
			else:
				updates[f"recheck_{recheck_days}_last_at"] = finished
				updates[f"recheck_{recheck_days}_next_at"] = _next_recheck(
					finished, cint(config.get(f"recheck_{recheck_days}_interval_days"))
				)
		else:
			updates.update(last_incremental_at=config.task_started_at,
				next_sync_at=finished + timedelta(minutes=max(1, cint(config.interval_minutes))))
	except Exception as error:
		frappe.db.rollback()
		if not isinstance(error, TikTokAPIError):
			frappe.logger("tiktok_sync", allow_site=True).error("%s failed: %s", kind, type(error).__name__)
		message = str(error) if isinstance(error, TikTokAPIError) else f"同步执行失败（{type(error).__name__}），请联系管理员。"
		updates = {"task_status": "失败", "last_error": message}
		if historical:
			updates["history_status"] = "失败"
		elif recheck_days:
			updates[f"recheck_{recheck_days}_next_at"] = _next_recheck(now_datetime(), 1)
		else:
			updates["next_sync_at"] = now_datetime() + timedelta(minutes=max(5, cint(config.interval_minutes)))

	current = frappe.db.get_value(
		spec.configuration, name, ["task_id", "history_start_date"], as_dict=True, for_update=True,
	)
	if not current or current.task_id != task_id:
		frappe.db.rollback()
		return
	if "history_origin" in updates and current.history_start_date and (
		getdate(current.history_start_date) == getdate(config.history_origin)
	):
		updates["history_start_date"] = updates["history_origin"]
	frappe.db.set_value(spec.configuration, name, updates, update_modified="history_start_date" in updates)
	frappe.db.commit()


def run_scheduled_sync():
	now = now_datetime()
	for kind, spec in SYNC_TYPES.items():
		names = frappe.get_all(spec.configuration, pluck="name", or_filters={
			"enabled": 1, "history_status": ["in", ["待抓取", "进行中"]], "task_status": ["in", [*BUSY, CONTINUING]],
		})
		for name in names:
			try:
				config = frappe.get_doc(spec.configuration, name, for_update=True)
				_expire_abandoned_task(config, now)
				if config.task_status == CONTINUING:
					_queue(kind, config, config.task_mode)
				elif config.task_status not in BUSY:
					if config.enabled and (not config.next_sync_at or get_datetime(config.next_sync_at) <= now):
						_queue(kind, config, INCREMENTAL)
					elif config.history_status == "待抓取":
						_queue(kind, config, HISTORY)
					elif config.enabled:
						_schedule_recheck(kind, config, now)
			except Exception:
				frappe.db.rollback()
				frappe.logger("tiktok_sync", allow_site=True).error("TikTok scheduler could not queue %s", kind)
			else:
				# Release row locks and enqueue reserved jobs before visiting the next configuration.
				frappe.db.commit()


def _schedule_recheck(kind, config, now):
	for mode, days in RECHECK_MODES.items():
		if not config.get(f"enable_recheck_{days}"):
			continue
		field = f"recheck_{days}_next_at"
		next_at = config.get(field)
		if not next_at:
			first = _next_recheck(now, 0)
			_set(config, **{field: first if first > now else first + timedelta(days=1)})
		elif get_datetime(next_at) <= now:
			_start_recheck(kind, config, mode)
			break


@frappe.whitelist(methods=["GET"])
def get_overview(tiktok_store=None):
	stores = frappe.get_list(STORE_DOCTYPE, fields=["name", "store_name", "shop_id", "country", "shop_timezone", "authorized_at"], limit_page_length=0)
	store = next((record for record in stores if record.name == tiktok_store), None) if tiktok_store else (stores[0] if stores else None)
	if tiktok_store and not store:
		frappe.throw("无法读取该 TikTok 店铺配置。", frappe.PermissionError)
	try:
		today = datetime.now(_shop_timezone(store)).date() if store else None
	except TikTokAPIError:
		today = None
	tasks = []
	for kind, spec in SYNC_TYPES.items():
		settings = frappe.get_list(spec.configuration, filters={"tiktok_store": store.name}, fields=[
			"name", "modified", *CONFIGURATION_FIELDS, "task_status", "task_mode", "history_status",
			"history_cursor", "history_until", "last_success_at", "next_sync_at", "last_error", "last_result",
			"recheck_cursor", "recheck_until",
			*(f"recheck_{days}_{suffix}_at" for days in RECHECK_DAYS for suffix in ("last", "next")),
		], limit_page_length=1) if store else []
		tasks.append({
			"kind": kind, "label": spec.label, "configuration": spec.configuration,
			"storage": spec.storage, "settings": settings[0] if settings else None,
			"history_limit": _history_limit(kind, today, store.name if store else None),
			"defaults": {field: frappe.get_meta(spec.configuration).get_field(field).default
				for field in CONFIGURATION_FIELDS} if not settings else None,
		})
	return {"stores": stores, "store": store, "tasks": tasks, "recheck_days": RECHECK_DAYS}


@frappe.whitelist(methods=["POST"])
def save_configuration(kind, tiktok_store, values, modified=None):
	spec = SYNC_TYPES.get(kind)
	if not spec:
		frappe.throw("无效的 TikTok 同步配置。")
	frappe.get_doc(STORE_DOCTYPE, tiktok_store).check_permission("read")
	values = frappe.parse_json(values)
	if not isinstance(values, dict) or set(values) - set(CONFIGURATION_FIELDS):
		frappe.throw("配置字段无效。")
	name = frappe.db.get_value(spec.configuration, {"tiktok_store": tiktok_store})
	if name:
		config = frappe.get_doc(spec.configuration, name, for_update=True)
		config.check_permission("write")
		if not modified or get_datetime(modified) != get_datetime(config.modified):
			frappe.throw("配置已经变更，请刷新后再修改。", frappe.TimestampMismatchError)
		config.update(values)
		config.save()
	else:
		if modified:
			frappe.throw("配置已经变更，请刷新后再修改。", frappe.TimestampMismatchError)
		frappe.get_doc(doctype=spec.configuration, tiktok_store=tiktok_store, **values).insert()
