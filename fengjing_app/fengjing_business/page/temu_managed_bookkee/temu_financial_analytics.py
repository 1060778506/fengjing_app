from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, getdate


BATCH_DOCTYPE = "Temu Financial Reconciliation Batch"
MAPPING_DOCTYPE = "Fengjing - Product Corresponding Platform - Main Table"

PENDING_DOCTYPE = "Temu Pending Settlement Detail"
TRANSACTION_DOCTYPE = "Temu Transaction Settlement Detail"
PROTECTION_DOCTYPE = "Temu Fulfillment Protection Detail"
LEDGER_DOCTYPE = "Temu Accounting Ledger Detail"
AD_STORE_DOCTYPE = "Temu Advertising Store Daily"
AD_PRODUCT_DOCTYPE = "Temu Advertising Product Report"
AD_RECONCILIATION_DOCTYPE = "Temu Advertising Reconciliation Daily"
AD_PAYMENT_DOCTYPE = "Temu Advertising Payment Transaction"


def _as_filters(value=None):
	if isinstance(value, str):
		value = frappe.parse_json(value)
	return frappe._dict(value or {})


def _money(value):
	return flt(value or 0, 6)


def _text(value):
	return str(value or "").strip()


def _key(value):
	return _text(value).casefold()


def _day(value):
	if not value:
		return ""
	return str(getdate(value))


def _require_read(*doctypes):
	for doctype in doctypes:
		frappe.has_permission(doctype, "read", throw=True)


def _all_batches():
	_require_read(BATCH_DOCTYPE)
	return frappe.get_list(
		BATCH_DOCTYPE,
		fields=["name", "start_date", "end_date", "cost_center", "status", "file_count", "modified"],
		order_by="start_date desc, modified desc",
		limit_page_length=500,
	)


def _batch_scope(filters):
	batches = _all_batches()
	if filters.batch:
		batches = [row for row in batches if row.name == filters.batch]
	if filters.cost_center:
		batches = [row for row in batches if row.cost_center == filters.cost_center]
	return batches


def _base_db_filters(filters, batches, date_field=None, has_region=True, has_currency=True):
	batch_names = [row.name for row in batches]
	if not batch_names:
		return None
	db_filters = {"reconciliation_batch": ["in", batch_names]}
	if has_region and filters.region:
		db_filters["region"] = filters.region
	if has_currency and filters.currency:
		db_filters["currency"] = filters.currency
	if date_field:
		if filters.date_from and filters.date_to:
			db_filters[date_field] = ["between", [filters.date_from, f"{filters.date_to} 23:59:59"]]
		elif filters.date_from:
			db_filters[date_field] = [">=", filters.date_from]
		elif filters.date_to:
			db_filters[date_field] = ["<=", f"{filters.date_to} 23:59:59"]
	return db_filters


def _rows(doctype, fields, filters, batches, date_field=None, has_region=True, has_currency=True):
	_require_read(doctype)
	db_filters = _base_db_filters(filters, batches, date_field, has_region, has_currency)
	if db_filters is None:
		return []
	return frappe.get_list(
		doctype,
		filters=db_filters,
		fields=fields,
		order_by=f"{date_field} asc" if date_field else "modified asc",
		limit_page_length=100000,
	)


