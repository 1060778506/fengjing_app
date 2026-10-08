import calendar
from collections import defaultdict
from datetime import timedelta

import frappe
from frappe.utils import cint, getdate


SUMMARY_DOCTYPE = "Amazon FBA Inventory Ledger Summary"
DETAIL_DOCTYPE = "Amazon FBA Inventory Ledger Detail"

SUMMARY_FIELDS = [
	"name", "amazon_store", "cost_center", "marketplace_id", "country",
	"seller_sku", "fnsku", "asin", "product_name", "corresponding_item",
	"corresponding_item_name", "disposition", "period_date", "report_start_date",
	"report_end_date", "time_aggregation", "location_type", "location_id", "report_id",
	"starting_warehouse_balance", "receipts", "customer_shipments", "customer_returns",
	"vendor_returns", "ending_warehouse_balance", "in_transit_between_warehouses",
	"warehouse_transfer_in", "warehouse_transfer_out", "found_quantity", "lost_quantity",
	"damaged_quantity", "disposed_quantity", "other_events_quantity",
	"unknown_events_quantity", "fetched_at",
]

DETAIL_FIELDS = [
	"name", "amazon_store", "cost_center", "marketplace_id", "country",
	"seller_sku", "fnsku", "asin", "product_name", "corresponding_item",
	"corresponding_item_name", "event_at", "event_type", "reference_id", "quantity",
	"fulfillment_center", "disposition", "reason", "report_id", "reconciled_quantity",
	"unreconciled_quantity", "fetched_at",
]


def _filters(value=None):
	if isinstance(value, str):
		value = frappe.parse_json(value)
	return frappe._dict(value or {})


def _require_read():
	frappe.has_permission(SUMMARY_DOCTYPE, "read", throw=True)
	frappe.has_permission(DETAIL_DOCTYPE, "read", throw=True)


def _summary_filters(filters):
	result = {}
	for key in ("amazon_store", "country", "corresponding_item", "location_id", "disposition"):
		if filters.get(key):
			result[key] = filters.get(key)
	if filters.date_from and filters.date_to:
		result["period_date"] = ["between", [filters.date_from, filters.date_to]]
	elif filters.date_from:
		result["period_date"] = [">=", filters.date_from]
	elif filters.date_to:
		result["period_date"] = ["<=", filters.date_to]
	return result


def _detail_filters(filters):
	result = {}
	for key in ("amazon_store", "country", "corresponding_item", "disposition"):
		if filters.get(key):
			result[key] = filters.get(key)
	if filters.event_type:
		result["event_type"] = filters.event_type
	if filters.date_from and filters.date_to:
		result["event_at"] = ["between", [f"{filters.date_from} 00:00:00", f"{filters.date_to} 23:59:59"]]
	elif filters.date_from:
		result["event_at"] = [">=", f"{filters.date_from} 00:00:00"]
	elif filters.date_to:
		result["event_at"] = ["<=", f"{filters.date_to} 23:59:59"]
	return result


def _search_match(row, search):
	search = str(search or "").strip().casefold()
	if not search:
		return True
	return search in " ".join(
		str(row.get(key) or "") for key in (
			"seller_sku", "fnsku", "asin", "product_name", "corresponding_item",
			"corresponding_item_name", "amazon_store", "country", "location_id",
			"reference_id", "event_type", "reason",
		)
	).casefold()


def _item_images(rows):
	codes = {row.get("corresponding_item") for row in rows if row.get("corresponding_item")}
	if not codes:
		return {}
	return {
		row.name: row for row in frappe.get_all(
			"Item", filters={"name": ["in", sorted(codes)]}, fields=["name", "item_name", "image"],
			limit_page_length=0,
		)
	}


def _decorate(rows, images):
	for row in rows:
		item = images.get(row.corresponding_item)
		row.item_name = (item.item_name if item else None) or row.corresponding_item_name or ""
		row.item_image = (item.image if item else None) or ""
	return rows


def _latest_ending_balance(rows):
	latest = {}
	for row in rows:
		key = (
			row.amazon_store or "", row.seller_sku or "", row.fnsku or "", row.asin or "",
			row.location_id or "", row.disposition or "",
		)
		if key not in latest or row.period_date > latest[key].period_date:
			latest[key] = row
	return sum(cint(row.ending_warehouse_balance) for row in latest.values())


def _movement_totals(rows):
	fields = (
		("receipts", "接收入库"), ("customer_shipments", "客户出库"),
		("customer_returns", "客户退货"), ("vendor_returns", "供应商退货"),
		("warehouse_transfer_in", "仓库转入"), ("warehouse_transfer_out", "仓库转出"),
		("found_quantity", "盘盈"), ("lost_quantity", "丢失"),
		("damaged_quantity", "损坏"), ("disposed_quantity", "销毁"),
		("other_events_quantity", "其他事件"), ("unknown_events_quantity", "未知事件"),
	)
	return [
		{"field": field, "label": label, "quantity": sum(cint(row.get(field)) for row in rows)}
		for field, label in fields
	]


