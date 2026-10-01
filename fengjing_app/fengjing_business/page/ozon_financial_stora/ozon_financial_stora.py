from collections import defaultdict
import json

import frappe
from frappe.utils import add_days, cint, flt, getdate, nowdate


DOCTYPE = "Ozon Financial Storage"
MAPPING_DOCTYPE = "Fengjing - Product Corresponding Platform - Main Table"


def _money(value):
	return flt(value or 0, 2)


def _args(filters=None):
	if isinstance(filters, str):
		filters = frappe.parse_json(filters)
	return frappe._dict(filters or {})


def _db_filters(filters):
	result = []
	date_from = filters.date_from or add_days(nowdate(), -29)
	date_to = filters.date_to or nowdate()
	result.extend([
		["operation_date", ">=", f"{date_from} 00:00:00"],
		["operation_date", "<=", f"{date_to} 23:59:59"],
	])
	for request_key, fieldname in {
		"store": "store",
		"category": "transaction_category",
		"direction": "transaction_direction",
		"currency": "currency_code",
		"status": "transaction_status",
		"sync_type": "sync_type",
	}.items():
		if filters.get(request_key):
			result.append([fieldname, "=", filters.get(request_key)])
	return result


def _mapping_index():
	rows = frappe.get_all(
		MAPPING_DOCTYPE,
		filters={"启用": 1},
		fields=["店铺", "平台sku", "平台asin", "物料id", "物料名称"],
		limit_page_length=0,
	)
	index = {}
	for row in rows:
		store = str(row.get("店铺") or "")
		for value in (row.get("平台sku"), row.get("平台asin")):
			value = str(value or "").strip()
			if store and value:
				index[(store, value)] = row
	return index


def _enrich_items(rows):
	index = _mapping_index()
	item_codes = set()
	for row in rows:
		if not row.get("corresponding_item"):
			store = str(row.get("store") or "")
			for value in (row.get("offer_id"), row.get("sku"), row.get("product_id")):
				mapping = index.get((store, str(value or "").strip()))
				if mapping:
					row.corresponding_item = mapping.get("物料id")
					row.corresponding_item_name = mapping.get("物料名称")
					break
		if row.get("corresponding_item"):
			item_codes.add(row.corresponding_item)
	items = {}
	if item_codes:
		items = {
			row.name: row for row in frappe.get_all(
				"Item", filters={"name": ["in", list(item_codes)]},
				fields=["name", "item_name", "image"], limit_page_length=0,
			)
		}
	for row in rows:
		item = items.get(row.get("corresponding_item"))
		if item:
			row.corresponding_item_name = item.item_name or row.get("corresponding_item_name")
			row.corresponding_item_image = item.image or row.get("corresponding_item_image")
		row.is_product = 1 if row.get("sku") else 0
		row.is_bound = 1 if row.get("corresponding_item") else 0


def _amount_value(value):
	if isinstance(value, dict):
		value = value.get("amount", value.get("value"))
	return _money(value)


def _posting_breakdown(row):
	"""Build the sale and commission child rows shown by the Ozon finance UI."""
	if str(row.get("operation_type") or "") != "POSTING":
		return []
	try:
		products = json.loads(row.get("items_json") or "[]")
	except (TypeError, ValueError, json.JSONDecodeError):
		return []
	if not isinstance(products, list):
		return []
	result = []
	for product in products:
		if not isinstance(product, dict):
			continue
		commission = product.get("commission") or {}
		sku = str(product.get("sku") or row.get("sku") or "")
		quantity = flt(product.get("quantity") or 0)
		sale_amount = _amount_value(commission.get("sale_amount"))
		commission_amount = _amount_value(commission.get("commission"))
		if sale_amount:
			result.append({
				"label": "销售", "detail": "收入", "amount": sale_amount,
				"currency": row.get("currency_code"), "sku": sku, "quantity": quantity,
			})
		if commission_amount:
			result.append({
				"label": "Ozon代理佣金", "detail": "销售代理佣金", "amount": commission_amount,
				"currency": row.get("currency_code"), "sku": sku, "quantity": quantity,
			})
	return result


