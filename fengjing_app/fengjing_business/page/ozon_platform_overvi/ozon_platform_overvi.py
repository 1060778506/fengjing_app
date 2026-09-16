import frappe
from frappe.utils import add_days, flt, nowdate


DOCTYPE = "Ozon order storage"


@frappe.whitelist()
def get_dashboard_data(filters=None):
	"""Return read-only Ozon order rows, filter choices and sync health."""
	frappe.has_permission(DOCTYPE, "read", throw=True)
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	date_from = filters.get("date_from") or add_days(nowdate(), -30)
	date_to = filters.get("date_to") or nowdate()

	db_filters = [
		["order_created_at", ">=", f"{date_from} 00:00:00"],
		["order_created_at", "<=", f"{date_to} 23:59:59"],
	]
	for key, fieldname in {
		"store": "store",
		"status": "ozon_status",
		"fulfillment": "fulfillment_type",
		"currency": "currency_code",
		"warehouse": "warehouse_name",
	}.items():
		if filters.get(key):
			db_filters.append([fieldname, "=", filters[key]])

	fields = [
		"name", "store", "ozon_id", "fulfillment_type", "posting_number",
		"parent_posting_number", "order_id", "order_number", "product_id",
		"sku", "offer_id", "product_name", "corresponding_item",
		"corresponding_item_name", "corresponding_item_image", "ozon_status",
		"ozon_substatus", "cancellation_reason", "is_cancelled", "is_express",
		"sync_type", "order_created_at", "in_process_at", "source_updated_at",
		"shipment_date", "delivering_date", "delivery_date", "currency_code",
		"unit_price", "line_amount", "quantity", "commission_amount",
		"payout_amount", "warehouse_id", "warehouse_name", "delivery_method_name",
		"delivery_type", "tracking_number", "destination_place_name",
		"shipping_country_code", "shipping_region", "shipping_city",
		"shipping_postal_code", "first_fetched_at", "fetched_at", "sync_status",
	]
	rows = frappe.get_all(
		DOCTYPE,
		filters=db_filters,
		fields=fields,
		order_by="order_created_at asc, name asc",
		limit_page_length=30000,
	)
	_enrich_items(rows)
	_enrich_ozon_images(rows)

	search = str(filters.get("search") or "").strip().lower()
	item = str(filters.get("item") or "").strip()
	if item:
		rows = [row for row in rows if str(row.get("corresponding_item") or "") == item]
	if search:
		rows = [
			row for row in rows
			if search in " ".join(str(value or "") for value in row.values()).lower()
		]

	return {
		"rows": [_serialise(row) for row in rows],
		"options": _get_options(),
		"sync_health": _get_sync_health(),
		"range": {"date_from": date_from, "date_to": date_to},
		"limited": len(rows) >= 30000,
	}


def _serialise(row):
	data = dict(row)
	for fieldname in (
		"unit_price", "line_amount", "commission_amount", "payout_amount"
	):
		data[fieldname] = flt(data.get(fieldname))
	data["quantity"] = int(data.get("quantity") or 0)
	return data


def _enrich_items(rows):
	"""Use the platform mapping table when stored Ozon rows have no Item yet."""
	mappings = frappe.get_all(
		"Fengjing - Product Corresponding Platform - Main Table",
		filters={"启用": 1},
		fields=["店铺", "平台sku", "物料id", "物料名称"],
		limit_page_length=0,
	)
	mapping = {}
	for row in mappings:
		if row.get("店铺") and row.get("平台sku") and row.get("物料id"):
			mapping[(str(row["店铺"]), str(row["平台sku"]))] = str(row["物料id"])

	for row in rows:
		if row.get("corresponding_item"):
			continue
		for candidate in (row.get("sku"), row.get("offer_id"), row.get("product_id")):
			item = mapping.get((str(row.get("store") or ""), str(candidate or "")))
			if item:
				row["corresponding_item"] = item
				break

	items = {str(row.get("corresponding_item")) for row in rows if row.get("corresponding_item")}
	if not items:
		return
	details = {
		row.name: row for row in frappe.get_all(
			"Item", filters={"name": ["in", list(items)]},
			fields=["name", "item_name", "image"], limit_page_length=0,
		)
	}
	for row in rows:
		detail = details.get(row.get("corresponding_item"))
		if detail:
			row["corresponding_item_name"] = detail.item_name or row.get("corresponding_item_name")
			row["corresponding_item_image"] = detail.image or row.get("corresponding_item_image")