def _daily(rows):
	values = defaultdict(lambda: {"receipts": 0, "shipments": 0, "returns": 0, "adjustments": 0, "ending": 0})
	ending_rows = defaultdict(list)
	for row in rows:
		day = str(getdate(row.period_date))
		values[day]["receipts"] += cint(row.receipts)
		values[day]["shipments"] += cint(row.customer_shipments)
		values[day]["returns"] += cint(row.customer_returns)
		values[day]["adjustments"] += (
			cint(row.found_quantity) - cint(row.lost_quantity) - cint(row.damaged_quantity)
			- cint(row.disposed_quantity) + cint(row.other_events_quantity)
		)
		ending_rows[day].append(row)
	for day, day_rows in ending_rows.items():
		values[day]["ending"] = _latest_ending_balance(day_rows)
	return [{"date": day, **value} for day, value in sorted(values.items())]


def _options():
	rows = frappe.get_all(
		SUMMARY_DOCTYPE,
		fields=["amazon_store", "country", "location_id", "disposition"],
		group_by="amazon_store, country, location_id, disposition",
		limit_page_length=0,
	)
	events = frappe.get_all(
		DETAIL_DOCTYPE, fields=["event_type"], group_by="event_type", limit_page_length=0,
	)
	return {
		"stores": sorted({row.amazon_store for row in rows if row.amazon_store}),
		"countries": sorted({row.country for row in rows if row.country}),
		"locations": sorted({row.location_id for row in rows if row.location_id}),
		"dispositions": sorted({row.disposition for row in rows if row.disposition}),
		"event_types": sorted({row.event_type for row in events if row.event_type}),
	}


@frappe.whitelist()
def get_ledger_data(filters=None, page=1, page_size=50):
	_require_read()
	filters = _filters(filters)
	summary_rows = frappe.get_list(
		SUMMARY_DOCTYPE,
		filters=_summary_filters(filters),
		fields=SUMMARY_FIELDS,
		order_by="period_date desc, amazon_store asc, seller_sku asc",
		limit_page_length=100000,
	)
	summary_rows = [row for row in summary_rows if _search_match(row, filters.search)]
	detail_rows = frappe.get_list(
		DETAIL_DOCTYPE,
		filters=_detail_filters(filters),
		fields=DETAIL_FIELDS,
		order_by="event_at desc",
		limit_page_length=100000,
	)
	detail_rows = [row for row in detail_rows if _search_match(row, filters.search)]

	images = _item_images([*summary_rows, *detail_rows])
	_decorate(summary_rows, images)
	page = max(cint(page), 1)
	page_size = min(max(cint(page_size), 10), 200)
	start = (page - 1) * page_size
	page_rows = summary_rows[start:start + page_size]
	event_totals = defaultdict(lambda: {"quantity": 0, "count": 0})
	for row in detail_rows:
		entry = event_totals[row.event_type or "UNKNOWN"]
		entry["quantity"] += cint(row.quantity)
		entry["count"] += 1

	return {
		"summary": {
			"rows": len(summary_rows),
			"detail_rows": len(detail_rows),
			"sku_count": len({(row.amazon_store, row.seller_sku) for row in summary_rows}),
			"latest_ending_balance": _latest_ending_balance(summary_rows),
			"receipts": sum(cint(row.receipts) for row in summary_rows),
			"shipments": sum(cint(row.customer_shipments) for row in summary_rows),
			"returns": sum(cint(row.customer_returns) for row in summary_rows),
			"unreconciled": sum(abs(cint(row.unreconciled_quantity)) for row in detail_rows),
		},
		"movement_totals": _movement_totals(summary_rows),
		"daily": _daily(summary_rows),
		"event_totals": [
			{"event_type": key, **value}
			for key, value in sorted(event_totals.items(), key=lambda item: abs(item[1]["quantity"]), reverse=True)
		],
		"rows": page_rows,
		"options": _options(),
		"pagination": {
			"page": page,
			"page_size": page_size,
			"total": len(summary_rows),
			"pages": max(1, (len(summary_rows) + page_size - 1) // page_size),
		},
		"limited": len(summary_rows) >= 100000 or len(detail_rows) >= 100000,
	}


def _period_end(period_date, aggregation):
	start = getdate(period_date)
	aggregation = str(aggregation or "DAILY").upper()
	if aggregation == "WEEKLY":
		return start + timedelta(days=6)
	if aggregation == "MONTHLY":
		return start.replace(day=calendar.monthrange(start.year, start.month)[1])
	return start


@frappe.whitelist()
def get_summary_details(name):
	_require_read()
	doc = frappe.get_doc(SUMMARY_DOCTYPE, name)
	doc.check_permission("read")
	start = getdate(doc.period_date)
	end = _period_end(start, doc.time_aggregation)
	filters = {
		"amazon_store": doc.amazon_store,
		"seller_sku": doc.seller_sku,
		"event_at": ["between", [f"{start} 00:00:00", f"{end} 23:59:59"]],
	}
	rows = frappe.get_list(
		DETAIL_DOCTYPE,
		filters=filters,
		fields=DETAIL_FIELDS,
		order_by="event_at desc, modified desc",
		limit_page_length=2000,
	)
	images = _item_images(rows)
	_decorate(rows, images)
	return {
		"rows": rows,
		"limited": len(rows) >= 2000,
		"period": {"date_from": str(start), "date_to": str(end)},
	}
