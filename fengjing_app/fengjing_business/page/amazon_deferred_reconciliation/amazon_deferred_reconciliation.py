"""Capture immutable Amazon balance, financial transaction and order views."""

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta

import frappe
from frappe.utils import get_datetime, now_datetime

from fengjing_app.fengjing_business.doctype.amazon_balance_master_configuration.amazon_balance_master_configuration import (
	TASK_TIMEOUT as BALANCE_TIMEOUT,
	execute_balance_snapshot_sync,
	execute_balance_sync,
)
from fengjing_app.fengjing_business.doctype.amazon_financial_configuration.amazon_financial_configuration import (
	TASK_TIMEOUT as FINANCIAL_TIMEOUT,
	execute_financial_sync,
)
from fengjing_app.fengjing_business.doctype.amazon_order_configuration.amazon_order_configuration import (
	TASK_TIMEOUT as ORDER_TIMEOUT,
	execute_order_sync,
)


DOCTYPE = "Amazon Deferred Reconciliation Snapshot"
BALANCE_DOCTYPE = "Amazon Balance Snapshot"
FINANCIAL_DOCTYPE = "Amazon Financial Transaction"
ORDER_DOCTYPE = "Amazon order synchronization"
STORE_DOCTYPE = "Amazon Store Configuration"
SOURCE_FIELDS = {
	"balance": ("balance_status", "balance_fetched_at", "余额"),
	"transactions": ("transactions_status", "transactions_fetched_at", "财务交易"),
	"orders": ("orders_status", "orders_fetched_at", "订单"),
}
SNAPSHOT_FIELDS = [
	"name", "snapshot_label", "snapshot_status", "snapshot_scope", "amazon_store",
	"marketplace_id", "requested_at", "completed_at", "snapshot_schema_version",
	"range_mode", "range_start", "range_end", "balance_status",
	"balance_fetched_at", "transactions_status", "transactions_fetched_at",
	"orders_status", "orders_fetched_at", "balance_row_count", "financial_transaction_count",
	"deferred_transaction_count", "order_count", "unmatched_order_count", "last_error",
]


def _check_access(action="read"):
	if not frappe.has_permission(DOCTYPE, action):
		frappe.throw("没有递延对账快照的访问权限。", frappe.PermissionError)


def _json(value):
	return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _month_boundary(index):
	return datetime(index // 12, index % 12 + 1, 1)


def _range_window(range_mode, month_start=None, month_end=None):
	# Balance is fetched as a current value. Finance and order APIs require their
	# date-range cutoff to be more than two minutes old, so freeze a queryable
	# cutoff up front instead of making both workers wait after enqueueing.
	safe_end = (now_datetime() - timedelta(minutes=2, seconds=5)).replace(microsecond=0)
	if range_mode in {"90d", "180d"}:
		days = int(range_mode[:-1])
		return safe_end - timedelta(days=days), safe_end, f"{days}天"
	if range_mode != "months":
		frappe.throw("不支持的快照时间范围。")
	for value in (month_start, month_end):
		if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(value or "")):
			frappe.throw("请选择有效的开始月份和结束月份。")
	start_year, start_month = map(int, month_start.split("-"))
	end_year, end_month = map(int, month_end.split("-"))
	start_index = start_year * 12 + start_month - 1
	end_index = end_year * 12 + end_month - 1
	current_index = safe_end.year * 12 + safe_end.month - 1
	if not current_index - 11 <= start_index <= end_index <= current_index:
		frappe.throw("月份范围只能选择包含本月的最近12个月。")
	start = _month_boundary(start_index)
	end = min(_month_boundary(end_index + 1), safe_end)
	if start >= end:
		frappe.throw("该月份尚无可抓取的数据。")
	return start, end, f"{start_year % 100}年{start_month}月—{end_year % 100}年{end_month}月"


