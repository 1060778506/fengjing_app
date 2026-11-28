import hashlib
import json
from decimal import Decimal, InvalidOperation

import frappe
from frappe.utils import get_datetime, now_datetime

from .tiktok_api import TikTokAPIError


def source_id(row, field):
	value = row.get(field)
	if not isinstance(value, (str, int)) or not str(value).strip():
		raise TikTokAPIError(f"TikTok 记录缺少 {field}，本段未推进同步进度。")
	return str(value)


def amount(value, currency=None):
	if isinstance(value, dict):
		currency = value.get("currency") or currency
		value = value.get("amount", value.get("value"))
	try:
		value = Decimal(str(value))
		if not value.is_finite():
			value = None
	except (InvalidOperation, ValueError):
		value = None
	return value, currency


def _hash(value):
	return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def save_record(doctype, store, external_id, row, values):
	identity = _hash([store, external_id])
	raw = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
	digest = _hash(row)
	fetched = now_datetime()
	values = {**values, "raw_json": raw, "raw_json_hash": digest, "fetched_at": fetched}
	existing = frappe.db.get_value(
		doctype, identity, ["raw_json", "raw_json_hash", "raw_json_history", "source_updated_at", "fetched_at"],
		as_dict=True, for_update=True,
	)
	if not existing:
		frappe.get_doc({
			"doctype": doctype,
			"identity_key": identity,
			"record_label": str(external_id)[:140],
			"tiktok_store": store,
			"first_raw_json": raw,
			"raw_json_history": "[]",
			**values,
		}).insert(ignore_permissions=True)
		return "created"
	if existing.raw_json_hash == digest:
		frappe.db.set_value(doctype, identity, "fetched_at", fetched, update_modified=False)
		return "unchanged"

	history = json.loads(existing.raw_json_history or "[]")
	older = (
		values.get("source_updated_at") and existing.source_updated_at
		and get_datetime(values["source_updated_at"]) < get_datetime(existing.source_updated_at)
	)
	prior = {
		"fetched_at": str(fetched),
		"hash": digest,
		"raw_json": raw,
	} if older else {
		"fetched_at": str(existing.fetched_at or ""),
		"hash": existing.raw_json_hash,
		"raw_json": existing.raw_json,
	}
	if not any(snapshot["hash"] == prior["hash"] for snapshot in history):
		history.append(prior)
	history_value = json.dumps(history, ensure_ascii=False)
	if older:
		frappe.db.set_value(doctype, identity, "raw_json_history", history_value)
		return "older"
	frappe.db.set_value(doctype, identity, {**values, "raw_json_history": history_value})
	return "updated"