def _enrich_ozon_images(rows):
	"""Attach an Ozon image from ranking snapshots or the cached Seller API."""
	skus = {str(row.get("sku")) for row in rows if row.get("sku")}
	if not skus:
		return
	images = {}
	if frappe.db.exists("DocType", "Ozon ranking storage"):
		ranking_rows = frappe.get_all(
			"Ozon ranking storage",
			filters=[
				["sku", "in", list(skus)],
				["ozon_product_image", "is", "set"],
			],
			fields=["store", "sku", "ozon_product_image", "statistics_date", "fetched_at"],
			order_by="statistics_date desc, fetched_at desc",
			limit_page_length=0,
		)
		for image_row in ranking_rows:
			key = (str(image_row.get("store") or ""), str(image_row.get("sku") or ""))
			images.setdefault(key, image_row.get("ozon_product_image"))

	missing_rows = [
		row for row in rows
		if not images.get((str(row.get("store") or ""), str(row.get("sku") or "")))
	]
	if missing_rows:
		images.update(_get_seller_api_images(missing_rows))
	for row in rows:
		store = str(row.get("store") or "")
		row["ozon_product_image"] = (
			images.get((store, str(row.get("sku") or "")))
			or images.get((store, str(row.get("offer_id") or "")))
			or ""
		)


def _get_seller_api_images(rows):
	"""Read product images without exposing credentials; cache results for six hours."""
	from fengjing_app.fengjing_business.doctype.fengjing___product_corresponding_platform___configuration.fengjing___product_corresponding_platform___configuration import (
		_发送ozon订单请求,
	)

	parent = frappe.get_single("Fengjing - Product Corresponding Platform - Configuration")
	configs = {
		str(row.get("店铺选项") or ""): row
		for row in (parent.get("table_wckx") or [])
		if row.get("店铺选项") and row.get("ozon_id") and row.get("ozon_秘钥")
	}
	result = {}
	cache = frappe.cache
	grouped = {}
	for row in rows:
		store = str(row.get("store") or "")
		offer_id = str(row.get("offer_id") or "")
		if store in configs and offer_id:
			grouped.setdefault(store, set()).add(offer_id)

	for store, offer_ids in grouped.items():
		config = configs[store]
		ozon_id = str(config.get("ozon_id") or "")
		uncached = []
		for offer_id in offer_ids:
			cached = cache.get_value(f"fengjing:ozon-product-image:{ozon_id}:{offer_id}")
			if cached is None:
				uncached.append(offer_id)
			elif cached != "__none__":
				result[(store, offer_id)] = str(cached)

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
					sku = str(item.get("sku") or "")
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
						result[(store, offer_id)] = image
						if sku:
							result[(store, sku)] = image
				for offer_id in set(chunk) - returned:
					cache.set_value(
						f"fengjing:ozon-product-image:{ozon_id}:{offer_id}",
						"__none__", expires_in_sec=21600,
					)
			except Exception:
				frappe.logger("ozon_dashboard", allow_site=True).warning(
					"读取Ozon商品图片失败：店铺=%s", store, exc_info=True,
				)
	return result


def _distinct(fieldname):
	return [
		str(value) for value in frappe.get_all(
			DOCTYPE, filters=[[fieldname, "is", "set"]], pluck=fieldname,
			group_by=fieldname, order_by=fieldname, limit_page_length=0,
		) if value
	]


def _get_options():
	item_codes = _distinct("corresponding_item")
	item_names = {
		row.name: row.item_name or row.name for row in frappe.get_all(
			"Item", filters={"name": ["in", item_codes]},
			fields=["name", "item_name"], limit_page_length=0,
		)
	} if item_codes else {}
	return {
		"stores": _distinct("store"),
		"statuses": _distinct("ozon_status"),
		"fulfillments": _distinct("fulfillment_type"),
		"currencies": _distinct("currency_code"),
		"warehouses": _distinct("warehouse_name"),
		"items": [
			{"value": code, "label": item_names.get(code, code)} for code in item_codes
		],
	}


def _get_sync_health():
	result = []
	try:
		parent = frappe.get_single("Fengjing - Product Corresponding Platform - Configuration")
		for row in parent.get("table_wckx") or []:
			result.append({
				"store": row.get("店铺选项") or "",
				"enabled": bool(row.get("开启订单同步")),
				"task_status": row.get("当前任务状态") or "",
				"history_status": row.get("历史同步状态") or "",
				"history_progress": flt(row.get("历史同步进度")),
				"last_sync": row.get("上次自动同步时间"),
				"next_sync": row.get("下次自动同步时间"),
				"last_result": row.get("上次自动同步结果") or "",
				"error": row.get("最近错误") or "",
			})
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Ozon店铺概况：读取同步状态失败")
	return result