@frappe.whitelist()
def get_dashboard_data(filters=None, page=1, page_size=50):
	frappe.has_permission(DOCTYPE, "read", throw=True)
	f = _args(filters)
	fields = [
		"name", "operation_id", "operation_type", "operation_type_name", "store", "ozon_id",
		"sync_type", "transaction_category", "transaction_subcategory", "transaction_direction",
		"transaction_status", "description", "is_refund", "is_reversal", "is_adjustment",
		"posting_number", "order_number", "delivery_schema", "warehouse_name", "statement_id",
		"statement_number", "sku", "offer_id", "product_id", "product_name", "quantity",
		"corresponding_item", "corresponding_item_name", "corresponding_item_image", "item_count",
		"currency_code", "transaction_amount", "accruals_for_sale", "sale_commission",
		"delivery_charge", "return_delivery_charge", "services_amount", "advertising_amount",
		"penalty_amount", "compensation_amount", "discount_points_amount", "other_amount",
		"net_amount", "operation_date", "order_date", "payment_date", "settlement_period_start",
		"settlement_period_end", "fetched_at", "data_version", "data_changed", "sync_status", "items_json", "is_booked",
	]
	rows = frappe.get_all(
		DOCTYPE, filters=_db_filters(f), fields=fields,
		order_by="operation_date desc, modified desc", limit_page_length=50000,
	)
	limited = len(rows) >= 50000
	_enrich_items(rows)
	for row in rows:
		row.breakdown = _posting_breakdown(row)
		row.pop("items_json", None)

	search = str(f.search or "").strip().lower()
	binding = str(f.binding or "")
	if search or binding:
		filtered = []
		for row in rows:
			haystack = " ".join(str(row.get(key) or "") for key in (
				"operation_id", "operation_type_name", "description", "posting_number", "order_number",
				"sku", "offer_id", "product_id", "product_name", "corresponding_item",
				"corresponding_item_name", "statement_number",
			)).lower()
			if search and search not in haystack:
				continue
			if binding == "bound" and not row.is_bound:
				continue
			if binding == "unbound" and (row.is_bound or not row.is_product):
				continue
			if binding == "store" and row.is_product:
				continue
			filtered.append(row)
		rows = filtered

	currencies = defaultdict(lambda: {"inflow": 0.0, "outflow": 0.0, "net": 0.0, "sales": 0.0, "fees": 0.0})
	daily = defaultdict(lambda: defaultdict(lambda: {"inflow": 0.0, "outflow": 0.0, "net": 0.0, "count": 0}))
	categories = defaultdict(lambda: defaultdict(lambda: {"amount": 0.0, "count": 0}))
	stores = defaultdict(lambda: defaultdict(lambda: {"amount": 0.0, "count": 0}))
	fee_fields = {
		"销售佣金": "sale_commission", "配送费": "delivery_charge", "退货配送费": "return_delivery_charge",
		"服务费": "services_amount", "广告推广": "advertising_amount", "罚款": "penalty_amount",
		"赔偿及返还": "compensation_amount", "折扣积分": "discount_points_amount", "其他": "other_amount",
	}
	fees = defaultdict(lambda: defaultdict(float))
	products = {}
	statements = {}
	bound = product_rows = store_rows = refunds = errors = 0
	for row in rows:
		currency = row.currency_code or "未知"
		amount = _money(row.net_amount if row.net_amount is not None else row.transaction_amount)
		currencies[currency]["net"] += amount
		currencies[currency]["inflow" if amount >= 0 else "outflow"] += abs(amount)
		currencies[currency]["sales"] += _money(row.accruals_for_sale)
		currencies[currency]["fees"] += sum(abs(_money(row.get(field))) for field in fee_fields.values())
		day = str(getdate(row.operation_date)) if row.operation_date else "未知日期"
		daily[day][currency]["net"] += amount
		daily[day][currency]["inflow" if amount >= 0 else "outflow"] += abs(amount)
		daily[day][currency]["count"] += 1
		category = row.transaction_category or "未分类"
		categories[currency][category]["amount"] += amount
		categories[currency][category]["count"] += 1
		stores[currency][row.store or "未归属店铺"]["amount"] += amount
		stores[currency][row.store or "未归属店铺"]["count"] += 1
		for label, fieldname in fee_fields.items():
			fees[currency][label] += _money(row.get(fieldname))
		if row.is_product:
			product_rows += 1
			bound += row.is_bound
			key = (row.store or "", row.sku or "", row.offer_id or "")
			product = products.setdefault(key, {
				"store": row.store, "sku": row.sku, "offer_id": row.offer_id,
				"product_name": row.product_name, "item": row.corresponding_item,
				"item_name": row.corresponding_item_name, "image": row.corresponding_item_image,
				"quantity": 0.0, "transactions": 0, "amounts": defaultdict(float),
			})
			product["quantity"] += flt(row.quantity)
			product["transactions"] += 1
			product["amounts"][currency] += amount
		else:
			store_rows += 1
		refunds += cint(row.is_refund)
		errors += 1 if row.sync_status == "失败" else 0
		statement_key = (row.store or "", row.statement_number or row.statement_id or "未关联结算报告", currency)
		statement = statements.setdefault(statement_key, {
			"store": row.store, "statement": statement_key[1], "currency": currency,
			"amount": 0.0, "count": 0, "latest": row.operation_date,
		})
		statement["amount"] += amount
		statement["count"] += 1
		if row.operation_date and (not statement["latest"] or row.operation_date > statement["latest"]):
			statement["latest"] = row.operation_date

	product_list = []
	for product in products.values():
		product["amounts"] = dict(product["amounts"])
		product_list.append(product)
	product_list.sort(key=lambda row: (row["quantity"], sum(abs(v) for v in row["amounts"].values())), reverse=True)
	statement_list = sorted(statements.values(), key=lambda row: str(row["latest"] or ""), reverse=True)
	page = max(cint(page), 1)
	page_size = min(max(cint(page_size), 20), 200)
	start = (page - 1) * page_size
	options = {
		"stores": sorted({str(row.store) for row in rows if row.store}),
		"categories": sorted({str(row.transaction_category) for row in rows if row.transaction_category}),
		"directions": sorted({str(row.transaction_direction) for row in rows if row.transaction_direction}),
		"currencies": sorted({str(row.currency_code) for row in rows if row.currency_code}),
		"statuses": sorted({str(row.transaction_status) for row in rows if row.transaction_status}),
		"sync_types": sorted({str(row.sync_type) for row in rows if row.sync_type}),
	}
	return {
		"summary": {
			"records": len(rows), "product_records": product_rows, "store_records": store_rows,
			"bound": bound, "unbound": product_rows - bound,
			"binding_rate": round(bound * 100 / product_rows, 1) if product_rows else 0,
			"refunds": refunds, "errors": errors, "currencies": dict(currencies), "limited": limited,
		},
		"daily": {day: dict(values) for day, values in sorted(daily.items())},
		"categories": {currency: dict(values) for currency, values in categories.items()},
		"stores": {currency: dict(values) for currency, values in stores.items()},
		"fees": {currency: dict(values) for currency, values in fees.items()},
		"products": product_list[:500], "statements": statement_list[:300],
		"rows": rows[start:start + page_size], "options": options,
		"pagination": {"page": page, "page_size": page_size, "total": len(rows), "pages": max(1, (len(rows) + page_size - 1) // page_size)},
	}


@frappe.whitelist()
def save_booked_status(changes=None):
	"""保存财务明细页面中发生变化的“已记账”状态。"""
	frappe.has_permission(DOCTYPE, "write", throw=True)
	if isinstance(changes, str):
		changes = frappe.parse_json(changes)
	if not isinstance(changes, list):
		frappe.throw("记账状态数据格式不正确")
	if len(changes) > 200:
		frappe.throw("一次最多保存200条记账状态")

	updated = 0
	for change in changes:
		if not isinstance(change, dict):
			continue
		name = str(change.get("name") or "").strip()
		if not name or not frappe.db.exists(DOCTYPE, name):
			continue
		frappe.db.set_value(DOCTYPE, name, "is_booked", 1 if cint(change.get("is_booked")) else 0)
		updated += 1
	return {"updated": updated}


@frappe.whitelist()
def bind_item(store, item, sku=None, offer_id=None, product_id=None):
	frappe.only_for("System Manager")
	if not frappe.db.exists("Item", item):
		frappe.throw("请选择有效的ERPNext物料")
	identifier = str(offer_id or sku or product_id or "").strip()
	if not store or not identifier:
		frappe.throw("店铺和Ozon商品标识不能为空")
	name = frappe.db.get_value(MAPPING_DOCTYPE, {"店铺": store, "平台sku": identifier}, "name")
	if name:
		doc = frappe.get_doc(MAPPING_DOCTYPE, name)
		doc.物料id = item
		doc.启用 = 1
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc({
			"doctype": MAPPING_DOCTYPE, "启用": 1, "店铺": store,
			"平台sku": identifier, "物料id": item,
		}).insert(ignore_permissions=True)
	values = [item, store, identifier, identifier, identifier]
	frappe.db.sql(
		f"""UPDATE `tab{DOCTYPE}` SET corresponding_item=%s
		WHERE store=%s AND (offer_id=%s OR sku=%s OR product_id=%s)""", values,
	)
	frappe.db.commit()
	return {"mapping": doc.name, "item": item}


@frappe.whitelist()
def refresh_item_bindings():
	frappe.only_for("System Manager")
	rows = frappe.get_all(DOCTYPE, fields=["name", "store", "sku", "offer_id", "product_id", "corresponding_item"], limit_page_length=0)
	index = _mapping_index()
	updated = 0
	for row in rows:
		target = None
		for value in (row.offer_id, row.sku, row.product_id):
			mapping = index.get((str(row.store or ""), str(value or "").strip()))
			if mapping:
				target = mapping.get("物料id")
				break
		if target and target != row.corresponding_item:
			frappe.db.set_value(DOCTYPE, row.name, "corresponding_item", target, update_modified=False)
			updated += 1
	frappe.db.commit()
	return {"total": len(rows), "updated": updated}
