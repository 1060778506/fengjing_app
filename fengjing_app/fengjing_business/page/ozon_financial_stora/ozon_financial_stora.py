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


def _available_months():
	"""Return current-year month buttons from the first finance record through this month."""
	today = getdate(nowdate())
	year_start = f"{today.year}-01-01 00:00:00"
	year_end = f"{today.year}-12-31 23:59:59"
	earliest = frappe.db.sql(
		f"""SELECT MIN(operation_date)
			FROM `tab{DOCTYPE}`
			WHERE operation_date BETWEEN %s AND %s""",
		(year_start, year_end),
	)[0][0]
	first_month = getdate(earliest).month if earliest else today.month
	first_month = min(first_month, today.month)
	return [f"{today.year}-{month:02d}" for month in range(first_month, today.month + 1)]


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
		if row.get("manual_corresponding_item"):
			item_codes.add(row.manual_corresponding_item)
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
		manual_item = items.get(row.get("manual_corresponding_item"))
		row.manual_item_name = manual_item.item_name if manual_item else ""
		row.manual_item_image = manual_item.image if manual_item else ""
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


TYPE_GROUP_ORDER = (
	"商品销售与佣金",
	"广告费",
	"国际配送服务",
	"Ozon平台服务费",
	"商品缺陷罚款",
)


def _accrual_item_type(row):
	"""Return the original Ozon accrual type translated for display."""
	key = str(row.get("operation_type_name") or row.get("operation_type") or "").strip()
	description = str(row.get("description") or "").strip()
	by_name = {
		"POSTING": "商品销售与佣金",
		"Acquiring": "收单服务费",
		"PayPerClick": "按点击付费",
		"RfbsGlobalDelivery": "国际配送服务",
		"RfbsGlobalAgentFee": "Ozon代理佣金",
		"RfbsGlobalIntermediaryService": "国际运输组织合同服务",
		"RfbsGlobalPlatformConnectionService": "Ozon物流平台接入服务",
		"Promotion": "推广服务",
		"DefectFineErrors": "商品缺陷罚款",
		"ReturnFlowLogistic": "退货运费",
		"Обратная логистика": "退货运费",
	}
	by_description = {
		"Эквайринг": "收单服务费",
		"Оплата за клик": "按点击付费",
		"Услуги международной доставки": "国际配送服务",
		"Агентское вознаграждение Ozon": "Ozon代理佣金",
		"Услуги по заключению договора на организацию международной перевозки": "国际运输组织合同服务",
		"Электронная услуга подключения к логистической Платформе Ozon": "Ozon物流平台接入服务",
		"Обратная логистика": "退货运费",
	}
	return by_name.get(key) or by_description.get(description) or description or key or "未分类应计项目"


def _accrual_group_label(row):
	"""Combine related original accrual types into the dashboard filter groups."""
	item_type = _accrual_item_type(row)
	if item_type in {"推广服务", "按点击付费"}:
		return "广告费"
	if item_type in {"国际配送服务", "退货运费"}:
		return "国际配送服务"
	if item_type in {
		"Ozon物流平台接入服务", "国际运输组织合同服务", "Ozon代理佣金", "收单服务费",
	}:
		return "Ozon平台服务费"
	return item_type


def _sort_dashboard_rows(rows, sort_field=None, sort_direction=None):
	"""Sort the complete filtered result before pagination; time is the stable secondary order."""
	sort_field = str(sort_field or "accrual_type")
	reverse = str(sort_direction or "asc").lower() == "desc"
	text_fields = {
		"posting_number": "posting_number",
		"manual_item": "manual_corresponding_item",
		"accrual_type": "accrual_type_label",
		"sync_type": "sync_type",
		"operation_date": "operation_date",
	}
	number_fields = {
		"is_booked": "is_booked",
		"net_amount": "net_amount",
		"accruals_for_sale": "accruals_for_sale",
		"coinvestment_amount": "coinvestment_amount",
	}

	# Stable secondary order: records inside the same primary value remain newest first.
	rows.sort(key=lambda row: str(row.get("operation_date") or ""), reverse=True)
	if sort_field == "order_product":
		key = lambda row: str(
			row.get("product_name") or row.get("offer_id") or row.get("sku") or row.get("order_number") or ""
		).casefold()
	elif sort_field in number_fields:
		fieldname = number_fields[sort_field]
		key = lambda row: flt(row.get(fieldname))
	else:
		fieldname = text_fields.get(sort_field, "accrual_type_label")
		key = lambda row: str(row.get(fieldname) or "").casefold()
	rows.sort(key=key, reverse=reverse)
	return rows


