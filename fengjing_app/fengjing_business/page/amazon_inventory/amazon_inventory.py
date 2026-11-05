from collections import defaultdict

import frappe
from frappe.utils import cint, getdate


FBA_DOCTYPE = "Amazon FBA Inventory Snapshot"
AWD_DOCTYPE = "Amazon AWD Inventory Snapshot"

FBA_FIELDS = [
	"name", "amazon_store", "cost_center", "marketplace_id", "country",
	"seller_sku", "fnsku", "asin", "product_name", "corresponding_item",
	"corresponding_item_name", "condition", "snapshot_at", "snapshot_date",
	"source_updated_at", "total_quantity", "fulfillable_quantity",
	"total_reserved_quantity", "total_unfulfillable_quantity",
	"pending_customer_order_quantity", "pending_transshipment_quantity",
	"fc_processing_quantity",
	"inbound_working_quantity", "inbound_shipped_quantity",
	"inbound_receiving_quantity", "total_researching_quantity",
]

AWD_FIELDS = [
	"name", "amazon_store", "cost_center", "marketplace_id", "country",
	"seller_sku", "product_name", "corresponding_item",
	"corresponding_item_name", "snapshot_at", "snapshot_date",
	"source_updated_at", "total_onhand_quantity", "total_inbound_quantity",
	"available_distributable_quantity", "reserved_distributable_quantity",
	"replenishment_quantity",
]


def _filters(value=None):
	if isinstance(value, str):
		value = frappe.parse_json(value)
	return frappe._dict(value or {})


def _read_permission():
	frappe.has_permission(FBA_DOCTYPE, "read", throw=True)
	frappe.has_permission(AWD_DOCTYPE, "read", throw=True)


def _db_filters(filters):
	result = {}
	if filters.amazon_store:
		result["amazon_store"] = filters.amazon_store
	if filters.country:
		result["country"] = filters.country
	if filters.corresponding_item:
		result["corresponding_item"] = filters.corresponding_item
	if filters.date_from and filters.date_to:
		result["snapshot_date"] = ["between", [filters.date_from, filters.date_to]]
	elif filters.date_from:
		result["snapshot_date"] = [">=", filters.date_from]
	elif filters.date_to:
		result["snapshot_date"] = ["<=", filters.date_to]
	return result


def _load_rows(doctype, fields, filters):
	return frappe.get_list(
		doctype,
		filters=_db_filters(filters),
		fields=fields,
		order_by="snapshot_at desc, modified desc",
		limit_page_length=100000,
	)


def _row_identity(row, platform):
	if platform == "AWD":
		return (row.get("amazon_store") or "", row.get("seller_sku") or row.get("name") or "")
	return (
		row.get("amazon_store") or "", row.get("seller_sku") or "", row.get("fnsku") or "",
		row.get("condition") or "",
	)


def _latest_per_item(rows, platform):
	latest = {}
	for row in rows:
		key = _row_identity(row, platform)
		if key not in latest or row.get("snapshot_at") > latest[key].get("snapshot_at"):
			latest[key] = row
	return list(latest.values())


def _matches_search(row, filters):
	search = str(filters.search or "").strip().casefold()
	if search:
		haystack = " ".join(
			str(row.get(key) or "")
			for key in (
				"seller_sku", "fnsku", "asin", "product_name", "corresponding_item",
				"corresponding_item_name", "amazon_store", "country",
			)
		).casefold()
		if search not in haystack:
			return False
	if filters.mapping == "bound" and not row.get("corresponding_item"):
		return False
	if filters.mapping == "unbound" and row.get("corresponding_item"):
		return False
	return True


def _item_images(rows):
	item_codes = {row.get("corresponding_item") for row in rows if row.get("corresponding_item")}
	if not item_codes:
		return {}
	return {
		row.name: row
		for row in frappe.get_all(
			"Item",
			filters={"name": ["in", sorted(item_codes)]},
			fields=["name", "item_name", "image"],
			limit_page_length=0,
		)
	}


def _decorate_fba(rows, images):
	result = []
	for source in rows:
		row = frappe._dict(source)
		item = images.get(row.corresponding_item)
		row.platform = "FBA"
		row.item_name = (item.item_name if item else None) or row.corresponding_item_name or ""
		row.item_image = (item.image if item else None) or ""
		row.total = cint(row.total_quantity)
		row.available = cint(row.fulfillable_quantity)
		row.reserved = cint(row.total_reserved_quantity)
		row.reserved_customer_orders = cint(row.pending_customer_order_quantity)
		row.reserved_transshipment = cint(row.pending_transshipment_quantity)
		row.reserved_fc_processing = cint(row.fc_processing_quantity)
		row.inbound = sum(cint(row.get(key)) for key in (
			"inbound_working_quantity", "inbound_shipped_quantity", "inbound_receiving_quantity",
		))
		row.unavailable = cint(row.total_unfulfillable_quantity)
		row.replenishment = 0
		row.source_store_count = 1
		row.source_stores = [row.amazon_store]
		result.append(row)
	return result