def _scope(selected_store=None):
	selected_store = str(selected_store or "").strip()
	masters = frappe.get_all(
		"Amazon Balance Master Configuration", filters={"enabled": 1}, pluck="name", limit_page_length=0
	)
	if not masters:
		frappe.throw("没有已启用的亚马逊余额总配置。")
	countries = frappe.get_all(
		"Amazon Balance Country Configuration",
		filters={"enabled": 1, "master_configuration": ["in", masters]},
		fields=["master_configuration", "amazon_store"], limit_page_length=0,
	)
	if selected_store:
		countries = [row for row in countries if row.amazon_store == selected_store]
		if len(countries) != 1:
			frappe.throw("所选站点未启用，或关联了多个余额国家配置，请检查站点配置。")
	masters = sorted({row.master_configuration for row in countries if row.master_configuration})
	store_names = sorted({row.amazon_store for row in countries if row.amazon_store})
	if not store_names:
		frappe.throw("余额总配置下没有已启用的国家配置。")
	financial = frappe.get_all(
		"Amazon Financial Configuration", filters={"enabled": 1, "amazon_store": ["in", store_names]},
		fields=["name", "amazon_store"], limit_page_length=0,
	)
	orders = frappe.get_all(
		"Amazon Order Configuration", filters={"enabled": 1, "amazon_store": ["in", store_names]},
		fields=["name", "amazon_store"], limit_page_length=0,
	)
	for label, rows in (("财务", financial), ("订单", orders)):
		counts = {store: 0 for store in store_names}
		for row in rows:
			counts[row.amazon_store] += 1
			if counts[row.amazon_store] > 1:
				frappe.throw(f"{row.amazon_store} 有多个已启用的{label}配置，请先保留一个。")
		missing = [store for store, count in counts.items() if count == 0]
		if missing:
			frappe.throw(f"以下店铺缺少已启用的{label}配置：{'、'.join(missing)}")
	stores = frappe.get_all(
		STORE_DOCTYPE, filters={"name": ["in", store_names], "enabled": 1},
		fields=["name", "store_name", "cost_center", "marketplace_id", "country"], limit_page_length=0,
	)
	if len(stores) != len(store_names) or any(not row.cost_center or not row.marketplace_id for row in stores):
		frappe.throw("亚马逊店铺缺少成本中心或 Marketplace ID，无法关联三类数据。")
	if len({row.cost_center for row in stores}) != len(stores):
		frappe.throw("多个亚马逊店铺使用同一个成本中心，无法安全关联递延交易。")
	return {
		"masters": masters,
		"financial": sorted(row.name for row in financial),
		"orders": sorted(row.name for row in orders),
		"stores": store_names,
		"selected_store": next((row for row in stores if row.name == selected_store), None),
	}


@frappe.whitelist()
def get_store_options():
	_check_access()
	masters = frappe.get_all(
		"Amazon Balance Master Configuration", filters={"enabled": 1}, pluck="name", limit_page_length=0,
	)
	if not masters:
		return []
	countries = frappe.get_all(
		"Amazon Balance Country Configuration",
		filters={"enabled": 1, "master_configuration": ["in", masters]},
		pluck="amazon_store", limit_page_length=0,
	)
	country_counts = Counter(name for name in countries if name)
	store_names = sorted(country_counts)
	if not store_names:
		return []
	financial_counts = Counter(frappe.get_all(
		"Amazon Financial Configuration", filters={"enabled": 1, "amazon_store": ["in", store_names]},
		pluck="amazon_store", limit_page_length=0,
	))
	order_counts = Counter(frappe.get_all(
		"Amazon Order Configuration", filters={"enabled": 1, "amazon_store": ["in", store_names]},
		pluck="amazon_store", limit_page_length=0,
	))
	stores = frappe.get_all(
		STORE_DOCTYPE, filters={"name": ["in", store_names], "enabled": 1},
		fields=["name", "store_name", "country", "marketplace_id", "api_region", "cost_center"],
		limit_page_length=0,
	)
	return sorted(({
		"name": row.name,
		"label": row.store_name or row.name,
		"country": row.country,
		"marketplace_id": row.marketplace_id,
		"api_region": row.api_region,
		"ready": bool(row.cost_center and row.marketplace_id and country_counts[row.name] == 1
			and financial_counts[row.name] == 1 and order_counts[row.name] == 1),
	} for row in stores), key=lambda row: (row["api_region"] or "", row["country"] or "", row["label"]))


@frappe.whitelist()
def list_snapshots():
	_check_access()
	return frappe.get_all(DOCTYPE, fields=SNAPSHOT_FIELDS, order_by="requested_at desc", limit_page_length=0)


@frappe.whitelist()
def get_snapshot(name):
	_check_access()
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	return {field: doc.get(field) for field in [
		*SNAPSHOT_FIELDS, "balance_json", "financial_transactions_json",
		"deferred_transactions_json", "orders_json", "snapshot_hash",
	]}


@frappe.whitelist()
def start_snapshot(range_mode="90d", month_start=None, month_end=None, amazon_store=None):
	_check_access()
	lock = frappe.cache().lock(
		frappe.cache().make_key("fengjing:amazon-deferred-reconciliation:start"), timeout=120,
	)
	if not lock.acquire(blocking=False):
		frappe.throw("已有快照正在启动，请稍后刷新页面。")
	try:
		return _start_snapshot_locked(range_mode, month_start, month_end, amazon_store)
	finally:
		lock.release()