def _attach_related_products(rows):
	"""按店铺和应计费用ID关联商品，仅用于展示，不回写费用记录的物料绑定。"""
	keys = {
		(str(row.get("store") or ""), str(row.get("posting_number") or ""))
		for row in rows if row.get("posting_number") and not row.get("sku")
	}
	if not keys:
		return

	posting_numbers = sorted({key[1] for key in keys})
	source_rows = frappe.get_all(
		DOCTYPE,
		filters={"posting_number": ["in", posting_numbers]},
		fields=[
			"name", "store", "posting_number", "sku", "offer_id", "product_id", "product_name",
			"quantity", "corresponding_item", "manual_corresponding_item",
			"corresponding_item_name", "corresponding_item_image", "items_json",
		],
		limit_page_length=0,
	)
	mapping_index = _mapping_index()
	grouped = defaultdict(dict)
	item_codes = set()

	for source in source_rows:
		group_key = (str(source.get("store") or ""), str(source.get("posting_number") or ""))
		if group_key not in keys:
			continue
		try:
			products = json.loads(source.get("items_json") or "[]")
		except (TypeError, ValueError, json.JSONDecodeError):
			products = []
		if not isinstance(products, list) or not products:
			products = [{
				"sku": source.get("sku"), "offer_id": source.get("offer_id"),
				"product_id": source.get("product_id"), "name": source.get("product_name"),
				"quantity": source.get("quantity"),
			}]

		for product in products:
			if not isinstance(product, dict):
				continue
			sku = str(product.get("sku") or source.get("sku") or "").strip()
			offer_id = str(product.get("offer_id") or source.get("offer_id") or "").strip()
			product_id = str(product.get("product_id") or source.get("product_id") or "").strip()
			if not (sku or offer_id or product_id):
				continue

			item_code = None
			item_name = None
			item_image = None
			if source.get("corresponding_item") and (not source.get("sku") or sku == str(source.get("sku"))):
				item_code = source.get("corresponding_item")
				item_name = source.get("corresponding_item_name")
				item_image = source.get("corresponding_item_image")
			if not item_code:
				for identifier in (offer_id, sku, product_id):
					mapping = mapping_index.get((group_key[0], identifier)) if identifier else None
					if mapping:
						item_code = mapping.get("物料id")
						item_name = mapping.get("物料名称")
						break
			if item_code:
				item_codes.add(item_code)

			product_key = item_code or sku or offer_id or product_id
			existing = grouped[group_key].get(product_key)
			candidate = {
				"sku": sku, "offer_id": offer_id, "product_id": product_id,
				"product_name": product.get("name") or product.get("product_name") or source.get("product_name"),
				"quantity": flt(product.get("quantity") or source.get("quantity") or 0),
				"item": item_code, "item_name": item_name, "image": item_image,
			}
			if existing:
				existing["quantity"] = max(flt(existing.get("quantity")), candidate["quantity"])
			else:
				grouped[group_key][product_key] = candidate

	item_details = {}
	if item_codes:
		item_details = {
			item.name: item for item in frappe.get_all(
				"Item", filters={"name": ["in", list(item_codes)]},
				fields=["name", "item_name", "image"], limit_page_length=0,
			)
		}
	for products in grouped.values():
		for product in products.values():
			item = item_details.get(product.get("item"))
			if item:
				product["item_name"] = item.item_name or product.get("item_name")
				product["image"] = item.image or product.get("image")

	for row in rows:
		if row.get("sku"):
			row.related_products = []
			continue
		group_key = (str(row.get("store") or ""), str(row.get("posting_number") or ""))
		row.related_products = list(grouped.get(group_key, {}).values())