def _decorate_awd(rows, images):
	decorated = []
	for source in rows:
		row = frappe._dict(source)
		item = images.get(row.corresponding_item)
		row.platform = "AWD"
		row.item_name = (item.item_name if item else None) or row.corresponding_item_name or ""
		row.item_image = (item.image if item else None) or ""
		row.total = cint(row.total_onhand_quantity)
		row.available = cint(row.available_distributable_quantity)
		row.reserved = cint(row.reserved_distributable_quantity)
		row.reserved_customer_orders = None
		row.reserved_transshipment = None
		row.reserved_fc_processing = None
		row.inbound = cint(row.total_inbound_quantity)
		row.unavailable = 0
		row.replenishment = cint(row.replenishment_quantity)
		row.source_store_count = 1
		row.source_stores = [row.amazon_store]
		decorated.append(row)
	return decorated


def _trend(rows, platform, filters):
	# 每个 SKU 每天只保留最后一次快照，并向后延续未变化 SKU 的状态。
	daily_changes = defaultdict(dict)
	for row in rows:
		day = str(getdate(row.snapshot_date or row.snapshot_at))
		key = _row_identity(row, platform)
		current = daily_changes[day].get(key)
		if not current or row.snapshot_at > current.snapshot_at:
			daily_changes[day][key] = row

	state = {}
	result = []
	for day in sorted(daily_changes):
		state.update(daily_changes[day])
		values = {"total": 0, "available": 0, "reserved": 0, "inbound": 0}
		for row in state.values():
			if platform == "FBA":
				values["total"] += cint(row.total_quantity)
				values["available"] += cint(row.fulfillable_quantity)
				values["reserved"] += cint(row.total_reserved_quantity)
				values["inbound"] += sum(cint(row.get(key)) for key in (
				"inbound_working_quantity", "inbound_shipped_quantity", "inbound_receiving_quantity",
				))
			else:
				values["total"] += cint(row.total_onhand_quantity)
				values["available"] += cint(row.available_distributable_quantity)
				values["reserved"] += cint(row.reserved_distributable_quantity)
				values["inbound"] += cint(row.total_inbound_quantity)
		result.append({"date": day, "platform": platform, **values})
	return result


def _options():
	stores = set()
	countries = set()
	for doctype in (FBA_DOCTYPE, AWD_DOCTYPE):
		for row in frappe.get_all(
			doctype,
			fields=["amazon_store", "country"],
			group_by="amazon_store, country",
			limit_page_length=0,
		):
			if row.amazon_store:
				stores.add(row.amazon_store)
			if row.country:
				countries.add(row.country)
	return {"stores": sorted(stores), "countries": sorted(countries)}


@frappe.whitelist()
def get_inventory_data(filters=None):
	_read_permission()
	filters = _filters(filters)
	fba_all = [row for row in _load_rows(FBA_DOCTYPE, FBA_FIELDS, filters) if _matches_search(row, filters)]
	awd_all = [row for row in _load_rows(AWD_DOCTYPE, AWD_FIELDS, filters) if _matches_search(row, filters)]

	fba_latest = _latest_per_item(fba_all, "FBA")
	awd_latest = _latest_per_item(awd_all, "AWD")
	images = _item_images([*fba_latest, *awd_latest])
	fba_rows = _decorate_fba(fba_latest, images)
	awd_rows = _decorate_awd(awd_latest, images)

	platform = str(filters.platform or "").upper()
	rows = fba_rows + awd_rows
	if platform in {"FBA", "AWD"}:
		rows = [row for row in rows if row.platform == platform]
	rows.sort(key=lambda row: (row.platform, row.country or "", -(row.total or 0), row.seller_sku or ""))

	def totals(source):
		return {
			"sku_count": len(source),
			"total": sum(cint(row.total) for row in source),
			"available": sum(cint(row.available) for row in source),
			"reserved": sum(cint(row.reserved) for row in source),
			"reserved_customer_orders": sum(cint(row.reserved_customer_orders) for row in source),
			"reserved_transshipment": sum(cint(row.reserved_transshipment) for row in source),
			"reserved_fc_processing": sum(cint(row.reserved_fc_processing) for row in source),
			"inbound": sum(cint(row.inbound) for row in source),
			"unavailable": sum(cint(row.unavailable) for row in source),
			"replenishment": sum(cint(row.replenishment) for row in source),
			"bound": sum(1 for row in source if row.corresponding_item),
		}

	return {
		"summary": {"fba": totals(fba_rows), "awd": totals(awd_rows)},
		"rows": rows[:5000],
		"row_count": len(rows),
		"limited": len(rows) > 5000,
		"trend": [*_trend(fba_all, "FBA", filters), *_trend(awd_all, "AWD", filters)],
		"options": _options(),
		"latest": {
			"fba": str(max((row.snapshot_at for row in fba_latest), default="") or ""),
			"awd": str(max((row.snapshot_at for row in awd_latest), default="") or ""),
		},
		"awd_deduplicated": False,
	}