class TemuItemResolver:
	def __init__(self, batches):
		self.batch_store = {row.name: _text(row.cost_center) for row in batches}
		stores = sorted({store for store in self.batch_store.values() if store})
		self.sku_index = defaultdict(dict)
		self.product_index = defaultdict(dict)
		self.item_info = {}
		if not stores or not frappe.has_permission(MAPPING_DOCTYPE, "read"):
			return
		mappings = frappe.get_list(
			MAPPING_DOCTYPE,
			filters={"启用": 1, "店铺": ["in", stores]},
			fields=["店铺", "平台asin", "平台sku", "物料id", "物料名称"],
			limit_page_length=100000,
		)
		item_codes = set()
		for mapping in mappings:
			store = _text(mapping.get("店铺"))
			if mapping.get("平台sku"):
				self.sku_index[store][_key(mapping.get("平台sku"))] = mapping
			if mapping.get("平台asin"):
				self.product_index[store][_key(mapping.get("平台asin"))] = mapping
			if mapping.get("物料id"):
				item_codes.add(mapping.get("物料id"))
		if item_codes:
			for item in frappe.get_all(
				"Item",
				filters={"name": ["in", sorted(item_codes)]},
				fields=["name", "item_name", "image"],
				limit_page_length=100000,
			):
				self.item_info[item.name] = item

	def resolve(self, row):
		store = self.batch_store.get(row.get("reconciliation_batch"), "")
		mapping = None
		for value in (row.get("sku_code"), row.get("sku_id")):
			if value and _key(value) in self.sku_index.get(store, {}):
				mapping = self.sku_index[store][_key(value)]
				break
		if not mapping:
			for value in (row.get("product_id"), row.get("spu_id"), row.get("sku_id")):
				if value and _key(value) in self.product_index.get(store, {}):
					mapping = self.product_index[store][_key(value)]
					break
		if not mapping:
			return {"item_code": "", "item_name": "", "item_image": "", "is_bound": 0, "cost_center": store}
		item_code = mapping.get("物料id") or ""
		item = self.item_info.get(item_code)
		return {
			"item_code": item_code,
			"item_name": (item.item_name if item else None) or mapping.get("物料名称") or "",
			"item_image": (item.image if item else None) or "",
			"is_bound": 1 if item_code else 0,
			"cost_center": store,
		}


def _options(all_batches, rows=()):
	regions = sorted({_text(row.get("region")) for row in rows if row.get("region")})
	currencies = sorted({_text(row.get("currency")) for row in rows if row.get("currency")})
	cost_centers = sorted({_text(row.cost_center) for row in all_batches if row.cost_center})
	return {
		"batches": [
			{
				"value": row.name,
				"label": f"{row.name} · {_day(row.start_date) or '未设置'} 至 {_day(row.end_date) or '未设置'}",
				"cost_center": row.cost_center or "",
				"date_from": _day(row.start_date),
				"date_to": _day(row.end_date),
			}
			for row in all_batches
		],
		"cost_centers": cost_centers,
		"regions": regions,
		"currencies": currencies,
	}


def _product_identity(row):
	return _text(row.get("sku_id") or row.get("sku_code") or row.get("product_id") or row.get("spu_id"))


