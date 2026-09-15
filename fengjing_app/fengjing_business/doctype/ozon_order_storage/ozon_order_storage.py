# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import hashlib
import json
from datetime import timezone
from zoneinfo import ZoneInfo

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, get_system_timezone, now_datetime


class Ozonorderstorage(Document):
	pass


def _ozon_time_to_system(value):
	"""Convert an Ozon RFC3339 value to a naive ERPNext system-time datetime."""
	if not value:
		return None
	text = str(value).strip()
	if text.endswith("Z"):
		text = text[:-1] + "+00:00"
	try:
		parsed = get_datetime(text)
	except Exception:
		return None
	if parsed.tzinfo is None:
		parsed = parsed.replace(tzinfo=timezone.utc)
	return parsed.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)


def _safe_json_array(value):
	try:
		result = json.loads(value or "[]")
		return result if isinstance(result, list) else []
	except (TypeError, ValueError, json.JSONDecodeError):
		return []


def _append_previous_json(doc, new_hash):
	"""Archive the previous payload only when Ozon returned different JSON."""
	old_json = str(doc.get("raw_json") or "").strip()
	old_hash = str(doc.get("raw_json_hash") or "").strip()
	if not old_json or not old_hash or old_hash == new_hash:
		return False

	history = _safe_json_array(doc.get("raw_json_history"))
	try:
		old_payload = json.loads(old_json)
	except (TypeError, ValueError, json.JSONDecodeError):
		old_payload = old_json
	history.append(
		{
			"archived_at": str(now_datetime()),
			"ozon_status": doc.get("ozon_status"),
			"ozon_substatus": doc.get("ozon_substatus"),
			"sync_type": doc.get("sync_type"),
			"raw_json_hash": old_hash,
			"raw_json": old_payload,
		}
	)
	doc.raw_json_history = json.dumps(
		history, ensure_ascii=False, sort_keys=True, indent=2
	)
	return True


def _find_corresponding_item(store, sku, offer_id, product_id):
	"""Resolve an Item from the shared platform mapping table without requiring it."""
	if not store:
		return None, None, None

	base = {"启用": 1, "店铺": store}
	lookups = []
	for value in (offer_id, sku):
		value = str(value or "").strip()
		if value:
			lookups.append({**base, "平台sku": value})
	for value in (product_id, sku):
		value = str(value or "").strip()
		if value:
			lookups.append({**base, "平台asin": value})

	item_code = None
	for filters in lookups:
		item_code = frappe.db.get_value(
			"Fengjing - Product Corresponding Platform - Main Table",
			filters,
			"物料id",
		)
		if item_code:
			break
	if not item_code:
		return None, None, None

	item = frappe.db.get_value(
		"Item", item_code, ["item_name", "image"], as_dict=True
	) or {}
	return item_code, item.get("item_name"), item.get("image")


def _financial_product(posting, sku, product_id):
	rows = ((posting.get("financial_data") or {}).get("products") or [])
	wanted = {str(value) for value in (sku, product_id) if value not in (None, "")}
	for row in rows:
		if str(row.get("product_id") or "") in wanted:
			return row
	return {}


