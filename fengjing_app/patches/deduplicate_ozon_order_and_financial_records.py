import json
from collections import defaultdict

import frappe


ORDER_DOCTYPE = "Ozon order storage"
ORDER_KEY_FIELD = "order_line_key"
FINANCIAL_DOCTYPE = "Ozon Financial Storage"
FINANCIAL_KEY_FIELD = "transaction_unique_key"

SYSTEM_FIELDS = {
	"name",
	"creation",
	"modified",
	"modified_by",
	"owner",
	"docstatus",
	"idx",
	"doctype",
	"_user_tags",
	"_comments",
	"_assign",
	"_liked_by",
}


def _json_array(value):
	try:
		parsed = json.loads(value or "[]")
		return parsed if isinstance(parsed, list) else []
	except (TypeError, ValueError, json.JSONDecodeError):
		return []


def _json_value(value):
	try:
		return json.loads(value)
	except (TypeError, ValueError, json.JSONDecodeError):
		return value


def _history_identity(entry):
	if not isinstance(entry, dict):
		return json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str)
	return "|".join(
		(
			str(entry.get("raw_json_hash") or ""),
			json.dumps(entry.get("raw_json"), ensure_ascii=False, sort_keys=True, default=str),
		)
	)


def _merged_history(docs, source):
	result = []
	seen = set()
	for doc in docs:
		for entry in _json_array(doc.get("raw_json_history")):
			identity = _history_identity(entry)
			if identity not in seen:
				seen.add(identity)
				result.append(entry)

		if doc.name == source.name or not doc.get("raw_json"):
			continue
		entry = {
			"archived_at": str(doc.get("fetched_at") or doc.modified),
			"sync_type": doc.get("sync_type"),
			"raw_json_hash": doc.get("raw_json_hash"),
			"raw_json": _json_value(doc.get("raw_json")),
		}
		identity = _history_identity(entry)
		if identity not in seen:
			seen.add(identity)
			result.append(entry)
	return result


def _sort_datetime(doc):
	return str(doc.get("fetched_at") or doc.modified or doc.creation or "")


def _merge_group(doctype, key_field, canonical_key, names):
	docs = [frappe.get_doc(doctype, name) for name in names]
	if len(docs) == 1 and docs[0].get(key_field) == canonical_key:
		return 0
	source = max(docs, key=_sort_datetime)
	survivor = next((doc for doc in docs if doc.get(key_field) == canonical_key), source)
	history = _merged_history(docs, source)

	if source.name != survivor.name:
		valid_fields = set(frappe.get_meta(doctype).get_valid_columns())
		for fieldname in valid_fields - SYSTEM_FIELDS - {key_field, "first_fetched_at", "raw_json_history"}:
			survivor.set(fieldname, source.get(fieldname))

	first_fetched_values = [doc.get("first_fetched_at") for doc in docs if doc.get("first_fetched_at")]
	if first_fetched_values:
		survivor.first_fetched_at = min(first_fetched_values)
	survivor.set(key_field, canonical_key)
	survivor.raw_json_history = json.dumps(history, ensure_ascii=False, sort_keys=True, indent=2)

	removed = [doc.name for doc in docs if doc.name != survivor.name]
	if removed:
		frappe.db.delete(doctype, {"name": ["in", removed]})
	survivor.save(ignore_permissions=True)
	return len(removed)


def _order_groups():
	from fengjing_app.fengjing_business.doctype.ozon_order_configuration.ozon_order_configuration import (
		_line_key,
	)

	groups = defaultdict(list)
	rows = frappe.get_all(
		ORDER_DOCTYPE,
		fields=[
			"name",
			"store",
			"fulfillment_type",
			"posting_number",
			"sku",
			"offer_id",
			"product_id",
		],
		limit_page_length=0,
	)
	for row in rows:
		key = _line_key(
			row.store,
			row.fulfillment_type,
			row.posting_number,
			row.sku,
			row.offer_id,
			row.product_id,
		)
		groups[key].append(row.name)
	return groups


def _financial_groups():
	from fengjing_app.fengjing_business.doctype.ozon_financial_configuration.ozon_financial_configuration import (
		_交易唯一键,
	)

	groups = defaultdict(list)
	skipped = 0
	rows = frappe.get_all(
		FINANCIAL_DOCTYPE,
		fields=["name", "store", "raw_json", FINANCIAL_KEY_FIELD],
		limit_page_length=0,
	)
	for row in rows:
		try:
			payload = json.loads(row.raw_json or "{}")
			if not isinstance(payload, dict):
				raise ValueError
			key = _交易唯一键(row.store, payload)
		except (TypeError, ValueError, json.JSONDecodeError):
			skipped += 1
			groups[row.get(FINANCIAL_KEY_FIELD) or f"legacy-{row.name}"].append(row.name)
			continue
		groups[key].append(row.name)
	return groups, skipped


def execute():
	"""Canonicalize Ozon keys and merge rows left by older key algorithms."""
	result = {
		"order_groups": 0,
		"order_removed": 0,
		"financial_groups": 0,
		"financial_removed": 0,
		"financial_skipped": 0,
	}

	for canonical_key, names in _order_groups().items():
		result["order_groups"] += 1
		result["order_removed"] += _merge_group(
			ORDER_DOCTYPE, ORDER_KEY_FIELD, canonical_key, names
		)

	financial_groups, skipped = _financial_groups()
	result["financial_skipped"] = skipped
	for canonical_key, names in financial_groups.items():
		result["financial_groups"] += 1
		result["financial_removed"] += _merge_group(
			FINANCIAL_DOCTYPE, FINANCIAL_KEY_FIELD, canonical_key, names
		)

	frappe.db.commit()
	return result
