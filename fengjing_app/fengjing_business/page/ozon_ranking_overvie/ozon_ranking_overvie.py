import json

import frappe
from frappe.utils import add_days, flt, nowdate


DOCTYPE = "Ozon ranking storage"


@frappe.whitelist()
def get_dashboard_data(filters=None):
	"""Return Ozon product/keyword analytics for the ranking dashboard."""
	frappe.has_permission(DOCTYPE, "read", throw=True)
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	date_from = filters.get("date_from") or add_days(nowdate(), -30)
	date_to = filters.get("date_to") or nowdate()
	db_filters = [
		["statistics_date", ">=", date_from],
		["statistics_date", "<=", date_to],
	]
	for key, fieldname in {
		"store": "store", "sku": "sku", "item": "corresponding_item",
		"level": "data_level", "category": "category",
	}.items():
		if filters.get(key):
			db_filters.append([fieldname, "=", filters[key]])

	fields = [
		"name", "store", "ozon_id", "data_level", "sku", "offer_id",
		"product_id", "product_name", "category", "ozon_product_image",
		"corresponding_item", "corresponding_item_name", "corresponding_item_image",
		"statistics_date", "analytics_period_from", "analytics_period_to",
		"fetched_at", "search_query", "position", "query_index",
		"unique_search_users", "unique_view_users", "view_conversion",
		"currency_code", "gmv", "order_count", "source_endpoint",
		"sync_type", "sync_status", "raw_json",
	]
	rows = frappe.get_all(
		DOCTYPE, filters=db_filters, fields=fields,
		order_by="statistics_date asc, fetched_at asc, name asc",
		limit_page_length=50000,
	)
	_enrich_items(rows)
	_enrich_ozon_images(rows)
	keyword = str(filters.get("keyword") or "").strip().lower()
	if keyword:
		rows = [row for row in rows if keyword in str(row.get("search_query") or "").lower()]

	metric_availability = {"views": False, "orders": False}
	for row in rows:
		try:
			raw_data = (json.loads(row.get("raw_json") or "{}") or {}).get("data") or {}
			metric_availability["views"] = metric_availability["views"] or raw_data.get("unique_view_users") is not None
			metric_availability["orders"] = metric_availability["orders"] or raw_data.get("order_count") is not None
		except (TypeError, ValueError):
			pass
		row.pop("raw_json", None)
		for fieldname in ("position", "query_index", "view_conversion", "gmv"):
			row[fieldname] = flt(row.get(fieldname))
		for fieldname in ("unique_search_users", "unique_view_users", "order_count"):
			row[fieldname] = int(row.get(fieldname) or 0)

	return {
		"rows": [dict(row) for row in rows],
		"options": _get_options(),
		"sync_health": _get_sync_health(),
		"order_metrics": _get_order_metrics(filters, date_from, date_to),
		"metric_availability": metric_availability,
		"range": {"date_from": date_from, "date_to": date_to},
		"limited": len(rows) >= 50000,
	}


def _get_order_metrics(filters, date_from, date_to):
	"""Use the real order store when ranking analytics omits order_count."""
	db_filters = [
		["order_created_at", ">=", f"{date_from} 00:00:00"],
		["order_created_at", "<=", f"{date_to} 23:59:59"],
	]
	if filters.get("store"):
		db_filters.append(["store", "=", filters["store"]])
	rows = frappe.get_all(
		"Ozon order storage", filters=db_filters,
		fields=["order_number", "posting_number", "currency_code", "line_amount"],
		limit_page_length=0,
	)
	orders = {str(row.get("order_number") or row.get("posting_number") or "") for row in rows}
	orders.discard("")
	amounts = {}
	for row in rows:
		currency = str(row.get("currency_code") or "未知")
		amounts[currency] = amounts.get(currency, 0) + flt(row.get("line_amount"))
	return {"orders": len(orders), "amounts": amounts, "lines": len(rows)}


def _enrich_items(rows):
	mappings = frappe.get_all(
		"Fengjing - Product Corresponding Platform - Main Table",
		filters={"启用": 1}, fields=["店铺", "平台sku", "物料id"],
		limit_page_length=0,
	)
	mapping = {
		(str(row["店铺"]), str(row["平台sku"])): str(row["物料id"])
		for row in mappings if row.get("店铺") and row.get("平台sku") and row.get("物料id")
	}
	for row in rows:
		if not row.get("corresponding_item"):
			for candidate in (row.get("sku"), row.get("offer_id"), row.get("product_id")):
				item = mapping.get((str(row.get("store") or ""), str(candidate or "")))
				if item:
					row["corresponding_item"] = item
					break
	items = {str(row.get("corresponding_item")) for row in rows if row.get("corresponding_item")}
	details = {
		row.name: row for row in frappe.get_all(
			"Item", filters={"name": ["in", list(items)]}, fields=["name", "item_name", "image"],
			limit_page_length=0,
		)
	} if items else {}
	for row in rows:
		detail = details.get(row.get("corresponding_item"))
		if detail:
			row["corresponding_item_name"] = detail.item_name or row.get("corresponding_item_name")
			row["corresponding_item_image"] = detail.image or row.get("corresponding_item_image")