def _start_snapshot_locked(range_mode, month_start, month_end, amazon_store):
	range_start, range_end, range_label = _range_window(range_mode, month_start, month_end)
	active = frappe.get_all(
		DOCTYPE, filters={"snapshot_status": ["in", ["等待中", "抓取中"]]},
		fields=["name", "requested_at"], order_by="requested_at desc", limit_page_length=1,
	)
	if active:
		if get_datetime(active[0].requested_at) > now_datetime() - timedelta(hours=7):
			return {"name": active[0].name, "status": "already_running"}
		frappe.db.set_value(DOCTYPE, active[0].name, {
			"snapshot_status": "失败", "completed_at": now_datetime(), "last_error": "任务超过 7 小时仍未完成。"
		})
	scope = _scope(amazon_store)
	selected_store = scope["selected_store"]
	scope_label = (selected_store.store_name or selected_store.name) if selected_store else "全部站点"
	started = now_datetime()
	doc = frappe.get_doc({
		"doctype": DOCTYPE,
		"snapshot_label": f"亚马逊对账 {scope_label} · {range_label} · {started:%Y-%m-%d %H:%M:%S}",
		"snapshot_status": "等待中",
		"snapshot_scope": "单个站点" if selected_store else "全部站点",
		"amazon_store": selected_store.name if selected_store else None,
		"marketplace_id": selected_store.marketplace_id if selected_store else None,
		"country": selected_store.country if selected_store else None,
		"range_mode": range_mode,
		"range_start": range_start,
		"range_end": range_end,
		"requested_at": started,
		"balance_status": "等待中",
		"transactions_status": "等待中",
		"orders_status": "等待中",
		"snapshot_schema_version": 2,
	})
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	try:
		for source, names in (("balance", scope["masters"]), ("transactions", scope["financial"]), ("orders", scope["orders"])):
			frappe.enqueue(
				_run_source, queue="long", timeout=max(BALANCE_TIMEOUT, FINANCIAL_TIMEOUT, ORDER_TIMEOUT),
				job_id=f"amazon-deferred-reconciliation-{doc.name}-{source}",
				snapshot_name=doc.name, source=source, configuration_names=names, store_names=scope["stores"],
			)
	except Exception as exc:
		frappe.db.set_value(DOCTYPE, doc.name, {
			"snapshot_status": "失败", "completed_at": now_datetime(),
			"last_error": f"任务入队失败：{str(exc)[:1600]}",
		})
		frappe.db.commit()
		raise
	return {"name": doc.name, "status": "queued"}


def _run_source(snapshot_name, source, configuration_names, store_names):
	status_field, time_field, label = SOURCE_FIELDS[source]
	if not frappe.db.exists(DOCTYPE, snapshot_name):
		return
	if frappe.db.get_value(DOCTYPE, snapshot_name, "snapshot_status") == "失败":
		return
	current_status = frappe.db.get_value(DOCTYPE, snapshot_name, "snapshot_status")
	updates = {status_field: "运行中"}
	if current_status == "等待中":
		updates["snapshot_status"] = "抓取中"
	frappe.db.set_value(DOCTYPE, snapshot_name, updates)
	frappe.db.commit()
	try:
		snapshot = frappe.get_doc(DOCTYPE, snapshot_name)
		for name in configuration_names:
			if source == "balance":
				result = (execute_balance_snapshot_sync(name, snapshot.amazon_store) if snapshot.amazon_store
					else execute_balance_sync(name, "current"))
			elif source == "transactions":
				result = execute_financial_sync(name, "snapshot_range", snapshot.range_start, snapshot.range_end)
			else:
				result = execute_order_sync(name, "snapshot_range", snapshot.range_start, snapshot.range_end)
			if result.get("status") != "success":
				raise RuntimeError(f"{label}配置 {name} 未完成：{result.get('status')}")
		frappe.db.set_value(DOCTYPE, snapshot_name, {status_field: "已完成", time_field: now_datetime()})
		frappe.db.commit()
		_try_finalize(snapshot_name, store_names)
	except Exception as exc:
		if frappe.db.exists(DOCTYPE, snapshot_name):
			frappe.db.set_value(DOCTYPE, snapshot_name, {
				status_field: "失败", "snapshot_status": "失败", "completed_at": now_datetime(),
				"last_error": f"{label}抓取失败：{str(exc)[:1600]}",
			})
			frappe.db.commit()
		frappe.logger("amazon_deferred_reconciliation", allow_site=True).exception(
			"Amazon deferred snapshot source failed: snapshot=%s source=%s", snapshot_name, source
		)
		raise


