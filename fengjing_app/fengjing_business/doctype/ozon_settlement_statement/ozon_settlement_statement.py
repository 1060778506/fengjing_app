# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""Persistence helpers for Ozon settlement-period statements."""

import hashlib
import json
import math
from datetime import timezone
from zoneinfo import ZoneInfo

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, get_system_timezone, now_datetime


DOCTYPE = "Ozon Settlement Statement"


class OzonSettlementStatement(Document):
	pass


def _number(value):
	if isinstance(value, dict):
		value = value.get("amount", value.get("value"))
	try:
		result = float(value)
		return result if math.isfinite(result) else 0.0
	except (TypeError, ValueError):
		return 0.0


def _mapping(value):
	return value if isinstance(value, dict) else {}


def _system_datetime(value):
	if not value:
		return None
	text = str(value).strip()
	if len(text) == 10:
		text += "T00:00:00+00:00"
	elif text.endswith("Z"):
		text = text[:-1] + "+00:00"
	try:
		parsed = get_datetime(text)
	except Exception:
		return None
	if parsed.tzinfo is None:
		parsed = parsed.replace(tzinfo=timezone.utc)
	return parsed.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)


def _json_text(value):
	return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str)


def _json_list(value):
	try:
		result = json.loads(value or "[]")
		return result if isinstance(result, list) else []
	except (TypeError, ValueError, json.JSONDecodeError):
		return []


def _changed_fields(doc, values):
	ignored = {
		"fetched_at",
		"sync_type",
		"sync_status",
		"last_error",
		"raw_json",
		"raw_json_hash",
		"data_changed",
		"changed_fields",
	}
	changed = []
	for key, value in values.items():
		if key in ignored:
			continue
		old_value = doc.get(key)
		if isinstance(value, float):
			if abs(flt(old_value) - value) > 0.000001:
				changed.append(key)
		elif str(old_value or "") != str(value or ""):
			changed.append(key)
	return changed


def _currency(details, payments, store):
	code = str(
		payments.get("currency_code")
		or payments.get("currency")
		or details.get("currency_code")
		or details.get("currency")
		or store.default_currency
		or "RUB"
	).strip().upper()
	return code if code and frappe.db.exists("Currency", code) else None


def save_settlement_statement(details, store, sync_type):
	"""Upsert one Ozon statement period and archive changed raw responses."""
	if not isinstance(details, dict):
		raise ValueError("Ozon settlement statement must be a JSON object")

	period = _mapping(details.get("period"))
	period_id = period.get("id")
	period_begin = period.get("begin") or period.get("date_from")
	period_end = period.get("end") or period.get("date_to")
	if not period_id and not (period_begin and period_end):
		raise ValueError("Ozon settlement statement is missing its period identity")

	identity = f"statement|{store.cost_center}|{period_id or period_begin}|{period_end}"
	record_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
	raw_text = _json_text(details)
	raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
	payments = _mapping(details.get("payments"))
	delivery = _mapping(details.get("delivery"))
	returns = _mapping(details.get("return"))
	services = _mapping(details.get("services"))
	others = _mapping(details.get("others"))
	begin_balance = _number(details.get("begin_balance_amount"))
	end_balance = _number(details.get("end_balance_amount"))
	statement_number = str(
		details.get("number") or details.get("statement_number") or period_id or record_key[:16]
	).strip()[:140]

	values = {
		"statement_record_key": record_key,
		"statement_id": str(period_id or "").strip()[:140],
		"statement_number": statement_number,
		"ozon_store": store.name,
		"store": store.cost_center,
		"ozon_id": str(store.ozon_id or "").strip(),
		"sync_type": sync_type,
		"settlement_period_start": _system_datetime(period_begin),
		"settlement_period_end": _system_datetime(period_end),
		"payment_date": _system_datetime(details.get("payment_date")),
		"currency_code": _currency(details, payments, store),
		"statement_status": str(details.get("status") or "Generated").strip()[:140],
		"begin_balance_amount": begin_balance,
		"end_balance_amount": end_balance,
		"payment_amount": _number(payments.get("payment")),
		"net_amount": end_balance - begin_balance,
		"delivery_charge": _number(delivery.get("total")),
		"return_delivery_charge": _number(returns.get("total")),
		"services_amount": _number(services.get("total")),
		"other_amount": _number(others.get("total")),
		"fetched_at": now_datetime(),
		"raw_json_hash": raw_hash,
		"sync_status": "Success",
		"last_error": "",
		"raw_json": raw_text,
	}
	values = {key: value for key, value in values.items() if value not in (None, "")}
	existing_name = frappe.db.get_value(DOCTYPE, {"statement_record_key": record_key}, "name")
	if existing_name:
		doc = frappe.get_doc(DOCTYPE, existing_name)
		changed_fields = _changed_fields(doc, values)
		changed = str(doc.raw_json_hash or "") != raw_hash
		if changed and doc.raw_json and doc.raw_json_hash:
			history = _json_list(doc.raw_json_history)
			history.append(
				{
					"version": cint(doc.data_version or 1),
					"archived_at": str(now_datetime()),
					"changed_fields": changed_fields,
					"raw_json_hash": doc.raw_json_hash,
					"raw_json": json.loads(doc.raw_json),
				}
			)
			doc.raw_json_history = _json_text(history[-50:])
		values.update(
			{
				"data_changed": cint(changed),
				"changed_fields": ", ".join(changed_fields),
				"last_changed_at": now_datetime() if changed else doc.last_changed_at,
				"data_version": cint(doc.data_version or 1) + (1 if changed else 0),
			}
		)
		doc.update(values)
		doc.save(ignore_permissions=True)
		return {"created": 0, "updated": 1, "archived": cint(changed), "name": doc.name}

	values.update(
		{
			"data_version": 1,
			"data_changed": 0,
			"changed_fields": "",
			"last_changed_at": now_datetime(),
			"raw_json_history": "[]",
		}
	)
	doc = frappe.get_doc({"doctype": DOCTYPE, **values})
	doc.insert(ignore_permissions=True)
	return {"created": 1, "updated": 0, "archived": 0, "name": doc.name}
