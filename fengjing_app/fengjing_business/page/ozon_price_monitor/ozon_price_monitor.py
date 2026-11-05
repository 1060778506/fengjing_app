import json

import frappe
from frappe.utils import add_days, flt, nowdate


DOCTYPE = "Ozon Price History"


@frappe.whitelist()
def get_products(date_from=None, date_to=None, store=None):
	frappe.has_permission(DOCTYPE, "read", throw=True)
	date_from = date_from or add_days(nowdate(), -29)
	date_to = date_to or nowdate()
	cost_expression = _cost_price_sql("h")
	market_expression = _market_min_price_sql("h")
	latest_conditions = []
	latest_values = {}
	if store:
		latest_conditions.append("store = %(store)s")
		latest_values["store"] = store
	latest_where = " WHERE " + " AND ".join(latest_conditions) if latest_conditions else ""
	latest_rows = frappe.db.sql(
		f"""
		SELECT h.store, h.ozon_product_id, h.sku_id, h.offer_id,
			h.corresponding_item, h.corresponding_item_name, h.product_name,
			h.ozon_image_url, h.currency_code, h.seller_price, h.buyer_price,
			h.old_price, h.marketing_seller_price, h.minimum_price,
			{market_expression} AS market_min_price, h.recommended_price,
			{cost_expression} AS ozon_cost_price,
			h.commission_percent,
			h.has_promotion, h.visibility, h.product_status, h.recorded_at,
			h.price_change_amount, h.price_change_percent, h.change_direction
		FROM `tab{DOCTYPE}` h
		INNER JOIN (
			SELECT store, ozon_product_id, MAX(recorded_at) AS latest_at
			FROM `tab{DOCTYPE}`{latest_where}
			GROUP BY store, ozon_product_id
		) x ON x.store = h.store
			AND x.ozon_product_id = h.ozon_product_id
			AND x.latest_at = h.recorded_at
		ORDER BY COALESCE(h.corresponding_item_name, h.product_name, h.offer_id) ASC
		""",
		latest_values,
		as_dict=True,
	)

	range_conditions = ["recorded_at >= %(date_from)s", "recorded_at <= %(date_to)s"]
	range_values = {"date_from": f"{date_from} 00:00:00", "date_to": f"{date_to} 23:59:59"}
	if store:
		range_conditions.append("store = %(store)s")
		range_values["store"] = store
	range_rows = frappe.db.sql(
		f"""
		SELECT store, ozon_product_id, COUNT(*) AS snapshot_count,
			MIN(COALESCE(buyer_price, seller_price)) AS range_min,
			MAX(COALESCE(buyer_price, seller_price)) AS range_max,
			SUM(CASE WHEN price_changed = 1 THEN 1 ELSE 0 END) AS change_count,
			MIN(recorded_at) AS first_at, MAX(recorded_at) AS last_at
		FROM `tab{DOCTYPE}`
		WHERE {' AND '.join(range_conditions)}
		GROUP BY store, ozon_product_id
		""",
		range_values,
		as_dict=True,
	)
	stats = {(row.store, row.ozon_product_id): row for row in range_rows}

	item_codes = {row.corresponding_item for row in latest_rows if row.corresponding_item}
	items = {
		row.name: row
		for row in frappe.get_all(
			"Item",
			filters={"name": ["in", list(item_codes)]},
			fields=["name", "item_name", "image"],
			limit_page_length=0,
		)
	} if item_codes else {}
	products = []
	for row in latest_rows:
		item = items.get(row.corresponding_item)
		stat = stats.get((row.store, row.ozon_product_id), {})
		data = dict(row)
		data.update({
			"corresponding_item_name": (item.item_name if item else None) or row.corresponding_item_name,
			"corresponding_item_image": item.image if item else "",
			"snapshot_count": int(stat.get("snapshot_count") or 0),
			"range_min": flt(stat.get("range_min")),
			"range_max": flt(stat.get("range_max")),
			"change_count": int(stat.get("change_count") or 0),
			"first_at": stat.get("first_at"),
			"last_at": stat.get("last_at"),
		})
		_normalize_numbers(data)
		products.append(data)
	return {
		"products": products,
		"stores": _distinct("store"),
		"range": {"date_from": date_from, "date_to": date_to},
	}


@frappe.whitelist()
def get_price_series(store, ozon_product_id, date_from=None, date_to=None):
	frappe.has_permission(DOCTYPE, "read", throw=True)
	if not store or not ozon_product_id:
		frappe.throw("请选择店铺和 Ozon 商品")
	date_from = date_from or add_days(nowdate(), -29)
	date_to = date_to or nowdate()
	fields = [
		"name", "recorded_at", "currency_code", "seller_price", "buyer_price",
		"old_price", "marketing_seller_price", "premium_price", "minimum_price",
		"market_min_price", "recommended_price", "commission_percent",
		"promotion_discount", "has_promotion", "price_index", "visibility",
		"product_status", "price_change_amount", "price_change_percent",
		"change_direction", "sync_status", "raw_json",
	]
	if frappe.db.has_column(DOCTYPE, "ozon_cost_price"):
		fields.append("ozon_cost_price")
	rows = frappe.get_all(
		DOCTYPE,
		filters=[
			["store", "=", store],
			["ozon_product_id", "=", ozon_product_id],
			["recorded_at", ">=", f"{date_from} 00:00:00"],
			["recorded_at", "<=", f"{date_to} 23:59:59"],
		],
		fields=fields,
		order_by="recorded_at asc, name asc",
		limit_page_length=10000,
	)
	for row in rows:
		row["ozon_cost_price"] = _cost_from_row(row)
		row["market_min_price"] = _market_min_from_row(row)
		row.pop("raw_json", None)
		_normalize_numbers(row)
	return {"rows": [dict(row) for row in rows], "limited": len(rows) >= 10000}