def _try_finalize(snapshot_name, store_names):
	lock = frappe.cache().lock(
		frappe.cache().make_key(f"fengjing:amazon-deferred-reconciliation:{snapshot_name}"),
		timeout=600, blocking_timeout=30,
	)
	if not lock.acquire():
		raise RuntimeError("快照保存锁等待超时，请检查后台任务。")
	try:
		if not frappe.db.exists(DOCTYPE, snapshot_name):
			return
		doc = frappe.get_doc(DOCTYPE, snapshot_name)
		if doc.snapshot_status in {"已完成", "失败", "不完整"} or "失败" in (
			doc.balance_status, doc.transactions_status, doc.orders_status,
		):
			return
		if not all(doc.get(field) == "已完成" for field, _, _ in SOURCE_FIELDS.values()):
			return
		try:
			if (doc.snapshot_schema_version or 1) >= 2:
				balances, transactions, orders = _capture_rows_v2(doc, store_names)
				payload = [_json(balances), _json(transactions), _json(orders)]
				doc.balance_json, doc.financial_transactions_json, doc.orders_json = payload
				doc.financial_transaction_count = len(transactions)
				doc.deferred_transaction_count = len(_first_by(
					[row for row in transactions if row.transaction_status == "DEFERRED"],
					lambda row: (row.store, row.marketplace_id, row.transaction_id or row.name),
				))
			else:
				balances, transactions, orders, unmatched = _capture_rows(doc, store_names)
				payload = [_json(balances), _json(transactions), _json(orders)]
				doc.balance_json, doc.deferred_transactions_json, doc.orders_json = payload
				doc.deferred_transaction_count = len(transactions)
				doc.unmatched_order_count = unmatched
			doc.balance_row_count = len(balances)
			doc.order_count = len(orders)
			doc.snapshot_hash = hashlib.sha256("\n".join(payload).encode("utf-8")).hexdigest()
			doc.completed_at = now_datetime()
			doc.snapshot_status = "已完成"
			doc.save(ignore_permissions=True)
			frappe.db.commit()
		except Exception as exc:
			frappe.db.rollback()
			frappe.db.set_value(DOCTYPE, snapshot_name, {
				"snapshot_status": "不完整", "completed_at": now_datetime(),
				"last_error": f"三项抓取已完成，但快照保存失败：{str(exc)[:1600]}",
			})
			frappe.db.commit()
			frappe.logger("amazon_deferred_reconciliation", allow_site=True).exception(
				"Amazon deferred snapshot capture failed: snapshot=%s", snapshot_name
			)
	finally:
		lock.release()


def _capture_rows(snapshot, store_names):
	stores = frappe.get_all(
		STORE_DOCTYPE, filters={"name": ["in", store_names]},
		fields=["name", "cost_center", "marketplace_id", "country"], limit_page_length=0,
	)
	by_cost_center = {row.cost_center: row for row in stores}
	balances = _capture_balances(snapshot, store_names)
	centers = sorted(by_cost_center)
	financial = frappe.get_all(
		FINANCIAL_DOCTYPE,
		filters={"store": ["in", centers], "transaction_status": "DEFERRED"},
		fields=["name", "transaction_id", "amazon_order_id", "store", "marketplace_id", "country",
			"transaction_type", "transaction_status", "description", "posted_date", "release_date",
			"currency_code", "total_amount", "net_amount", "fetched_at", "sku", "asin"],
		order_by="fetched_at desc", limit_page_length=0,
	)
	financial = [row for row in financial if row.store in by_cost_center and row.marketplace_id == by_cost_center[row.store].marketplace_id]
	transactions = _first_by(financial, lambda row: (row.store, row.marketplace_id, row.transaction_id or row.name))
	for row in transactions:
		row["amazon_store"] = by_cost_center[row.store].name
	order_ids = sorted({row.amazon_order_id for row in transactions if row.amazon_order_id})
	order_rows = []
	for offset in range(0, len(order_ids), 200):
		order_rows.extend(frappe.get_all(
			ORDER_DOCTYPE,
			filters={"amazon_order_id": ["in", order_ids[offset:offset + 200]], "store": ["in", centers]},
			fields=["name", "amazon_order_id", "store", "marketplace_id", "country", "purchase_date",
				"order_status", "currency_code", "order_total", "quantity_ordered", "fetched_at"],
			order_by="fetched_at desc", limit_page_length=0,
		))
	order_rows = [row for row in order_rows if row.store in by_cost_center and row.marketplace_id == by_cost_center[row.store].marketplace_id]
	orders = _first_by(order_rows, lambda row: (row.store, row.marketplace_id, row.amazon_order_id))
	for row in orders:
		row["amazon_store"] = by_cost_center[row.store].name
	matched = {(row.store, row.marketplace_id, row.amazon_order_id) for row in orders}
	unmatched = len({
		(row.store, row.marketplace_id, row.amazon_order_id) for row in transactions
		if row.amazon_order_id and (row.store, row.marketplace_id, row.amazon_order_id) not in matched
	})
	return balances, transactions, orders, unmatched