def _store_cost_center_map():
	"""Map every Ozon store identifier used by finance records to its configured cost center."""
	result = {}
	for row in frappe.get_all(
		"Ozon Store Configuration",
		filters={"enabled": 1},
		fields=["name", "store_name", "cost_center", "ozon_id"],
		limit_page_length=0,
	):
		cost_center = str(row.cost_center or "").strip()
		if not cost_center:
			continue
		for value in (row.name, row.store_name, row.cost_center, row.ozon_id):
			if value:
				result[str(value).strip()] = cost_center
	return result


def _filtered_type_summary(rows, selected_type):
	"""按币种、对应物料和原始应计项目类型汇总当前筛选分组。"""
	if not selected_type:
		return None

	currencies = {}
	manual_item_details = {}
	store_cost_centers = _store_cost_center_map()
	for row in rows:
		currency = row.get("currency_code") or "未知"
		accrual_item_type = row.get("accrual_type_label") or _accrual_item_type(row)
		amount = _money(row.get("net_amount") if row.get("net_amount") is not None else row.get("transaction_amount"))
		sale_amount = _money(row.get("accruals_for_sale"))
		commission_amount = _money(row.get("sale_commission"))
		manual_item = str(row.get("manual_corresponding_item") or "")
		if manual_item:
			manual_item_details[manual_item] = {
				"name": row.get("manual_item_name") or manual_item,
				"image": row.get("manual_item_image") or "",
			}
		bucket = currencies.setdefault(currency, {
			"total": 0.0, "count": 0, "sale_total": 0.0,
			"commission_total": 0.0, "items": {},
		})
		bucket["total"] += amount
		bucket["sale_total"] += sale_amount
		bucket["commission_total"] += commission_amount
		bucket["count"] += 1

		item = None
		if row.get("corresponding_item"):
			item = {
				"item": row.get("corresponding_item"),
				"item_name": row.get("corresponding_item_name"),
				"image": row.get("corresponding_item_image"),
			}
		else:
			# 店铺级费用只有在能唯一对应一个物料时才归入该物料，避免一笔费用重复计算。
			related = {
				product.get("item"): product
				for product in row.get("related_products") or [] if product.get("item")
			}
			if len(related) == 1:
				product = next(iter(related.values()))
				item = {
					"item": product.get("item"),
					"item_name": product.get("item_name"),
					"image": product.get("image"),
				}

		item = item or {"item": "", "item_name": "未明确归属物料", "image": ""}
		default_cost_center = store_cost_centers.get(str(row.get("store") or "").strip(), "")
		effective_cost_center = str(row.get("cost_center") or default_cost_center or "")
		# 同一物料只合并相同的原始应计项目类型，不同服务类型必须分行。
		group_key = (str(item.get("item") or ""), accrual_item_type)
		item_bucket = bucket["items"].setdefault(group_key, {
			**item, "accrual_type": accrual_item_type,
			"amount": 0.0, "sale_amount": 0.0, "commission_amount": 0.0,
			"count": 0, "record_names": [], "account_codes": set(),
			"cost_centers": set(), "manual_items": set(),
		})
		item_bucket["amount"] += amount
		item_bucket["sale_amount"] += sale_amount
		item_bucket["commission_amount"] += commission_amount
		item_bucket["count"] += 1
		item_bucket["record_names"].append(row.get("name"))
		item_bucket["account_codes"].add(str(row.get("account_code") or ""))
		item_bucket["cost_centers"].add(effective_cost_center)
		item_bucket["manual_items"].add(manual_item)

	result = []
	for currency, bucket in currencies.items():
		bucket["total"] = _money(bucket["total"])
		bucket["sale_total"] = _money(bucket["sale_total"])
		bucket["commission_total"] = _money(bucket["commission_total"])
		items = sorted(
			bucket.pop("items").values(),
			key=lambda item: (item["accrual_type"], -abs(item["amount"]), item["item"]),
		)
		for item in items:
			item["amount"] = _money(item["amount"])
			item["sale_amount"] = _money(item["sale_amount"])
			item["commission_amount"] = _money(item["commission_amount"])
			account_codes = item.pop("account_codes")
			item["account_code"] = next(iter(account_codes)) if len(account_codes) == 1 else ""
			item["account_mixed"] = len(account_codes) > 1
			cost_centers = item.pop("cost_centers")
			item["cost_center"] = next(iter(cost_centers)) if len(cost_centers) == 1 else ""
			item["cost_center_mixed"] = len(cost_centers) > 1
			manual_items = item.pop("manual_items")
			manual_item = next(iter(manual_items)) if len(manual_items) == 1 else ""
			manual_detail = manual_item_details.get(manual_item, {})
			item["manual_item"] = manual_item
			item["manual_item_name"] = manual_detail.get("name", "")
			item["manual_item_image"] = manual_detail.get("image", "")
			item["manual_item_mixed"] = len(manual_items) > 1
			item["equation_difference"] = _money(item["sale_amount"] + item["commission_amount"] - item["amount"])
			item["equation_valid"] = abs(item["equation_difference"]) < 0.01
		bucket["equation_difference"] = _money(bucket["sale_total"] + bucket["commission_total"] - bucket["total"])
		bucket["equation_valid"] = abs(bucket["equation_difference"]) < 0.01
		bucket["currency"] = currency
		bucket["items"] = items
		bucket["has_items"] = bool(items)
		result.append(bucket)
	result.sort(key=lambda row: row["currency"])
	return {
		"type": selected_type,
		"count": len(rows),
		"is_sales_commission": selected_type == "商品销售与佣金",
		"currencies": result,
	}