def _enrich_ozon_images(rows):
	"""Fill missing Ozon images from Seller API and reuse the six-hour cache."""
	from fengjing_app.fengjing_business.doctype.fengjing___product_corresponding_platform___configuration.fengjing___product_corresponding_platform___configuration import (
		_发送ozon订单请求,
	)

	missing = [row for row in rows if not row.get("ozon_product_image")]
	if not missing:
		return
	parent = frappe.get_single("Fengjing - Product Corresponding Platform - Configuration")
	configs = {
		str(row.get("店铺选项") or ""): row
		for row in (parent.get("table_wckx") or [])
		if row.get("店铺选项") and row.get("ozon_id") and row.get("ozon_秘钥")
	}
	grouped = {}
	for row in missing:
		store = str(row.get("store") or "")
		offer_id = str(row.get("offer_id") or "")
		if store in configs and offer_id:
			grouped.setdefault(store, set()).add(offer_id)

	images = {}
	cache = frappe.cache
	for store, offer_ids in grouped.items():
		config = configs[store]
		ozon_id = str(config.get("ozon_id") or "")
		uncached = []
		for offer_id in offer_ids:
			cached = cache.get_value(f"fengjing:ozon-product-image:{ozon_id}:{offer_id}")
			if cached is None:
				uncached.append(offer_id)
			elif cached != "__none__":
				images[(store, offer_id)] = str(cached)

		for offset in range(0, len(uncached), 500):
			chunk = uncached[offset:offset + 500]
			try:
				response = _发送ozon订单请求(
					config,
					"https://api-seller.ozon.ru/v3/product/info/list",
					{"offer_id": chunk},
				)
				if response.status_code != 200:
					continue
				items = (response.json() or {}).get("items") or []
				returned = set()
				for item in items:
					offer_id = str(item.get("offer_id") or "")
					primary = item.get("primary_image") or item.get("images") or []
					image = primary[0] if isinstance(primary, list) and primary else primary
					if not offer_id:
						continue
					returned.add(offer_id)
					cache.set_value(
						f"fengjing:ozon-product-image:{ozon_id}:{offer_id}",
						image or "__none__", expires_in_sec=21600,
					)
					if image:
						images[(store, offer_id)] = image
				for offer_id in set(chunk) - returned:
					cache.set_value(
						f"fengjing:ozon-product-image:{ozon_id}:{offer_id}",
						"__none__", expires_in_sec=21600,
					)
			except Exception:
				frappe.logger("ozon_dashboard", allow_site=True).warning(
					"读取Ozon排名商品图片失败：店铺=%s", store, exc_info=True,
				)

	for row in rows:
		if not row.get("ozon_product_image"):
			row["ozon_product_image"] = images.get(
				(str(row.get("store") or ""), str(row.get("offer_id") or ""))
			) or ""


def _distinct(fieldname):
	return [
		str(value) for value in frappe.get_all(
			DOCTYPE, filters=[[fieldname, "is", "set"]], pluck=fieldname,
			group_by=fieldname, order_by=fieldname, limit_page_length=0,
		) if value
	]


def _get_options():
	items = _distinct("corresponding_item")
	names = {
		row.name: row.item_name or row.name for row in frappe.get_all(
			"Item", filters={"name": ["in", items]}, fields=["name", "item_name"],
			limit_page_length=0,
		)
	} if items else {}
	return {
		"stores": _distinct("store"), "skus": _distinct("sku"),
		"categories": _distinct("category"),
		"items": [{"value": code, "label": names.get(code, code)} for code in items],
	}


def _get_sync_health():
	result = []
	try:
		parent = frappe.get_single("Fengjing - Product Corresponding Platform - Configuration")
		for row in parent.get("table_wckx") or []:
			result.append({
				"store": row.get("店铺选项") or "",
				"enabled": bool(row.get("开启ozon商品排名同步")),
				"task_status": row.get("排名当前任务状态") or "",
				"history_status": row.get("排名历史同步状态") or "",
				"history_progress": flt(row.get("排名历史同步进度")),
				"complete_to": row.get("排名历史已完整同步到"),
				"last_sync": row.get("上次排名同步时间"),
				"next_sync": row.get("下次排名同步时间"),
				"last_result": row.get("上次排名同步结果") or "",
				"error": row.get("排名最近错误") or "",
			})
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Ozon排名概览：读取同步状态失败")
	return result
