from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
import re

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


def _cents(value):
	return int((Decimal(str(value or 0)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _allocate_ad_wallet_change(entries, total_cents):
	"""Allocate a batch's actual wallet movement by product net spend, in cents."""
	weights = [max(_cents(row["net_spend"]), 0) for row in entries]
	weight_total = sum(weights)
	if not weight_total:
		return False
	abs_total = abs(total_cents)
	shares = [abs_total * weight // weight_total for weight in weights]
	remainders = [abs_total * weight % weight_total for weight in weights]
	for index in sorted(range(len(entries)), key=lambda i: (-remainders[i], entries[i]["name"]))[:abs_total - sum(shares)]:
		shares[index] += 1
	sign = -1 if total_cents < 0 else 1
	for row, share in zip(entries, shares):
		row["actual_wallet_change"] = sign * share / 100
	return True


def _protection_signed_amount(value):
	# The source sheet lists compensation as a positive amount; Temu's ledger
	# records it as an expense, so use the cash-flow sign in financial views.
	return -_money(value)


def _text(value):
	return str(value or "").strip()


def _ledger_remark_group(value):
	remark = _text(value)
	# Import notes may end with a generated statement ID, such as
	# 合规EPR物流包装环保费（634418211291203_1053_2025-08_0_-1_0_0）.
	remark = re.sub(r"\s*[（(]\s*\d{6,}_[^（）()]*[）)]\s*$", "", remark).strip()
	return remark or "无备注"


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
		self.sku_index = defaultdict(lambda: defaultdict(list))
		self.product_index = defaultdict(lambda: defaultdict(list))
		self.spu_index = defaultdict(lambda: defaultdict(list))
		self.item_info = {}
		if not stores or not frappe.has_permission(MAPPING_DOCTYPE, "read"):
			return
		mappings = frappe.get_list(
			MAPPING_DOCTYPE,
			filters={"启用": 1, "店铺": ["in", stores]},
			fields=["店铺", "平台asin", "平台sku", "平台spu", "物料id", "物料名称"],
			limit_page_length=100000,
		)
		item_codes = set()
		for mapping in mappings:
			store = _text(mapping.get("店铺"))
			if mapping.get("平台sku"):
				self.sku_index[store][_key(mapping.get("平台sku"))].append(mapping)
			if mapping.get("平台asin"):
				self.product_index[store][_key(mapping.get("平台asin"))].append(mapping)
			if mapping.get("平台spu"):
				self.spu_index[store][_key(mapping.get("平台spu"))].append(mapping)
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
		mappings = []
		mapping_basis = ""
		for value in (row.get("sku_code"), row.get("sku_id")):
			matches = self.sku_index.get(store, {}).get(_key(value)) if value else None
			if matches:
				mappings = matches
				mapping_basis = "SKU"
				break
		if not mappings:
			for value in (row.get("product_id"), row.get("sku_id")):
				matches = self.product_index.get(store, {}).get(_key(value)) if value else None
				if matches:
					mappings = matches
					mapping_basis = "商品ID"
					break
		if not mappings and row.get("spu_id"):
			mappings = self.spu_index.get(store, {}).get(_key(row.get("spu_id")), [])
			if mappings:
				mapping_basis = "SPU"
		if not mappings and row.get("spu_id"):
			# 兼容曾经把SPU保存在“平台ASIN/商品ID”中的旧映射。
			mappings = self.product_index.get(store, {}).get(_key(row.get("spu_id")), [])
			if mappings:
				mapping_basis = "商品ID（兼容SPU）"

		items = []
		seen = set()
		for mapping in mappings:
			item_code = mapping.get("物料id") or ""
			if not item_code or item_code in seen:
				continue
			seen.add(item_code)
			item = self.item_info.get(item_code)
			items.append(
				{
					"item_code": item_code,
					"item_name": (item.item_name if item else None) or mapping.get("物料名称") or "",
					"item_image": (item.image if item else None) or "",
				}
			)
		primary = items[0] if items else {}
		return {
			"item_code": primary.get("item_code", ""),
			"item_name": primary.get("item_name", ""),
			"item_image": primary.get("item_image", ""),
			"items": items,
			"item_count": len(items),
			"is_multi_item": 1 if len(items) > 1 else 0,
			"is_bound": 1 if items else 0,
			"mapping_basis": mapping_basis,
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


def _reconciliation_available_months(filters, batches):
	if not batches:
		return []
	params = {"batch_names": tuple(batch.name for batch in batches)}
	conditions = ["reconciliation_batch IN %(batch_names)s"]
	if filters.region:
		conditions.append("region = %(region)s")
		params["region"] = filters.region
	if filters.currency:
		conditions.append("currency = %(currency)s")
		params["currency"] = filters.currency
	where = " AND ".join(conditions)
	months = set()
	for doctype in (TRANSACTION_DOCTYPE, PROTECTION_DOCTYPE):
		months.update(
			_text(row.month)
			for row in frappe.db.sql(
				f"SELECT DISTINCT LEFT(accounting_time, 7) AS month FROM `tab{doctype}` "
				f"WHERE {where} AND accounting_time IS NOT NULL",
				params,
				as_dict=True,
			)
			if row.month
		)
	undated_batches = set()
	for doctype, extra in ((PENDING_DOCTYPE, ""), (PROTECTION_DOCTYPE, " AND accounting_time IS NULL")):
		undated_batches.update(
			row.reconciliation_batch
			for row in frappe.db.sql(
				f"SELECT DISTINCT reconciliation_batch FROM `tab{doctype}` WHERE {where}{extra}",
				params,
				as_dict=True,
			)
		)
	for batch in batches:
		if batch.name in undated_batches and batch.start_date and batch.end_date:
			start_month, end_month = _day(batch.start_date)[:7], _day(batch.end_date)[:7]
			if start_month == end_month:
				months.add(start_month)
	return sorted(month for month in months if len(month) == 7)


def _overview_available_months(filters, batches):
	months = set(_reconciliation_available_months(filters, batches))
	if not batches:
		return []
	params = {"batch_names": tuple(batch.name for batch in batches)}
	for doctype, date_field, has_region in (
		(LEDGER_DOCTYPE, "accounting_time", False),
		(AD_STORE_DOCTYPE, "report_date", True),
		(AD_RECONCILIATION_DOCTYPE, "report_date", True),
		(AD_PAYMENT_DOCTYPE, "payment_time", True),
	):
		conditions = ["reconciliation_batch IN %(batch_names)s", f"{date_field} IS NOT NULL"]
		if filters.currency:
			conditions.append("currency = %(currency)s")
			params["currency"] = filters.currency
		if has_region and filters.region:
			conditions.append("region = %(region)s")
			params["region"] = filters.region
		where = " AND ".join(conditions)
		months.update(
			_text(row.month)
			for row in frappe.db.sql(
				f"SELECT DISTINCT LEFT({date_field}, 7) AS month FROM `tab{doctype}` WHERE {where}",
				params,
				as_dict=True,
			)
			if row.month
		)
	return sorted(month for month in months if len(month) == 7)


@frappe.whitelist()
def get_overview(filters=None):
	filters = _as_filters(filters)
	all_batches = _all_batches()
	batches = _batch_scope(filters)
	batch_by_name = {batch.name: batch for batch in batches}
	pending = _rows(
		PENDING_DOCTYPE,
		["reconciliation_batch", "region", "currency", "estimated_pending_sales"],
		filters, batches,
	)
	transactions = _rows(
		TRANSACTION_DOCTYPE,
		["reconciliation_batch", "region", "currency", "transaction_type", "amount", "accounting_time"],
		filters, batches, "accounting_time",
	)
	protections = _rows(
		PROTECTION_DOCTYPE,
		["reconciliation_batch", "region", "currency", "compensation_amount", "accounting_time"],
		filters, batches,
	)
	date_from = getdate(filters.date_from) if filters.date_from else None
	date_to = getdate(filters.date_to) if filters.date_to else None
	if date_from or date_to:
		def batch_in_range(row):
			batch = batch_by_name.get(row.reconciliation_batch)
			return bool(
				batch and batch.start_date and batch.end_date
				and (not date_from or getdate(batch.start_date) >= date_from)
				and (not date_to or getdate(batch.end_date) <= date_to)
			)

		pending = [row for row in pending if batch_in_range(row)]
		protections = [
			row for row in protections
			if (
				(
					(not date_from or getdate(row.accounting_time) >= date_from)
					and (not date_to or getdate(row.accounting_time) <= date_to)
				) if row.accounting_time else batch_in_range(row)
			)
		]
	available_months = _overview_available_months(filters, batches)
	ledgers = _rows(
		LEDGER_DOCTYPE,
		["name", "reconciliation_batch", "currency", "accounting_type", "income_expense_amount", "accounting_time", "remarks"],
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

	currencies = defaultdict(lambda: defaultdict(float))
	daily = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
	regions = defaultdict(lambda: defaultdict(lambda: {"pending": 0.0, "settled": 0.0, "transactions": 0}))
	types = defaultdict(lambda: defaultdict(lambda: {"amount": 0.0, "count": 0}))
	ledger_groups = {}

	for row in pending:
		currency = _text(row.currency) or "未知"
		amount = _money(row.estimated_pending_sales)
		currencies[currency]["pending"] += amount
		regions[row.region or "未分类"][currency]["pending"] += amount
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
	for row in protections:
		currency = _text(row.currency) or "未知"
		amount = _protection_signed_amount(row.compensation_amount)
		currencies[currency]["protection"] += amount
	for row in ledgers:
		currency = _text(row.currency) or "未知"
		amount = _money(row.income_expense_amount)
		batch = batch_by_name.get(row.reconciliation_batch)
		cost_center = _text(batch.cost_center if batch else "") or "未指定成本中心"
		accounting_type = _text(row.accounting_type) or "未分类"
		remark = _ledger_remark_group(row.remarks)
		group_key = (currency, cost_center, accounting_type, remark)
		if group_key not in ledger_groups:
			ledger_groups[group_key] = {
				"currency": currency, "cost_center": cost_center, "accounting_type": accounting_type,
				"remarks": remark, "income": 0.0, "expense": 0.0,
				"amount": 0.0, "count": 0,
			}
		group = ledger_groups[group_key]
		group["income"] += max(amount, 0)
		group["expense"] += min(amount, 0)
		group["amount"] += amount
		group["count"] += 1
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

	return {
		"options": {
			**_options(all_batches, [*pending, *transactions, *protections, *ledgers, *ad_stores]),
			"available_months": available_months,
		},
		"currencies": {currency: dict(values) for currency, values in sorted(currencies.items())},
		"daily": [
			{"date": date, "currency": currency, **dict(values)}
			for date, currency_values in sorted(daily.items())
			for currency, values in sorted(currency_values.items())
		],
		"regions": [
			{
				"region": region, "currency": currency,
				"pending": round(values["pending"], 2),
				"settled": round(values["settled"], 2),
				"transactions": values["transactions"],
			}
			for region, currency_values in sorted(regions.items())
			for currency, values in sorted(currency_values.items())
		],
		"transaction_types": [
			{"name": name, "currency": currency, **values}
			for name, currency_values in types.items()
			for currency, values in currency_values.items()
		],
		"ledger_groups": [
			{**group, **{key: round(group[key], 2) for key in ("income", "expense", "amount")}}
			for group in sorted(ledger_groups.values(), key=lambda row: (-abs(row["amount"]), row["cost_center"], row["accounting_type"], row["remarks"]))
		],
		"ledger_details": [
			{
				"name": row.name,
				"accounting_time": str(row.accounting_time or ""),
				"currency": _text(row.currency) or "未知",
				"cost_center": (_text(batch_by_name[row.reconciliation_batch].cost_center) if row.reconciliation_batch in batch_by_name else "") or "未指定成本中心",
				"accounting_type": _text(row.accounting_type) or "未分类",
				"remarks": _text(row.remarks),
				"remark_group": _ledger_remark_group(row.remarks),
				"amount": _money(row.income_expense_amount),
			}
			for row in sorted(ledgers, key=lambda row: (str(row.accounting_time or ""), row.name), reverse=True)
		],
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
def get_reconciliation(filters=None, page=1, page_size=50, detail_region=None, detail_type=None):
	filters = _as_filters(filters)
	all_batches = _all_batches()
	batches = _batch_scope(filters)
	batch_by_name = {batch.name: batch for batch in batches}
	pending = _rows(
		PENDING_DOCTYPE,
		["name", "reconciliation_batch", "region", "stock_order_number", "sku_id", "sku_code", "product_name", "sales_quantity", "currency", "estimated_pending_sales"],
		filters, batches,
	)
	transactions = _rows(
		TRANSACTION_DOCTYPE,
		["name", "reconciliation_batch", "region", "sales_order_number", "after_sales_order_number", "stock_order_number", "sku_id", "sku_code", "product_name", "quantity", "currency", "transaction_type", "amount", "accounting_time", "is_checked"],
		filters, batches, "accounting_time",
	)
	# Compare the whole date range across all regions. The overview's settlement
	# is the sum of these same transaction amounts, regardless of SKU filters or
	# the 200-row limit on the detail list.
	comparison_transactions = transactions if not filters.region else _rows(
		TRANSACTION_DOCTYPE,
		["currency", "transaction_type", "amount"],
		frappe._dict({**filters, "region": ""}), batches, "accounting_time",
	)
	comparison_by_currency = defaultdict(lambda: {
		"sales_receipts": 0.0, "sales_reversals": 0.0,
		"non_seller_subsidy": 0.0, "overview_settlement": 0.0,
	})
	for row in comparison_transactions:
		entry = comparison_by_currency[_text(row.currency) or "未知"]
		amount = _money(row.amount)
		entry["overview_settlement"] += amount
		if row.transaction_type == "销售回款":
			entry["sales_receipts"] += amount
		elif row.transaction_type == "销售冲回":
			entry["sales_reversals"] -= amount  # Display the deduction as a positive number.
		elif row.transaction_type == "非商责补贴":
			entry["non_seller_subsidy"] += amount
	settlement_comparison = []
	for currency, entry in sorted(comparison_by_currency.items()):
		calculated = entry["sales_receipts"] - entry["sales_reversals"] + entry["non_seller_subsidy"]
		settlement_comparison.append({
			"currency": currency,
			**{key: _money(value) for key, value in entry.items()},
			"calculated": _money(calculated),
			"difference": _money(calculated - entry["overview_settlement"]),
		})
	protections = _rows(
		PROTECTION_DOCTYPE,
		["name", "reconciliation_batch", "region", "order_number", "business_number", "sku_id", "sku_code", "product_name", "quantity", "currency", "protection_type", "compensation_amount", "accounting_time", "is_checked"],
		filters, batches,
	)
	date_from = getdate(filters.date_from) if filters.date_from else None
	date_to = getdate(filters.date_to) if filters.date_to else None
	if date_from or date_to:
		def in_selected_dates(row):
			if row.accounting_time:
				day = getdate(row.accounting_time)
				return (not date_from or day >= date_from) and (not date_to or day <= date_to)
			batch = batch_by_name.get(row.reconciliation_batch)
			if not batch or not batch.start_date or not batch.end_date:
				return False
			# Undated fulfillment rows belong to their batch only when the selected
			# range covers the entire batch; never invent an individual posting date.
			return (not date_from or getdate(batch.start_date) >= date_from) and (
				not date_to or getdate(batch.end_date) <= date_to
			)

		protections = [row for row in protections if in_selected_dates(row)]
	available_months = _reconciliation_available_months(filters, batches)
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
		entry["protection_amount"] += _protection_signed_amount(row.compensation_amount)
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

	detail_region = _text(detail_region)
	detail_type = _text(detail_type)
	detail_records = [
		(doctype, row)
		for doctype, source in ((TRANSACTION_DOCTYPE, transactions), (PROTECTION_DOCTYPE, protections))
		for row in source
		if not detail_region or _text(row.region) == detail_region
	]

	def detail_record_type(doctype, row):
		return _text(row.transaction_type if doctype == TRANSACTION_DOCTYPE else row.protection_type) or "未分类"

	transaction_types = sorted({detail_record_type(doctype, row) for doctype, row in detail_records})
	if detail_type:
		detail_records = [
			(doctype, row) for doctype, row in detail_records
			if detail_record_type(doctype, row) == detail_type
		]

	# A return, subsidy, or after-sales issue can refer to a receipt from an
	# earlier batch. For issues, the violation ID identifies an after-sales
	# transaction, which in turn supplies the original sales order number.
	issue_numbers = sorted({
		_text(row.business_number) for doctype, row in detail_records
		if doctype == PROTECTION_DOCTYPE and row.protection_type == "售后问题" and row.business_number
	})
	after_sales_by_number = defaultdict(list)
	if issue_numbers:
		_require_read(TRANSACTION_DOCTYPE)
		for related in frappe.get_list(
			TRANSACTION_DOCTYPE,
			filters={"after_sales_order_number": ["in", issue_numbers]},
			fields=["name", "region", "sales_order_number", "after_sales_order_number", "sku_id", "sku_code", "currency"],
			limit_page_length=100000,
		):
			after_sales_by_number[_text(related.after_sales_order_number)].append(related)

	receipt_order_numbers = {
		_text(row.sales_order_number) for doctype, row in detail_records
		if doctype == TRANSACTION_DOCTYPE and row.transaction_type in ("销售冲回", "非商责补贴")
		and row.sales_order_number
	}
	receipt_order_numbers.update(
		_text(related.sales_order_number)
		for rows_for_issue in after_sales_by_number.values() for related in rows_for_issue
		if related.sales_order_number
	)
	receipts_by_order = defaultdict(list)
	if receipt_order_numbers:
		_require_read(TRANSACTION_DOCTYPE)
		for receipt in frappe.get_list(
			TRANSACTION_DOCTYPE,
			filters={"transaction_type": "销售回款", "sales_order_number": ["in", sorted(receipt_order_numbers)]},
			fields=["name", "region", "sales_order_number", "sku_id", "sku_code", "currency", "amount", "accounting_time"],
			order_by="accounting_time desc",
			limit_page_length=100000,
		):
			receipts_by_order[_text(receipt.sales_order_number)].append(receipt)

	def matching_receipt(doctype, row):
		identity = _product_identity(row)
		if not identity:
			return None
		if doctype == TRANSACTION_DOCTYPE and row.transaction_type in ("销售冲回", "非商责补贴"):
			order_numbers = [_text(row.sales_order_number)] if row.sales_order_number else []
		elif doctype == PROTECTION_DOCTYPE and row.protection_type == "售后问题":
			order_numbers = [
				_text(related.sales_order_number)
				for related in after_sales_by_number[_text(row.business_number)]
				if related.sales_order_number and related.region == row.region
				and related.currency == row.currency and _product_identity(related) == identity
			]
		else:
			return None
		return next((receipt for order_number in order_numbers
			for receipt in receipts_by_order[order_number]
			if receipt.region == row.region and receipt.currency == row.currency
			and _product_identity(receipt) == identity), None)
	summary_groups = {}
	transaction_amount_by_currency = defaultdict(float)
	protection_amount_by_currency = defaultdict(float)
	for doctype, row in detail_records:
		sku = _product_identity(row)
		type_name = detail_record_type(doctype, row)
		currency = row.currency or "未知"
		amount = _money(row.amount) if doctype == TRANSACTION_DOCTYPE else _protection_signed_amount(row.compensation_amount)
		if doctype == TRANSACTION_DOCTYPE:
			transaction_amount_by_currency[currency] += amount
		else:
			protection_amount_by_currency[currency] += amount
		region = row.region or "未分类"
		key = (sku or f"无SKU:{doctype}:{row.name}", type_name, currency, region)
		if key not in summary_groups:
			summary_groups[key] = {
				"sku": sku or "无SKU",
				"sku_code": row.sku_code or "",
				"product_name": row.product_name or "",
				"transaction_type": type_name,
				"currency": currency,
				"region": region,
				"count": 0,
				"quantity": 0.0,
				"amount": 0.0,
				"source_row": row,
			}
		entry = summary_groups[key]
		entry["count"] += 1
		entry["quantity"] += _money(row.quantity)
		entry["amount"] += amount
	detail_summary_rows = []
	for entry in summary_groups.values():
		entry.update(resolver.resolve(entry.pop("source_row")))
		entry["quantity"] = _money(entry["quantity"])
		entry["amount"] = _money(entry["amount"])
		detail_summary_rows.append(entry)
	detail_summary_rows.sort(key=lambda row: (row["sku"], row["transaction_type"], row["region"], row["currency"]))
	amount_by_currency = defaultdict(float)
	for entry in detail_summary_rows:
		amount_by_currency[entry["currency"]] += entry["amount"]
	details = []
	for doctype, row in sorted(detail_records, key=lambda value: get_datetime(value[1].accounting_time or "1900-01-01"), reverse=True)[:200]:
		mapping = resolver.resolve(row)
		receipt = matching_receipt(doctype, row)
		details.append({
			"name": row.name,
			"doctype": doctype,
			"batch": row.reconciliation_batch,
			"accounting_time": row.accounting_time,
			"batch_period": (
				f"{_day(batch_by_name[row.reconciliation_batch].start_date)} 至 {_day(batch_by_name[row.reconciliation_batch].end_date)}"
				if not row.accounting_time and row.reconciliation_batch in batch_by_name else ""
			),
			"region": row.region or "",
			"order_number": (
				row.sales_order_number or row.after_sales_order_number or row.stock_order_number
				if doctype == TRANSACTION_DOCTYPE else row.order_number or row.business_number
			) or "",
			"sku": _product_identity(row),
			"sku_code": row.sku_code or "",
			"product_name": row.product_name or "",
			"quantity": _money(row.quantity),
			"currency": row.currency or "",
			"transaction_type": detail_record_type(doctype, row),
			"amount": _money(row.amount) if doctype == TRANSACTION_DOCTYPE else _protection_signed_amount(row.compensation_amount),
			"original_receipt_amount": _money(receipt.amount) if receipt else None,
			"original_receipt_month": _day(receipt.accounting_time)[:7] if receipt else "",
			"is_checked": cint(row.is_checked),
			**mapping,
		})

	return {
		"options": {
			**_options(all_batches, [*pending, *transactions, *protections]),
			"available_months": available_months,
			"transaction_types": transaction_types,
		},
		"settlement_comparison": settlement_comparison,
		"detail_summary": {
			"count": len(detail_records),
			"groups": detail_summary_rows,
			"amount_by_currency": {currency: _money(amount) for currency, amount in sorted(amount_by_currency.items())},
			"transaction_amount_by_currency": {currency: _money(amount) for currency, amount in sorted(transaction_amount_by_currency.items())},
			"protection_amount_by_currency": {currency: _money(amount) for currency, amount in sorted(protection_amount_by_currency.items())},
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
def set_reconciliation_detail_checked(doctype, name, checked):
	if doctype not in (TRANSACTION_DOCTYPE, PROTECTION_DOCTYPE):
		frappe.throw(_("不支持此明细类型。"))
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("write")
	value = 1 if cint(checked) else 0
	frappe.db.set_value(doctype, name, "is_checked", value)
	return {"name": name, "is_checked": value}


@frappe.whitelist()
def set_reconciliation_details_checked(records, checked):
	if isinstance(records, str):
		records = frappe.parse_json(records)
	if not isinstance(records, list) or len(records) > 200 or not records:
		frappe.throw(_("请选择当前显示的明细（最多 200 条）。"))
	value = 1 if cint(checked) else 0
	seen = set()
	for record in records:
		if not isinstance(record, dict) or record.get("doctype") not in (TRANSACTION_DOCTYPE, PROTECTION_DOCTYPE) or not record.get("name"):
			frappe.throw(_("明细数据无效。"))
		key = (record["doctype"], record["name"])
		if key in seen:
			continue
		seen.add(key)
		doc = frappe.get_doc(*key)
		doc.check_permission("write")
		frappe.db.set_value(*key, "is_checked", value)
	return {"count": len(seen), "is_checked": value}


@frappe.whitelist()
def get_ad_analysis(filters=None, page=1, page_size=50):
	filters = _as_filters(filters)
	all_batches = _all_batches()
	batches = _batch_scope(filters)
	available_months = set()
	if batches:
		month_sources = (
			(AD_STORE_DOCTYPE, "report_date"),
			(AD_PRODUCT_DOCTYPE, "period_start"),
			(AD_RECONCILIATION_DOCTYPE, "report_date"),
			(AD_PAYMENT_DOCTYPE, "payment_time"),
		)
		for doctype, date_field in month_sources:
			for dated in frappe.get_list(
				doctype,
				filters={"reconciliation_batch": ["in", [batch.name for batch in batches]]},
				fields=[date_field],
				limit_page_length=100000,
			):
				if dated.get(date_field):
					available_months.add(str(dated[date_field])[:7])
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
	all_products = []
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
		all_products.append(entry)
		if filters.binding == "bound" and not entry["is_bound"]:
			continue
		if filters.binding == "unbound" and entry["is_bound"]:
			continue
		item_search = " ".join(
			f"{item.get('item_code', '')} {item.get('item_name', '')}"
			for item in entry.get("items", [])
		)
		haystack = _key(
			" ".join(
				[_text(entry.get(field)) for field in ("product_name", "product_id", "spu_id", "item_code", "item_name")]
				+ [item_search]
			)
		)
		if search and search not in haystack:
			continue
		products.append(entry)
	wallet_by_batch = defaultdict(int)
	for row in reconciliation_rows:
		wallet_by_batch[(row.reconciliation_batch, row.currency or "未知")] -= (
			_cents(row.period_paid_amount) - _cents(row.period_refund_red_packet_amount)
		)
	products_by_batch = defaultdict(list)
	for product in all_products:
		products_by_batch[(product["batch"], product["currency"] or "未知")].append(product)
	unallocated_wallet_change = defaultdict(int)
	for key, total_cents in wallet_by_batch.items():
		if not _allocate_ad_wallet_change(products_by_batch[key], total_cents):
			unallocated_wallet_change[key[1]] += total_cents
	products.sort(key=lambda row: (not row["is_bound"], -row["spend"], -row["sales"]))
	product_groups = {}
	for product in products:
		key = (
			product["cost_center"], product["product_id"] or product["spu_id"] or product["name"],
			product["spu_id"], product["currency"],
		)
		group = product_groups.get(key)
		if group is None:
			group = {field: product.get(field) for field in (
				"cost_center", "product_name", "product_id", "spu_id", "currency", "item_code",
				"item_name", "item_image", "items", "is_bound",
			)}
			group.update(spend=0, actual_wallet_change=0, allocation_available=True, sales=0, count=0)
			product_groups[key] = group
		group["spend"] += product["spend"]
		if product.get("actual_wallet_change") is None:
			group["allocation_available"] = False
		group["actual_wallet_change"] += product.get("actual_wallet_change") or 0
		group["sales"] += product["sales"]
		group["count"] += 1
	product_summary = sorted(product_groups.values(), key=lambda group: (-group["spend"], -group["sales"]))
	page = max(cint(page), 1)
	page_size = min(max(cint(page_size), 20), 200)
	total = len(products)
	return {
		"options": {**_options(all_batches, [*store_rows, *product_rows, *reconciliation_rows, *payment_rows]), "available_months": sorted(available_months)},
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
		"product_summary": product_summary,
		"unallocated_wallet_change": {currency: cents / 100 for currency, cents in unallocated_wallet_change.items()},
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