def _line_key(store, fulfillment_type, posting_number, sku, offer_id, product_id):
	identity = "|".join(
		str(value or "").strip()
		for value in (
			store,
			fulfillment_type,
			posting_number,
			sku or offer_id or product_id or "__ORDER__",
		)
	)
	return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def 保存ozon订单(posting, store, ozon_id, fulfillment_type, sync_type):
	"""Upsert one row per Ozon posting + SKU and preserve changed raw payloads."""
	if not isinstance(posting, dict):
		raise ValueError("Ozon订单数据必须是JSON对象")

	posting_number = str(posting.get("posting_number") or "").strip()
	order_id = str(posting.get("order_id") or "").strip()
	order_number = str(posting.get("order_number") or "").strip()
	if not posting_number and not order_id and not order_number:
		raise ValueError("Ozon订单缺少posting_number、order_id和order_number")
	posting_number = posting_number or order_number or order_id

	raw_text = json.dumps(posting, ensure_ascii=False, sort_keys=True, indent=2)
	raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
	analytics = posting.get("analytics_data") or {}
	delivery_method = posting.get("delivery_method") or {}
	cancellation = posting.get("cancellation") or {}
	customer = posting.get("customer") or {}
	address = customer.get("address") or posting.get("address") or {}

	base_data = {
		"store": store,
		"ozon_id": str(ozon_id or "").strip(),
		"fulfillment_type": fulfillment_type or "未知",
		"posting_number": posting_number,
		"parent_posting_number": posting.get("parent_posting_number"),
		"order_id": order_id,
		"order_number": order_number,
		"is_multibox": cint(posting.get("is_multibox")),
		"multi_box_qty": cint(posting.get("multi_box_qty")),
		"ozon_status": posting.get("status"),
		"ozon_substatus": posting.get("substatus"),
		"cancellation_reason": cancellation.get("cancel_reason"),
		"is_cancelled": cint(bool(cancellation.get("cancel_reason_id")) or posting.get("status") == "cancelled"),
		"is_express": cint(posting.get("is_express")),
		"sync_type": sync_type,
		"order_created_at": _ozon_time_to_system(posting.get("created_at") or posting.get("in_process_at")),
		"in_process_at": _ozon_time_to_system(posting.get("in_process_at")),
		"source_updated_at": _ozon_time_to_system(posting.get("updated_at") or posting.get("last_changed_status_date")),
		"shipment_date": _ozon_time_to_system(posting.get("shipment_date")),
		"delivering_date": _ozon_time_to_system(posting.get("delivering_date")),
		"delivery_date": _ozon_time_to_system(posting.get("delivery_date")),
		"warehouse_id": delivery_method.get("warehouse_id") or analytics.get("warehouse_id"),
		"warehouse_name": delivery_method.get("warehouse") or analytics.get("warehouse"),
		"delivery_method_id": delivery_method.get("id"),
		"delivery_type": analytics.get("delivery_type") or posting.get("tpl_integration_type"),
		"delivery_method_name": delivery_method.get("name"),
		"tracking_number": posting.get("tracking_number"),
		"destination_place_id": posting.get("destination_place_id"),
		"destination_place_name": posting.get("destination_place_name"),
		"shipping_country_code": address.get("country_code") or address.get("country"),
		"shipping_region": analytics.get("region") or address.get("region"),
		"shipping_city": analytics.get("city") or address.get("city"),
		"shipping_postal_code": address.get("zip_code") or address.get("postal_code"),
		"fetched_at": now_datetime(),
		"raw_json_hash": raw_hash,
		"sync_status": "成功",
		"last_error": None,
		"raw_json": raw_text,
	}
	base_data = {key: value for key, value in base_data.items() if value is not None}

	products = posting.get("products") or [{}]
	grouped = {}
	for product in products:
		sku = str(product.get("sku") or "").strip()
		offer_id = str(product.get("offer_id") or "").strip()
		product_id = str(product.get("product_id") or sku or "").strip()
		group_key = sku or offer_id or product_id or "__ORDER__"
		grouped.setdefault(group_key, []).append(product)

	created = updated = archived = 0
	document_names = []
	for _, product_rows in grouped.items():
		first = product_rows[0]
		sku = str(first.get("sku") or "").strip()
		offer_id = str(first.get("offer_id") or "").strip()
		product_id = str(first.get("product_id") or sku or "").strip()
		financial = _financial_product(posting, sku, product_id)
		quantity = sum(cint(row.get("quantity")) for row in product_rows)
		unit_price = flt(first.get("price") or financial.get("price"))
		currency = first.get("currency_code") or financial.get("currency_code")
		item_code, item_name, item_image = _find_corresponding_item(
			store, sku, offer_id, product_id
		)
		unique_key = _line_key(
			store, fulfillment_type, posting_number, sku, offer_id, product_id
		)
		line_data = {
			**base_data,
			"order_line_key": unique_key,
			"product_id": product_id,
			"sku": sku,
			"offer_id": offer_id,
			"product_name": first.get("name"),
			"corresponding_item": item_code,
			"corresponding_item_name": item_name,
			"corresponding_item_image": item_image,
			"currency_code": currency,
			"unit_price": unit_price,
			"line_amount": sum(flt(row.get("price")) * cint(row.get("quantity")) for row in product_rows),
			"quantity": quantity,
			"commission_amount": (
				flt(financial.get("commission_amount"))
				if "commission_amount" in financial
				else None
			),
			"payout_amount": (
				flt(financial.get("payout")) if "payout" in financial else None
			),
		}
		line_data = {key: value for key, value in line_data.items() if value not in (None, "")}

		existing_name = frappe.db.get_value(
			"Ozon order storage", {"order_line_key": unique_key}, "name"
		)
		if existing_name:
			doc = frappe.get_doc("Ozon order storage", existing_name)
			if _append_previous_json(doc, raw_hash):
				archived += 1
			doc.update(line_data)
			doc.save(ignore_permissions=True)
			updated += 1
		else:
			line_data["first_fetched_at"] = now_datetime()
			doc = frappe.get_doc({"doctype": "Ozon order storage", **line_data})
			doc.insert(ignore_permissions=True)
			created += 1
		document_names.append(doc.name)

	return {
		"created": created,
		"updated": updated,
		"archived": archived,
		"document_names": document_names,
	}
