import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import frappe
from frappe.utils import cint, get_datetime, get_system_timezone, getdate

from .doctype.tiktok_order_configuration.tiktok_storage import amount


STORE = "TikTok Order Configuration"
ORDER = "TikTok Order Record"
AFTERSALES = "TikTok Aftersales Record"
STATEMENT = "TikTok Statement Record"
TRANSACTION = "TikTok Statement Transaction Record"
PAYOUT = "TikTok Payout Record"
PERFORMANCE = "TikTok Shop Performance Record"
PAGE_SIZE = 30
BATCH_SIZE = 400

REPORTS = {
	"orders": (ORDER, "created_at", "amount", ("source_order_id", "status")),
	"aftersales": (AFTERSALES, "created_at", "refund_amount", ("aftersales_id", "source_order_id", "status")),
	"payouts": (PAYOUT, "created_at", "amount", ("payment_id", "status")),
	"statements": (STATEMENT, "created_at", "amount", ("statement_id", "payment_id", "status")),
	"transactions": (TRANSACTION, "created_at", "amount", ("statement", "transaction_id", "statement_id", "source_order_id", "adjustment_id", "transaction_type")),
	"performance": (PERFORMANCE, "report_date", "amount", ("order_count",)),
}


def _scope(store):
	stores = frappe.get_list(STORE, fields=["name", "store_name", "shop_timezone"], order_by="store_name asc", limit_page_length=0)
	selected = next((row for row in stores if row.name == store), None) if store else next(iter(stores), None)
	if store and not selected:
		frappe.throw("无法访问该 TikTok 店铺。", frappe.PermissionError)
	return stores, selected


def _check_read(doctype):
	if not frappe.has_permission(doctype, "read"):
		frappe.throw("没有读取这类 TikTok 记录的权限。", frappe.PermissionError)


def _rows(kind, filters, *, include_raw=True):
	doctype, stamp, money, extra = REPORTS[kind]
	fields = ["name", "tiktok_store", stamp, money, "currency", *extra]
	if include_raw:
		fields.append("raw_json")
	offset = 0
	while True:
		rows = frappe.get_list(
			doctype, filters=filters, fields=fields, order_by=f"{stamp} desc, name desc",
			limit_start=offset, limit_page_length=BATCH_SIZE,
		)
		yield from rows
		if len(rows) < BATCH_SIZE:
			break
		offset += BATCH_SIZE


def _raw(row):
	try:
		value = json.loads(row.get("raw_json") or "{}")
	except (ValueError, TypeError):
		return {}
	return value if isinstance(value, dict) else {}


def _zone(store):
	try:
		return ZoneInfo(store.shop_timezone or "America/Los_Angeles")
	except ZoneInfoNotFoundError:
		frappe.throw("店铺时区无效，请在 TikTok 店铺配置中修正。")