def _account_options():
	"""Return selectable leaf account codes for the summary table."""
	rows = frappe.get_all(
		"Account",
		filters={"is_group": 0, "disabled": 0, "account_number": ["!=", ""]},
		fields=["name", "account_number", "account_name", "company", "account_currency"],
		order_by="account_number asc, company asc",
		limit_page_length=0,
	)
	return [
		{
			"code": str(row.account_number or ""),
			"name": row.name,
			"label": row.name,
			"currency": row.account_currency,
		}
		for row in rows if row.account_number
	]


def _cost_center_options():
	"""Return selectable leaf cost centers for the summary table."""
	rows = frappe.get_all(
		"Cost Center",
		filters={"is_group": 0, "disabled": 0},
		fields=["name", "cost_center_name", "company"],
		order_by="cost_center_name asc, company asc",
		limit_page_length=0,
	)
	return [
		{
			"value": row.name,
			"label": f"{row.cost_center_name} · {row.company}",
		}
		for row in rows
	]


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
		"corresponding_item", "manual_corresponding_item", "corresponding_item_name", "corresponding_item_image", "item_count",
		"currency_code", "transaction_amount", "accruals_for_sale", "coinvestment_amount", "sale_commission",
		"delivery_charge", "return_delivery_charge", "services_amount", "advertising_amount",
		"penalty_amount", "compensation_amount", "discount_points_amount", "other_amount",
		"net_amount", "operation_date", "order_date", "payment_date", "settlement_period_start",
		"settlement_period_end", "fetched_at", "data_version", "data_changed", "sync_status", "items_json", "is_booked", "account_code", "cost_center",
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

	# 每条主交易只能归入一个类型分组；按钮合计必须严格等于“全部”。
	type_counts = defaultdict(int)
	type_booked_counts = defaultdict(int)
	for row in rows:
		row.accrual_type_label = _accrual_item_type(row)
		row.accrual_group_label = _accrual_group_label(row)
		type_counts[row.accrual_group_label] += 1
		type_booked_counts[row.accrual_group_label] += cint(row.get("is_booked"))
	type_total = len(rows)
	type_sum = sum(type_counts.values())
	selected_type = str(f.accrual_type or "").strip()
	if selected_type and selected_type in type_counts:
		rows = [row for row in rows if row.accrual_group_label == selected_type]
	_sort_dashboard_rows(rows, f.sort_field, f.sort_direction)
	if selected_type:
		_attach_related_products(rows)
	filtered_type_summary = _filtered_type_summary(rows, selected_type)

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
	page_rows = rows[start:start + page_size]
	if not selected_type:
		_attach_related_products(page_rows)
	options = {
		"stores": sorted({str(row.store) for row in rows if row.store}),
		"categories": sorted({str(row.transaction_category) for row in rows if row.transaction_category}),
		"directions": sorted({str(row.transaction_direction) for row in rows if row.transaction_direction}),
		"currencies": sorted({str(row.currency_code) for row in rows if row.currency_code}),
		"statuses": sorted({str(row.transaction_status) for row in rows if row.transaction_status}),
		"sync_types": sorted({str(row.sync_type) for row in rows if row.sync_type}),
	}
	ordered_type_names = [name for name in TYPE_GROUP_ORDER if name in type_counts]
	ordered_type_names.extend(sorted(name for name in type_counts if name not in TYPE_GROUP_ORDER))
	return {
		"available_months": _available_months(),
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
		"rows": page_rows, "options": options, "filtered_type_summary": filtered_type_summary,
		"account_options": _account_options(),
		"cost_center_options": _cost_center_options(),
		"type_filter": {
			"total": type_total,
			"booked_total": sum(type_booked_counts.values()),
			"sum": type_sum,
			"valid": type_sum == type_total,
			"counts": {name: type_counts[name] for name in ordered_type_names},
			"booked_counts": {name: type_booked_counts[name] for name in ordered_type_names},
		},
		"pagination": {"page": page, "page_size": page_size, "total": len(rows), "pages": max(1, (len(rows) + page_size - 1) // page_size)},
	}


@frappe.whitelist()
def save_booked_status(changes=None):
	"""保存财务明细页面中发生变化的记账状态、科目代码和成本中心。"""
	frappe.has_permission(DOCTYPE, "write", throw=True)
	if isinstance(changes, str):
		changes = frappe.parse_json(changes)
	if not isinstance(changes, list):
		frappe.throw("记账状态数据格式不正确")
	if len(changes) > 5000:
		frappe.throw("一次最多保存5000条财务记录")

	updated = 0
	booked_updated = 0
	account_updated = 0
	cost_center_updated = 0
	for change in changes:
		if not isinstance(change, dict):
			continue
		name = str(change.get("name") or "").strip()
		if not name or not frappe.db.exists(DOCTYPE, name):
			continue
		values = {}
		if "is_booked" in change:
			values["is_booked"] = 1 if cint(change.get("is_booked")) else 0
			booked_updated += 1
		if "account_code" in change:
			account_code = str(change.get("account_code") or "").strip()
			if len(account_code) > 140:
				frappe.throw("科目代码长度不能超过140个字符")
			if account_code and not frappe.db.exists(
				"Account", {"account_number": account_code, "is_group": 0, "disabled": 0}
			):
				frappe.throw(f"科目代码不存在或不可用：{account_code}")
			values["account_code"] = account_code
			account_updated += 1
		if "cost_center" in change:
			cost_center = str(change.get("cost_center") or "").strip()
			if cost_center and not frappe.db.exists(
				"Cost Center", {"name": cost_center, "is_group": 0, "disabled": 0}
			):
				frappe.throw(f"成本中心不存在或不可用：{cost_center}")
			values["cost_center"] = cost_center
			cost_center_updated += 1
		if not values:
			continue
		frappe.db.set_value(DOCTYPE, name, values)
		updated += 1
	return {
		"updated": updated,
		"booked_updated": booked_updated,
		"account_updated": account_updated,
		"cost_center_updated": cost_center_updated,
	}


@frappe.whitelist()
def bind_financial_item(record_names=None, item=None):
	"""Set the independent bookkeeping item without changing corresponding-item or SKU mapping data."""
	frappe.has_permission(DOCTYPE, "write", throw=True)
	if isinstance(record_names, str):
		record_names = frappe.parse_json(record_names)
	if not isinstance(record_names, list) or not record_names:
		frappe.throw("请选择需要归属物料的财务记录")
	if len(record_names) > 5000:
		frappe.throw("一次最多处理5000条财务记录")
	item = str(item or "").strip()
	if item and not frappe.db.exists("Item", item):
		frappe.throw("请选择有效的ERPNext物料")
	updated = 0
	for name in {str(value or "").strip() for value in record_names}:
		if name and frappe.db.exists(DOCTYPE, name):
			frappe.db.set_value(DOCTYPE, name, "manual_corresponding_item", item, update_modified=True)
			updated += 1
	return {"updated": updated, "item": item}


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
