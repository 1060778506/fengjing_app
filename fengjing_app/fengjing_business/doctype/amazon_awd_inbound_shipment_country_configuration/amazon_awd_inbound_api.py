# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""Small wrappers around Amazon AWD v2024-05-09 inbound operations."""

from datetime import timezone

from frappe.utils import get_datetime

from fengjing_app.fengjing_business.doctype.amazon_store_configuration.amazon_store_configuration import amazon_api_json


SERVICE = "awd-inbound"
BASE_PATH = "/awd/2024-05-09"


def amazon_datetime(value):
	if not value:
		return None
	dt = get_datetime(value)
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=timezone.utc)
	return dt.astimezone(timezone.utc)


def frappe_datetime(value):
	dt = amazon_datetime(value)
	return dt.replace(tzinfo=None) if dt else None


def _iso(value):
	if not value:
		return None
	dt = value if hasattr(value, "tzinfo") else get_datetime(value)
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=timezone.utc)
	return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def iter_inbound_shipment_pages(store, *, updated_after, updated_before, sort_order="ASCENDING"):
	base = {
		"sortBy": "UPDATED_AT", "sortOrder": sort_order,
		"updatedAfter": _iso(updated_after), "updatedBefore": _iso(updated_before), "maxResults": 100,
	}
	next_token = None
	seen = set()
	while True:
		params = {key: value for key, value in base.items() if value not in (None, "")}
		if next_token:
			params["nextToken"] = next_token
		payload = amazon_api_json(store, SERVICE, "GET", f"{BASE_PATH}/inboundShipments", params=params, timeout=90)
		yield payload.get("shipments") or [], payload.get("nextToken")
		next_token = payload.get("nextToken")
		if not next_token:
			break
		if next_token in seen:
			raise RuntimeError("AWD Inbound API returned a repeated nextToken")
		seen.add(next_token)


def get_inbound_shipment(store, shipment_id):
	return amazon_api_json(
		store, SERVICE, "GET", f"{BASE_PATH}/inboundShipments/{shipment_id}",
		params={"skuQuantities": "SHOW"}, timeout=90,
	)


def get_inbound_order(store, order_id):
	return amazon_api_json(store, SERVICE, "GET", f"{BASE_PATH}/inboundOrders/{order_id}", timeout=90)
