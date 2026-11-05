"""Unified Amazon FBA and AWD/GWD inbound shipment display."""

from collections import Counter, defaultdict

import frappe
from frappe.utils import cint, flt


FBA_SHIPMENT = "Amazon FBA Inbound Shipment"
FBA_ITEM = "Amazon FBA Inbound Shipment Item"
AWD_SHIPMENT = "Amazon AWD Inbound Shipment"
AWD_ITEM = "Amazon AWD Inbound Shipment Item"


def _filters(value=None):
	if isinstance(value, str):
		value = frappe.parse_json(value)
	return frappe._dict(value or {})


def _permissions():
	frappe.has_permission(FBA_SHIPMENT, "read", throw=True)
	frappe.has_permission(AWD_SHIPMENT, "read", throw=True)


def _date_filters(filters):
	conditions = {}
	if filters.date_from and filters.date_to:
		conditions["amazon_updated_at"] = ["between", [filters.date_from, f"{filters.date_to} 23:59:59"]]
	elif filters.date_from:
		conditions["amazon_updated_at"] = [">=", filters.date_from]
	elif filters.date_to:
		conditions["amazon_updated_at"] = ["<=", f"{filters.date_to} 23:59:59"]
	return conditions


def _item_counts(doctype, shipment_names):
	if not shipment_names:
		return {}
	counts = Counter()
	for row in frappe.get_all(
		doctype,
		filters={"inbound_shipment": ["in", shipment_names]},
		fields=["inbound_shipment"],
		limit_page_length=0,
	):
		counts[row.inbound_shipment] += 1
	return counts


def _awd_in_transit(status, expected, received):
	"""AWD does not return a standalone in-transit quantity on the shipment record."""
	value = str(status or "").strip().upper().replace("-", "_")
	active_transit_states = ("SHIPPED", "IN_TRANSIT", "RECEIVING")
	if not any(state in value for state in active_transit_states):
		return 0
	return max(flt(expected) - flt(received), 0)


def _load_fba(filters):
	db_filters = _date_filters(filters)
	if filters.amazon_store:
		db_filters["amazon_store"] = filters.amazon_store
	rows = frappe.get_all(
		FBA_SHIPMENT,
		filters=db_filters,
		fields=[
			"name", "shipment_id", "shipment_confirmation_id", "shipment_name", "shipment_status",
			"inbound_plan", "amazon_store", "marketplace_id", "fulfillment_center_id",
			"ship_to_city", "ship_to_state_or_province", "ship_to_country_code",
			"carrier_name", "tracking_number", "planned_quantity", "shipped_quantity",
			"received_quantity", "in_transit_quantity", "quantity_difference",
			"amazon_updated_at", "last_fetched_at", "sync_status", "last_error",
		],
		order_by="amazon_updated_at desc, modified desc",
		limit_page_length=10000,
	)
	counts = _item_counts(FBA_ITEM, [row.name for row in rows])
	result = []
	for row in rows:
		result.append(frappe._dict({
			"name": row.name,
			"doctype": FBA_SHIPMENT,
			"route": "amazon-fba-inbound-shipment",
			"program": "FBA",
			"shipment_id": row.shipment_id or row.shipment_confirmation_id or row.name,
			"shipment_name": row.shipment_name,
			"parent_reference": row.inbound_plan,
			"amazon_store": row.amazon_store,
			"marketplace_id": row.marketplace_id,
			"status": row.shipment_status,
			"sync_status": row.sync_status,
			"destination_code": row.fulfillment_center_id,
			"destination_label": " · ".join(filter(None, [row.ship_to_city, row.ship_to_state_or_province, row.ship_to_country_code])),
			"destination_warehouse": "",
			"carrier": row.carrier_name,
			"tracking_id": row.tracking_number,
			"item_count": counts.get(row.name, 0),
			"expected_quantity": flt(row.planned_quantity),
			"shipped_quantity": flt(row.shipped_quantity),
			"received_quantity": flt(row.received_quantity),
			"in_transit_quantity": flt(row.in_transit_quantity),
			"quantity_difference": flt(row.quantity_difference),
			"updated_at": row.amazon_updated_at or row.last_fetched_at,
			"last_error": row.last_error,
		}))
	return result