@frappe.whitelist()
def get_overview(filters=None):
	filters = _as_filters(filters)
	all_batches = _all_batches()
	batches = _batch_scope(filters)
	pending = _rows(
		PENDING_DOCTYPE,
		["reconciliation_batch", "region", "sku_id", "sku_code", "product_name", "sales_quantity", "currency", "estimated_pending_sales"],
		filters, batches,
	)
	transactions = _rows(
		TRANSACTION_DOCTYPE,
		["reconciliation_batch", "region", "sku_id", "sku_code", "product_name", "quantity", "currency", "transaction_type", "amount", "accounting_time"],
		filters, batches, "accounting_time",
	)
	protections = _rows(
		PROTECTION_DOCTYPE,
		["reconciliation_batch", "region", "sku_id", "sku_code", "product_name", "quantity", "currency", "protection_type", "compensation_amount", "accounting_time"],
		filters, batches, "accounting_time",
	)
	ledgers = _rows(
		LEDGER_DOCTYPE,
		["reconciliation_batch", "currency", "accounting_type", "income_expense_amount", "accounting_time"],
		filters, batches, "accounting_time", has_region=False,
	)
	ad_stores = _rows(
		AD_STORE_DOCTYPE,
		["reconciliation_batch", "region", "record_type", "report_date", "currency", "total_spend", "declared_sales_amount", "suborder_count", "units"],
		filters, batches, "report_date",
	)
	ad_reconciliation = _rows(
		AD_RECONCILIATION_DOCTYPE,
		["reconciliation_batch", "region", "report_date", "currency", "period_spend", "period_paid_amount", "period_discount_amount", "period_refund_red_packet_amount"],
		filters, batches, "report_date",
	)
	ad_payments = _rows(
		AD_PAYMENT_DOCTYPE,
		["reconciliation_batch", "region", "payment_time", "currency", "flow_type", "transaction_amount", "payment_status"],
		filters, batches, "payment_time",
	)

	resolver = TemuItemResolver(batches)
	currencies = defaultdict(lambda: defaultdict(float))
	daily = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
	regions = defaultdict(lambda: defaultdict(lambda: {"pending": 0.0, "settled": 0.0, "transactions": 0}))
	types = defaultdict(lambda: defaultdict(lambda: {"amount": 0.0, "count": 0}))
	products = {}
	bound_identifiers = set()
	all_identifiers = set()

	def product(row):
		identity = _product_identity(row)
		if not identity:
			return None
		currency = _text(row.get("currency")) or "未知"
		key = (row.get("reconciliation_batch"), row.get("region") or "未分类", currency, identity)
		if key not in products:
			mapping = resolver.resolve(row)
			products[key] = {
				"sku": identity,
				"sku_code": row.get("sku_code") or "",
				"product_name": row.get("product_name") or "",
				"currency": currency,
				"region": row.get("region") or "",
				"pending": 0.0,
				"settled": 0.0,
				"quantity": 0.0,
				**mapping,
			}
		all_identifiers.add(key)
		if products[key]["is_bound"]:
			bound_identifiers.add(key)
		return products[key]

	for row in pending:
		currency = _text(row.currency) or "未知"
		amount = _money(row.estimated_pending_sales)
		currencies[currency]["pending"] += amount
		regions[row.region or "未分类"][currency]["pending"] += amount
		entry = product(row)
		if entry:
			entry["pending"] += amount
			entry["quantity"] += _money(row.sales_quantity)
	for row in transactions:
		currency = _text(row.currency) or "未知"
		amount = _money(row.amount)
		currencies[currency]["settlement"] += amount
		currencies[currency]["settlement_in"] += max(amount, 0)
		currencies[currency]["settlement_out"] += min(amount, 0)
		day = _day(row.accounting_time)
		if day:
			daily[day][currency]["settlement"] += amount
		regions[row.region or "未分类"][currency]["settled"] += amount
		regions[row.region or "未分类"][currency]["transactions"] += 1
		types[row.transaction_type or "未分类"][currency]["amount"] += amount
		types[row.transaction_type or "未分类"][currency]["count"] += 1
		entry = product(row)
		if entry:
			entry["settled"] += amount
			entry["quantity"] += _money(row.quantity)
	for row in protections:
		currency = _text(row.currency) or "未知"
		currencies[currency]["protection"] += _money(row.compensation_amount)
	for row in ledgers:
		currency = _text(row.currency) or "未知"
		amount = _money(row.income_expense_amount)
		currencies[currency]["ledger"] += amount
		currencies[currency]["ledger_in"] += max(amount, 0)
		currencies[currency]["ledger_out"] += min(amount, 0)
		day = _day(row.accounting_time)
		if day:
			daily[day][currency]["ledger"] += amount
	for row in ad_stores:
		if row.record_type == "汇总":
			continue
		currency = _text(row.currency) or "未知"
		currencies[currency]["ad_spend"] += _money(row.total_spend)
		currencies[currency]["ad_sales"] += _money(row.declared_sales_amount)
		currencies[currency]["ad_orders"] += cint(row.suborder_count)
		day = _day(row.report_date)
		if day:
			daily[day][currency]["ad_spend"] += _money(row.total_spend)
			daily[day][currency]["ad_sales"] += _money(row.declared_sales_amount)
	for row in ad_reconciliation:
		currency = _text(row.currency) or "未知"
		currencies[currency]["ad_paid"] += _money(row.period_paid_amount)
		currencies[currency]["ad_discount"] += _money(row.period_discount_amount)
	for row in ad_payments:
		currency = _text(row.currency) or "未知"
		currencies[currency]["ad_payment_flow"] += _money(row.transaction_amount)

	product_rows = sorted(products.values(), key=lambda row: abs(row["settled"]) + abs(row["pending"]), reverse=True)[:30]
	return {
		"options": _options(all_batches, [*pending, *transactions, *protections, *ledgers, *ad_stores]),
		"currencies": {currency: dict(values) for currency, values in sorted(currencies.items())},
		"daily": [
			{"date": date, "currency": currency, **dict(values)}
			for date, currency_values in sorted(daily.items())
			for currency, values in sorted(currency_values.items())
		],
		"regions": [
			{"region": region, "currency": currency, **values}
			for region, currency_values in sorted(regions.items())
			for currency, values in sorted(currency_values.items())
		],
		"transaction_types": [
			{"name": name, "currency": currency, **values}
			for name, currency_values in types.items()
			for currency, values in currency_values.items()
		],
		"products": product_rows,
		"mapping": {
			"total": len(all_identifiers),
			"bound": len(bound_identifiers),
			"rate": round(len(bound_identifiers) * 100 / len(all_identifiers), 2) if all_identifiers else 0,
		},
		"counts": {
			"pending": len(pending),
			"transactions": len(transactions),
			"protections": len(protections),
			"ledgers": len(ledgers),
			"ad_store": len(ad_stores),
			"ad_reconciliation": len(ad_reconciliation),
			"ad_payments": len(ad_payments),
		},
	}