def _capture_balances(snapshot, store_names):
	requested = get_datetime(snapshot.get("requested_at"))
	balance_end = get_datetime(snapshot.get("balance_fetched_at"))
	balances = frappe.get_all(
		BALANCE_DOCTYPE,
		filters={"amazon_store": ["in", store_names], "fetched_at": ["between", [requested, balance_end]]},
		fields=["name", "amazon_store", "marketplace_id", "country", "account_type", "currency_code",
			"as_of_date", "last_updated_time", "fetched_at", "total_balance", "available_balance",
			"reserved_balance", "deferred_balance", "account_level_reserve"],
		order_by="fetched_at desc", limit_page_length=0,
	)
	balances = _first_by(balances, lambda row: (row.amazon_store, row.marketplace_id, row.account_type, row.currency_code))
	missing = sorted(set(store_names) - {row.amazon_store for row in balances})
	if missing:
		raise RuntimeError(f"以下店铺没有本次抓取的余额记录：{'、'.join(missing)}")
	return balances


def _capture_rows_v2(snapshot, store_names):
	stores = frappe.get_all(
		STORE_DOCTYPE, filters={"name": ["in", store_names]},
		fields=["name", "cost_center", "marketplace_id"], limit_page_length=0,
	)
	by_cost_center = {row.cost_center: row for row in stores}
	if len(by_cost_center) != len(store_names):
		raise RuntimeError("快照店铺的成本中心配置已发生变化，请重新抓取。")
	balances = _capture_balances(snapshot, store_names)
	centers = sorted(by_cost_center)
	start = get_datetime(snapshot.range_start)
	end = get_datetime(snapshot.range_end)
	financial = frappe.get_all(
		FINANCIAL_DOCTYPE,
		filters=[["store", "in", centers], ["posted_date", ">=", start], ["posted_date", "<", end]],
		fields=["name", "transaction_id", "amazon_order_id", "store", "marketplace_id", "country",
			"transaction_type", "transaction_status", "description", "posted_date", "release_date",
			"currency_code", "total_amount", "net_amount", "item_amount", "product_sales",
			"product_sales_tax", "shipping_credits_tax", "promotional_rebates_tax", "selling_fees",
			"fba_fees", "other_amount", "fetched_at", "sku", "asin", "quantity"],
		order_by="posted_date desc", limit_page_length=0,
	)
	financial = [row for row in financial if row.store in by_cost_center and row.marketplace_id == by_cost_center[row.store].marketplace_id]
	for row in financial:
		row["amazon_store"] = by_cost_center[row.store].name
	order_rows = frappe.get_all(
		ORDER_DOCTYPE,
		filters=[["store", "in", centers], ["purchase_date", ">=", start], ["purchase_date", "<", end]],
		fields=["name", "amazon_order_id", "store", "marketplace_id", "country", "purchase_date",
			"order_status", "currency_code", "order_total", "quantity_ordered", "number_of_items_shipped",
			"number_of_items_unshipped", "sku", "asin", "product_name", "fetched_at"],
		order_by="purchase_date desc", limit_page_length=0,
	)
	order_rows = [row for row in order_rows if row.store in by_cost_center and row.marketplace_id == by_cost_center[row.store].marketplace_id]
	for row in order_rows:
		row["amazon_store"] = by_cost_center[row.store].name
	return balances, financial, order_rows


def _first_by(rows, key_function):
	seen = set()
	result = []
	for row in rows:
		key = key_function(row)
		if key not in seen:
			seen.add(key)
			result.append(row)
	return result


@frappe.whitelist()
def delete_snapshot(name):
	_check_access("delete")
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("delete")
	if doc.snapshot_status in {"等待中", "抓取中"} or any(
		doc.get(field) in {"等待中", "运行中"} for field, _, _ in SOURCE_FIELDS.values()
	):
		frappe.throw("抓取任务尚未结束，暂不能删除此快照。")
	frappe.delete_doc(DOCTYPE, name)
	return {"deleted": name}