def _load_awd(filters):
	db_filters = _date_filters(filters)
	if filters.amazon_store:
		db_filters["amazon_store"] = filters.amazon_store
	if filters.program in {"AWD", "GWD", "Unrecognized"}:
		db_filters["warehousing_program"] = filters.program
	rows = frappe.get_all(
		AWD_SHIPMENT,
		filters=db_filters,
		fields=[
			"name", "shipment_id", "external_reference_id", "shipment_status", "inbound_order",
			"amazon_store", "marketplace_id", "warehousing_program", "destination_mapping",
			"amazon_destination_code", "destination_country_code", "destination_state_or_region",
			"destination_city", "erpnext_destination_warehouse", "carrier_code_value", "tracking_id",
			"expected_quantity", "received_quantity", "quantity_difference", "sku_count",
			"amazon_updated_at", "last_fetched_at", "sync_status", "last_error",
		],
		order_by="amazon_updated_at desc, modified desc",
		limit_page_length=10000,
	)
	counts = _item_counts(AWD_ITEM, [row.name for row in rows])
	result = []
	for row in rows:
		expected = flt(row.expected_quantity)
		received = flt(row.received_quantity)
		result.append(frappe._dict({
			"name": row.name,
			"doctype": AWD_SHIPMENT,
			"route": "amazon-awd-inbound-shipment",
			"program": row.warehousing_program or "Unrecognized",
			"shipment_id": row.shipment_id or row.name,
			"shipment_name": row.external_reference_id,
			"parent_reference": row.inbound_order,
			"amazon_store": row.amazon_store,
			"marketplace_id": row.marketplace_id,
			"status": row.shipment_status,
			"sync_status": row.sync_status,
			"destination_code": row.amazon_destination_code,
			"destination_label": " · ".join(filter(None, [row.destination_city, row.destination_state_or_region, row.destination_country_code])),
			"destination_mapping": row.destination_mapping,
			"destination_warehouse": row.erpnext_destination_warehouse,
			"carrier": row.carrier_code_value,
			"tracking_id": row.tracking_id,
			"item_count": counts.get(row.name, cint(row.sku_count)),
			"expected_quantity": expected,
			"shipped_quantity": None,
			"received_quantity": received,
			"in_transit_quantity": _awd_in_transit(row.shipment_status, expected, received),
			"quantity_difference": flt(row.quantity_difference),
			"updated_at": row.amazon_updated_at or row.last_fetched_at,
			"last_error": row.last_error,
		}))
	return result


def _matches(row, filters):
	if filters.program and row.program != filters.program:
		return False
	if filters.status and row.status != filters.status:
		return False
	if filters.destination and filters.destination not in {row.destination_code, row.destination_warehouse}:
		return False
	search = str(filters.search or "").strip().casefold()
	if search:
		haystack = " ".join(str(row.get(key) or "") for key in (
			"shipment_id", "shipment_name", "parent_reference", "amazon_store", "marketplace_id",
			"status", "destination_code", "destination_label", "destination_mapping",
			"destination_warehouse", "tracking_id",
		)).casefold()
		if search not in haystack:
			return False
	return True


def _summary(rows):
	programs = {}
	for program in ("FBA", "AWD", "GWD", "Unrecognized"):
		source = [row for row in rows if row.program == program]
		programs[program] = {
			"shipments": len(source),
			"items": sum(cint(row.item_count) for row in source),
			"expected": sum(flt(row.expected_quantity) for row in source),
			"shipped": sum(flt(row.shipped_quantity) for row in source),
			"received": sum(flt(row.received_quantity) for row in source),
			"in_transit": sum(flt(row.in_transit_quantity) for row in source),
			"difference": sum(flt(row.quantity_difference) for row in source),
		}
	return {
		"programs": programs,
		"shipments": len(rows),
		"items": sum(cint(row.item_count) for row in rows),
		"expected": sum(flt(row.expected_quantity) for row in rows),
		"received": sum(flt(row.received_quantity) for row in rows),
		"in_transit": sum(flt(row.in_transit_quantity) for row in rows),
		"difference": sum(flt(row.quantity_difference) for row in rows),
		"status_counts": dict(Counter(str(row.status or "未知状态") for row in rows)),
	}


@frappe.whitelist()
def get_inbound_data(filters=None):
	_permissions()
	filters = _filters(filters)
	rows = []
	if filters.program != "FBA":
		rows.extend(_load_awd(filters))
	if filters.program not in {"AWD", "GWD", "Unrecognized"}:
		rows.extend(_load_fba(filters))
	rows = [row for row in rows if _matches(row, filters)]
	rows.sort(key=lambda row: (str(row.updated_at or ""), row.shipment_id or ""), reverse=True)

	stores = sorted({row.amazon_store for row in rows if row.amazon_store})
	statuses = sorted({row.status for row in rows if row.status})
	destinations = sorted({
		value for row in rows for value in (row.destination_code, row.destination_warehouse) if value
	})
	return {
		"summary": _summary(rows),
		"rows": rows[:2000],
		"row_count": len(rows),
		"limited": len(rows) > 2000,
		"options": {"stores": stores, "statuses": statuses, "destinations": destinations},
	}