@frappe.whitelist()
def get_reconciliation(filters=None, page=1, page_size=50):
	filters = _as_filters(filters)
	all_batches = _all_batches()
	batches = _batch_scope(filters)
	pending = _rows(
		PENDING_DOCTYPE,
		["name", "reconciliation_batch", "region", "stock_order_number", "sku_id", "sku_code", "product_name", "sales_quantity", "currency", "estimated_pending_sales"],
		filters, batches,
	)
	transactions = _rows(
		TRANSACTION_DOCTYPE,
		["name", "reconciliation_batch", "region", "sales_order_number", "after_sales_order_number", "stock_order_number", "sku_id", "sku_code", "product_name", "quantity", "currency", "transaction_type", "amount", "accounting_time"],
		filters, batches, "accounting_time",
	)
	protections = _rows(
		PROTECTION_DOCTYPE,
		["name", "reconciliation_batch", "region", "order_number", "sku_id", "sku_code", "product_name", "quantity", "currency", "protection_type", "compensation_amount", "accounting_time"],
		filters, batches, "accounting_time",
	)
	resolver = TemuItemResolver(batches)
	groups = {}

	def group(row):
		identity = _product_identity(row) or "无SKU"
		key = (row.reconciliation_batch, row.region or "未分类", row.currency or "未知", identity)
		if key not in groups:
			groups[key] = {
				"group_key": "|".join(key),
				"batch": row.reconciliation_batch,
				"region": row.region or "未分类",
				"currency": row.currency or "未知",
				"sku": identity,
				"sku_code": row.get("sku_code") or "",
				"product_name": row.get("product_name") or "",
				"pending_amount": 0.0,
				"settlement_amount": 0.0,
				"protection_amount": 0.0,
				"pending_quantity": 0.0,
				"settlement_quantity": 0.0,
				"transaction_count": 0,
				"types": set(),
				**resolver.resolve(row),
			}
		return groups[key]

	for row in pending:
		entry = group(row)
		entry["pending_amount"] += _money(row.estimated_pending_sales)
		entry["pending_quantity"] += _money(row.sales_quantity)
	for row in transactions:
		entry = group(row)
		entry["settlement_amount"] += _money(row.amount)
		entry["settlement_quantity"] += _money(row.quantity)
		entry["transaction_count"] += 1
		entry["types"].add(row.transaction_type or "未分类")
	for row in protections:
		entry = group(row)
		entry["protection_amount"] += _money(row.compensation_amount)
		entry["types"].add(row.protection_type or "履约保障")

	rows = []
	search = _key(filters.search)
	for entry in groups.values():
		entry["difference"] = entry["settlement_amount"] + entry["protection_amount"] - entry["pending_amount"]
		entry["types"] = "、".join(sorted(entry["types"]))
		if entry["pending_amount"] and entry["transaction_count"]:
			entry["status"] = "都有数据"
		elif entry["pending_amount"]:
			entry["status"] = "仅待结算"
		elif entry["transaction_count"]:
			entry["status"] = "仅结算"
		else:
			entry["status"] = "仅履约保障"
		if filters.binding == "bound" and not entry["is_bound"]:
			continue
		if filters.binding == "unbound" and entry["is_bound"]:
			continue
		if filters.status and entry["status"] != filters.status:
			continue
		haystack = _key(" ".join(_text(entry.get(field)) for field in ("sku", "sku_code", "product_name", "item_code", "item_name", "types")))
		if search and search not in haystack:
			continue
		rows.append(entry)
	rows.sort(key=lambda row: (not row["is_bound"], -abs(row["difference"]), row["sku"]))
	summary_by_currency = defaultdict(
		lambda: {"pending": 0.0, "settled": 0.0, "protection": 0.0, "difference": 0.0, "groups": 0, "unbound": 0}
	)
	for row in rows:
		summary = summary_by_currency[row["currency"]]
		summary["pending"] += row["pending_amount"]
		summary["settled"] += row["settlement_amount"]
		summary["protection"] += row["protection_amount"]
		summary["difference"] += row["difference"]
		summary["groups"] += 1
		summary["unbound"] += 0 if row["is_bound"] else 1

	page = max(cint(page), 1)
	page_size = min(max(cint(page_size), 20), 200)
	total = len(rows)
	paged = rows[(page - 1) * page_size: page * page_size]

	details = []
	for row in sorted(transactions, key=lambda value: get_datetime(value.accounting_time), reverse=True)[:200]:
		mapping = resolver.resolve(row)
		details.append({
			"name": row.name,
			"batch": row.reconciliation_batch,
			"accounting_time": row.accounting_time,
			"region": row.region or "",
			"order_number": row.sales_order_number or row.after_sales_order_number or row.stock_order_number or "",
			"sku": _product_identity(row),
			"sku_code": row.sku_code or "",
			"product_name": row.product_name or "",
			"quantity": _money(row.quantity),
			"currency": row.currency or "",
			"transaction_type": row.transaction_type or "",
			"amount": _money(row.amount),
			**mapping,
		})

	return {
		"options": {
			**_options(all_batches, [*pending, *transactions, *protections]),
			"transaction_types": sorted({row.transaction_type for row in transactions if row.transaction_type}),
		},
		"summary": {
			"pending": sum(row["pending_amount"] for row in rows),
			"settled": sum(row["settlement_amount"] for row in rows),
			"protection": sum(row["protection_amount"] for row in rows),
			"difference": sum(row["difference"] for row in rows),
			"groups": total,
			"unbound": sum(1 for row in rows if not row["is_bound"]),
		},
		"summary_by_currency": dict(summary_by_currency),
		"rows": paged,
		"details": details,
		"total": total,
		"page": page,
		"page_size": page_size,
		"pages": max(1, (total + page_size - 1) // page_size),
	}


@frappe.whitelist()
def get_ad_analysis(filters=None, page=1, page_size=50):
	filters = _as_filters(filters)
	all_batches = _all_batches()
	batches = _batch_scope(filters)
	store_rows = _rows(
		AD_STORE_DOCTYPE,
		["reconciliation_batch", "region", "record_type", "report_date", "currency", "total_spend", "net_total_spend", "declared_sales_amount", "roas", "expense_ratio", "cost_per_order", "suborder_count", "units", "impressions", "clicks", "click_through_rate", "conversion_rate", "add_to_cart_count"],
		filters, batches, "report_date",
	)
	product_rows = _rows(
		AD_PRODUCT_DOCTYPE,
		["name", "reconciliation_batch", "region", "record_type", "period_start", "period_end", "product_name", "product_id", "spu_id", "currency", "spend", "net_spend", "declared_sales_amount", "roas", "expense_ratio", "cost_per_order", "suborder_count", "units", "impressions", "clicks", "click_through_rate", "conversion_rate", "add_to_cart_count"],
		filters, batches, "period_start",
	)
	reconciliation_rows = _rows(
		AD_RECONCILIATION_DOCTYPE,
		["reconciliation_batch", "region", "report_date", "currency", "period_spend", "period_paid_amount", "period_discount_amount", "period_refund_red_packet_amount"],
		filters, batches, "report_date",
	)
	payment_rows = _rows(
		AD_PAYMENT_DOCTYPE,
		["name", "reconciliation_batch", "region", "payment_time", "settlement_number", "site", "settlement_method", "flow_type", "currency", "transaction_amount", "operator", "payment_status"],
		filters, batches, "payment_time",
	)
	resolver = TemuItemResolver(batches)
	daily = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
	currencies = defaultdict(lambda: defaultdict(float))
	for row in store_rows:
		if row.record_type == "汇总":
			continue
		currency = row.currency or "未知"
		day = _day(row.report_date)
		for field in ("total_spend", "net_total_spend", "declared_sales_amount", "suborder_count", "units", "impressions", "clicks", "add_to_cart_count"):
			value = _money(row.get(field))
			daily[day][currency][field] += value
			currencies[currency][field] += value
	for row in reconciliation_rows:
		currency = row.currency or "未知"
		day = _day(row.report_date)
		for field in ("period_spend", "period_paid_amount", "period_discount_amount", "period_refund_red_packet_amount"):
			value = _money(row.get(field))
			daily[day][currency][field] += value
			currencies[currency][field] += value
	flows = defaultdict(lambda: defaultdict(lambda: {"amount": 0.0, "count": 0}))
	for row in payment_rows:
		currency = row.currency or "未知"
		flows[row.flow_type or "未分类"][currency]["amount"] += _money(row.transaction_amount)
		flows[row.flow_type or "未分类"][currency]["count"] += 1
		currencies[currency]["payment_flow"] += _money(row.transaction_amount)

	products = []
	search = _key(filters.search)
	for row in product_rows:
		if row.record_type == "汇总":
			continue
		mapping = resolver.resolve(row)
		entry = {
			"name": row.name,
			"batch": row.reconciliation_batch,
			"period_start": row.period_start,
			"period_end": row.period_end,
			"product_name": row.product_name or "",
			"product_id": row.product_id or "",
			"spu_id": row.spu_id or "",
			"currency": row.currency or "",
			"spend": _money(row.spend),
			"net_spend": _money(row.net_spend),
			"sales": _money(row.declared_sales_amount),
			"roas": _money(row.roas),
			"expense_ratio": _money(row.expense_ratio),
			"orders": cint(row.suborder_count),
			"units": cint(row.units),
			"impressions": cint(row.impressions),
			"clicks": cint(row.clicks),
			"ctr": _money(row.click_through_rate),
			"conversion_rate": _money(row.conversion_rate),
			**mapping,
		}
		if filters.binding == "bound" and not entry["is_bound"]:
			continue
		if filters.binding == "unbound" and entry["is_bound"]:
			continue
		haystack = _key(" ".join(_text(entry.get(field)) for field in ("product_name", "product_id", "spu_id", "item_code", "item_name")))
		if search and search not in haystack:
			continue
		products.append(entry)
	products.sort(key=lambda row: (not row["is_bound"], -row["spend"], -row["sales"]))
	page = max(cint(page), 1)
	page_size = min(max(cint(page_size), 20), 200)
	total = len(products)
	return {
		"options": _options(all_batches, [*store_rows, *product_rows, *reconciliation_rows, *payment_rows]),
		"currencies": {currency: dict(values) for currency, values in sorted(currencies.items())},
		"daily": [
			{"date": date, "currency": currency, **dict(values)}
			for date, currency_values in sorted(daily.items()) if date
			for currency, values in sorted(currency_values.items())
		],
		"flows": [
			{"name": name, "currency": currency, **values}
			for name, currency_values in flows.items()
			for currency, values in currency_values.items()
		],
		"products": products[(page - 1) * page_size: page * page_size],
		"total": total,
		"page": page,
		"page_size": page_size,
		"pages": max(1, (total + page_size - 1) // page_size),
		"counts": {
			"store_daily": len([row for row in store_rows if row.record_type != "汇总"]),
			"products": len([row for row in product_rows if row.record_type != "汇总"]),
			"reconciliation": len(reconciliation_rows),
			"payments": len(payment_rows),
			"unbound_products": sum(1 for row in products if not row["is_bound"]),
		},
	}