@frappe.whitelist()
def get_all_price_series(date_from=None, date_to=None, store=None, max_points=240):
	"""Return bounded time series for every product so all rows can render at once."""
	frappe.has_permission(DOCTYPE, "read", throw=True)
	date_from = date_from or add_days(nowdate(), -29)
	date_to = date_to or nowdate()
	max_points = max(24, min(int(max_points or 240), 500))
	conditions = ["recorded_at >= %(date_from)s", "recorded_at <= %(date_to)s"]
	values = {
		"date_from": f"{date_from} 00:00:00",
		"date_to": f"{date_to} 23:59:59",
		"max_points": max_points,
	}
	if store:
		conditions.append("store = %(store)s")
		values["store"] = store
	cost_expression = _cost_price_sql()
	market_expression = _market_min_price_sql()
	rows = frappe.db.sql(
		f"""
		SELECT store, ozon_product_id, recorded_at, currency_code,
			seller_price, buyer_price, old_price, marketing_seller_price,
			minimum_price, market_min_price, recommended_price, ozon_cost_price,
			price_change_amount, price_change_percent, has_promotion,
			row_number_value, total_rows
		FROM (
			SELECT store, ozon_product_id, recorded_at, currency_code,
				seller_price, buyer_price, old_price, marketing_seller_price,
				minimum_price, {market_expression} AS market_min_price, recommended_price,
				{cost_expression} AS ozon_cost_price,
				price_change_amount, price_change_percent, has_promotion,
				ROW_NUMBER() OVER (
					PARTITION BY store, ozon_product_id ORDER BY recorded_at
				) AS row_number_value,
				COUNT(*) OVER (PARTITION BY store, ozon_product_id) AS total_rows
			FROM `tab{DOCTYPE}`
			WHERE {' AND '.join(conditions)}
		) ranked
		WHERE MOD(row_number_value - 1, GREATEST(CEIL(total_rows / %(max_points)s), 1)) = 0
			OR row_number_value = total_rows
		ORDER BY store, ozon_product_id, recorded_at
		""",
		values,
		as_dict=True,
	)
	grouped, counts = {}, {}
	for row in rows:
		key = f"{row.store}¦{row.ozon_product_id}"
		counts[key] = int(row.total_rows or 0)
		data = dict(row)
		data.pop("row_number_value", None)
		data.pop("total_rows", None)
		_normalize_numbers(data)
		grouped.setdefault(key, []).append(data)
	return {"series": grouped, "source_counts": counts, "max_points": max_points}


def _cost_price_sql(alias=None):
	"""Use the new column after migrate and fall back to the preserved raw JSON before it."""
	prefix = f"{alias}." if alias else ""
	raw_value = (
		f"CAST(JSON_UNQUOTE(JSON_EXTRACT({prefix}raw_json, '$.price.net_price')) "
		"AS DECIMAL(21,9))"
	)
	if frappe.db.has_column(DOCTYPE, "ozon_cost_price"):
		return f"COALESCE(NULLIF({prefix}ozon_cost_price, 0), {raw_value})"
	return raw_value


def _market_min_price_sql(alias=None):
	"""Return market minimum in the seller currency used by the other chart lines."""
	prefix = f"{alias}." if alias else ""
	paths = (
		"$.price_indexes.ozon_index_data.min_price_in_seller",
		"$.price_indexes.external_index_data.min_price_in_seller",
		"$.price_indexes.self_marketplaces_index_data.min_price_in_seller",
	)
	values = [
		f"NULLIF(CAST(JSON_UNQUOTE(JSON_EXTRACT({prefix}raw_json, '{path}')) AS DECIMAL(21,9)), 0)"
		for path in paths
	]
	return f"COALESCE({', '.join(values)}, NULLIF({prefix}market_min_price, 0))"


def _cost_from_row(row):
	value = row.get("ozon_cost_price")
	if value not in (None, "", 0, 0.0):
		return value
	try:
		return (json.loads(row.get("raw_json") or "{}") or {}).get("price", {}).get("net_price")
	except (TypeError, ValueError, AttributeError):
		return None


def _market_min_from_row(row):
	try:
		indexes = (json.loads(row.get("raw_json") or "{}") or {}).get("price_indexes") or {}
		currency = str(row.get("currency_code") or "").upper()
		values = []
		for key in ("external_index_data", "ozon_index_data", "self_marketplaces_index_data"):
			entry = indexes.get(key) or {}
			value = flt(entry.get("min_price_in_seller"))
			value_currency = str(entry.get("min_price_in_seller_currency") or "").upper()
			if value > 0 and (not currency or not value_currency or value_currency == currency):
				values.append(value)
		return min(values) if values else row.get("market_min_price")
	except (TypeError, ValueError, AttributeError):
		return row.get("market_min_price")


def _normalize_numbers(row):
	for fieldname in (
		"seller_price", "buyer_price", "old_price", "marketing_seller_price",
		"premium_price", "minimum_price", "market_min_price", "recommended_price",
		"ozon_cost_price",
		"commission_percent", "promotion_discount", "price_index",
		"price_change_amount", "price_change_percent", "range_min", "range_max",
	):
		if fieldname in row:
			row[fieldname] = flt(row.get(fieldname)) if row.get(fieldname) is not None else None


def _distinct(fieldname):
	return [
		str(value) for value in frappe.get_all(
			DOCTYPE,
			filters=[[fieldname, "is", "set"]],
			pluck=fieldname,
			group_by=fieldname,
			order_by=fieldname,
			limit_page_length=0,
		) if value
	]