def _month_shift(month, offset):
	index = month.year * 12 + month.month - 1 + offset
	return date(index // 12, index % 12 + 1, 1)


def _month_label(day):
	return day.strftime("%Y-%m")


def _shop_datetime(value, site_zone, zone):
	return get_datetime(value).replace(tzinfo=site_zone).astimezone(zone) if value else None


def _month_filters(store, stamp, month, site_zone, zone):
	end = _month_shift(month, 1)
	filters = {"tiktok_store": store}
	if stamp == "report_date":
		filters[stamp] = ["between", [month, end - timedelta(days=1)]]
		return filters
	to_site = lambda day: datetime.combine(day, time.min, zone).astimezone(site_zone).replace(tzinfo=None)
	return [
		["tiktok_store", "=", store], [stamp, ">=", to_site(month)], [stamp, "<", to_site(end)],
	]


def _numeric(value):
	number, _ = amount(value)
	return number


def _matches(query, values):
	return not query or any(query in str(value).casefold() for value in values if value is not None)


def _order_items(raw):
	grouped = {}
	for line in raw.get("line_items") or []:
		if not isinstance(line, dict):
			continue
		sku = line.get("seller_sku") or line.get("sku_id") or line.get("product_id") or ""
		name = line.get("product_name") or line.get("sku_name") or sku
		key = (sku, name)
		item = grouped.setdefault(key, {"sku": sku, "product": name, "units": 0, "image": line.get("sku_image")})
		item["units"] += cint(line["quantity"]) if line.get("quantity") is not None else 1
	return list(grouped.values())


def _display_row(kind, record, site_zone, zone, *, statement_date=None):
	_, stamp, money, _ = REPORTS[kind]
	raw = _raw(record)
	local_stamp = _shop_datetime(record.get(stamp), site_zone, zone) if stamp == "created_at" else record.get(stamp)
	display_stamp = local_stamp.strftime("%Y-%m-%d %H:%M:%S") if isinstance(local_stamp, datetime) else str(local_stamp or "")
	row = {k: v for k, v in record.items() if k not in {"raw_json", stamp, money}}
	row.update(date=statement_date or display_stamp, amount=record.get(money))
	if kind == "orders":
		row["items"] = _order_items(raw)
		row["paid"] = record.status not in {"CANCELLED", "CANCELED", "UNPAID"} and bool(
			raw.get("paid_time") or record.status in {
				"AWAITING_SHIPMENT", "AWAITING_COLLECTION", "PARTIALLY_SHIPPING", "IN_TRANSIT", "DELIVERED", "COMPLETED",
			}
		)
		row["units"] = sum(item["units"] for item in row["items"])
		row["product_amount"] = _numeric((raw.get("payment") or {}).get("sub_total"))
	elif kind == "aftersales":
		row["reason"] = raw.get("return_reason_text") or raw.get("return_reason")
		row["type"] = raw.get("return_type")
	elif kind == "transactions":
		for field in ("revenue_amount", "shipping_cost_amount", "fee_tax_amount", "adjustment_amount"):
			row[field] = _numeric(raw.get(field))
	elif kind == "performance":
		traffic = raw.get("traffic") or {}
		row["views"] = traffic.get("avg_page_views")
		row["visitors"] = traffic.get("avg_visitors")
		row["conversion"] = traffic.get("avg_conversion_rate", traffic.get("avg_conversation_rate"))
	return row


def _money_summary(rows, field="amount"):
	totals = defaultdict(Decimal)
	for row in rows:
		if row.get(field) is not None:
			totals[row.get("currency") or ""] += Decimal(str(row[field]))
	return dict(sorted(totals.items()))


def _money_series(rows, field, label):
	by_currency = defaultdict(lambda: defaultdict(Decimal))
	for row in rows:
		if row["date"] and row.get(field) is not None:
			by_currency[row.get("currency") or ""][row["date"][:10]] += Decimal(str(row[field]))
	return [
		{"label": f"{label} ({currency})" if currency else label, "money": True, "points": [
			{"day": day, "value": value} for day, value in sorted(days.items())
		]}
		for currency, days in sorted(by_currency.items())
	]


def _analysis(kind, rows):
	summary = {"count": len(rows)}
	groups = []
	series = []
	by_day = Counter()
	label = {
		"orders": "订单量", "aftersales": "售后申请数", "payouts": "打款笔数",
		"statements": "账单数", "transactions": "交易笔数", "performance": "店铺表现订单量",
	}[kind]
	for row in rows:
		if row["date"]:
			by_day[row["date"][:10]] += cint(row["order_count"]) if kind == "performance" else 1
	if kind == "orders":
		paid = [row for row in rows if row["paid"]]
		summary.update(
			units=sum(row["units"] for row in paid), paid=len(paid),
			order_amounts=_money_summary(paid), product_amounts=_money_summary(paid, "product_amount"),
		)
		series = _money_series(paid, "product_amount", "商品金额")
		products = {}
		for row in paid:
			for item in row["items"]:
				key = (item["sku"], item["product"] if not item["sku"] else "")
				product = products.setdefault(key, {**item, "units": 0})
				product["units"] += item["units"]
		groups = sorted(products.values(), key=lambda row: (-row["units"], row["sku"]))[:12]
	elif kind == "aftersales":
		completed = [row for row in rows if row["status"] == "RETURN_OR_REFUND_REQUEST_COMPLETE"]
		summary.update(completed=len(completed), amounts=_money_summary(completed))
		series = _money_series(completed, "amount", "已完成退款金额")
		groups = [{"reason": reason, "count": count} for reason, count in Counter(
			row["reason"] or "未提供原因" for row in rows
		).most_common(10)]
	elif kind == "performance":
		summary.update(
			amounts=_money_summary(rows), orders=sum(cint(row["order_count"]) for row in rows),
			views=sum(cint(row["views"]) for row in rows) if any(row["views"] is not None for row in rows) else None,
		)
		series = _money_series(rows, "amount", "GMV")
		for key, title in (("views", "浏览量"), ("visitors", "访客数")):
			points = [{"day": row["date"][:10], "value": row[key]} for row in reversed(rows) if row["date"] and row[key] is not None]
			if points:
				series.append({"label": title, "points": points})
	else:
		summary["amounts"] = _money_summary(rows)
		series = _money_series(rows, "amount", "打款金额" if kind == "payouts" else "结算金额")
	return summary, groups, {"label": label, "series": series,
		"points": [{"day": day, "value": value} for day, value in sorted(by_day.items())]}



def _finance_transactions(filters, store, site_zone, zone):
	statements = list(_rows("statements", filters, include_raw=False))
	for start in range(0, len(statements), BATCH_SIZE):
		dates = {row.name: _shop_datetime(row.created_at, site_zone, zone).strftime("%Y-%m-%d %H:%M:%S")
			for row in statements[start:start + BATCH_SIZE]}
		for record in _rows("transactions", {"tiktok_store": store, "statement": ["in", list(dates)]}):
			yield _display_row("transactions", record, site_zone, zone, statement_date=dates[record.statement])


@frappe.whitelist(methods=["GET"])
def get_analysis(view, store=None, month=None, search=None, page=1):
	if view not in REPORTS:
		frappe.throw("无效的 TikTok 分析页面。")
	doctype, stamp, _, _ = REPORTS[view]
	_check_read(doctype)
	period_view = "statements" if view == "transactions" else view
	period_doctype, period_stamp, _, _ = REPORTS[period_view]
	_check_read(period_doctype)
	stores, store = _scope(store)
	response = {"stores": stores, "store": store, "months": [], "rows": [], "summary": {}, "groups": [], "trend": {}, "total": 0, "page": 1, "pages": 1}
	if not store:
		return response

	zone = _zone(store)
	site_zone = ZoneInfo(get_system_timezone())
	today = datetime.now(zone).date().replace(day=1)
	latest = frappe.get_list(period_doctype, filters={"tiktok_store": store.name, period_stamp: ["is", "set"]},
		fields=[period_stamp], order_by=f"{period_stamp} desc", limit_page_length=1)
	latest_month = today
	if latest:
		stamped = latest[0][period_stamp]
		latest_month = (getdate(stamped) if period_stamp == "report_date" else _shop_datetime(stamped, site_zone, zone).date()).replace(day=1)
	if month and not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", month):
		frappe.throw("月份格式应为 YYYY-MM。")
	selected = date.fromisoformat(f"{month}-01") if month else latest_month
	months = {_month_label(_month_shift(selected, offset)) for offset in range(-5, 2)}
	months.update((_month_label(today), _month_label(latest_month)))
	response.update(month=_month_label(selected), months=sorted(months, reverse=True))

	filters = _month_filters(store.name, period_stamp, selected, site_zone, zone)
	if view == "transactions":
		rows = _finance_transactions(filters, store.name, site_zone, zone)
	else:
		rows = (_display_row(view, row, site_zone, zone) for row in _rows(view, filters))
	query = (search or "").strip().casefold()[:100]
	rows = [row for row in rows if _matches(query, (
		row["date"], row.get("source_order_id"), row.get("statement_id"), row.get("payment_id"), row.get("aftersales_id"),
		row.get("transaction_id"), row.get("status"), row.get("reason"), row.get("type"), row.get("transaction_type"),
		*(value for item in row.get("items", ()) for value in (item["sku"], item["product"])),
	))]
	rows.sort(key=lambda row: (row["date"], row["name"]), reverse=True)
	page_count = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
	page = min(max(cint(page), 1), page_count)
	start = (page - 1) * PAGE_SIZE
	summary, groups, trend = _analysis(view, rows)
	visible_rows = rows[start:start + PAGE_SIZE]
	if view == "aftersales" and frappe.has_permission(ORDER, "read"):
		order_ids = sorted({row["source_order_id"] for row in visible_rows if row["source_order_id"]})
		if order_ids:
			orders = frappe.get_list(ORDER, filters={"tiktok_store": store.name, "source_order_id": ["in", order_ids]},
				fields=["source_order_id", "name"], limit_page_length=0)
			order_names = {row.source_order_id: row.name for row in orders}
			for row in visible_rows:
				row["order_record"] = order_names.get(row["source_order_id"])
	response.update(rows=visible_rows, page=page, pages=page_count, total=len(rows),
		summary=summary, groups=groups, trend=trend)
	return response


@frappe.whitelist(methods=["GET"])
def get_finance_details(parent_type, parent, page=1):
	if parent_type == "payouts":
		parent_doctype, child, relation = PAYOUT, "statements", "payment_id"
	elif parent_type == "statements":
		parent_doctype, child, relation = STATEMENT, "transactions", "statement"
	else:
		frappe.throw("无效的 TikTok 财务明细。")
	doc = frappe.get_doc(parent_doctype, parent)
	doc.check_permission("read")
	frappe.get_doc(STORE, doc.tiktok_store).check_permission("read")
	_, store = _scope(doc.tiktok_store)
	child_doctype, stamp, money, extra = REPORTS[child]
	_check_read(child_doctype)
	key = doc.payment_id if parent_type == "payouts" else doc.name
	if not key:
		return {"view": child, "rows": [], "page": 1, "more": False}
	page = max(cint(page), 1)
	records = frappe.get_list(
		child_doctype, fields=["name", "tiktok_store", stamp, money, "currency", "raw_json", *extra],
		filters={"tiktok_store": doc.tiktok_store, relation: key}, order_by=f"{stamp} desc, name desc",
		limit_start=(page - 1) * PAGE_SIZE, limit_page_length=PAGE_SIZE + 1,
	)
	site_zone = ZoneInfo(get_system_timezone())
	zone = _zone(store)
	parent_stamp = _shop_datetime(doc.created_at, site_zone, zone)
	statement_date = parent_stamp.strftime("%Y-%m-%d %H:%M:%S") if child == "transactions" and parent_stamp else None
	return {"view": child, "rows": [
		_display_row(child, row, site_zone, zone, statement_date=statement_date) for row in records[:PAGE_SIZE]
	], "page": page, "more": len(records) > PAGE_SIZE}
