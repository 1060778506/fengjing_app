# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""Ozon financial synchronization driven by the independent configuration."""

import hashlib
import json
import math
from datetime import date, timedelta, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, get_system_timezone, getdate, now_datetime

from fengjing_app.fengjing_business.doctype.ozon_store_configuration.ozon_store_configuration import (
	ensure_database_connection,
	get_store,
	ozon_seller_request,
	response_error_summary,
)


DOCTYPE = "Ozon Financial Configuration"
STORAGE_DOCTYPE = "Ozon Financial Storage"
MAPPING_DOCTYPE = "Fengjing - Product Corresponding Platform - Main Table"
TASK_TIMEOUT = 12 * 60 * 60
MAX_PAGES = 10000
RECHECK_DAYS = (7, 14, 30, 90, 180)

SYNC_TYPE_LABELS = {
	"history": "历史财务",
	"incremental": "最新财务增量",
	"recheck_7": "7天财务核对",
	"recheck_14": "14天财务核对",
	"recheck_30": "30天财务核对",
	"recheck_90": "90天财务核对",
	"recheck_180": "180天财务核对",
}


class OzonFinancialConfiguration(Document):
	def validate(self):
		if self.ozon_store:
			get_store(self.ozon_store, require_enabled=False)
		self.incremental_interval_minutes = max(cint(self.incremental_interval_minutes), 1)
		self.incremental_lookback_days = max(cint(self.incremental_lookback_days), 1)
		for days in RECHECK_DAYS:
			fieldname = f"finance_recheck_{days}_interval_days"
			self.set(fieldname, max(cint(self.get(fieldname)), 1))
		if self.history_start_date and getdate(self.history_start_date) < date(2022, 1, 1):
			frappe.throw("Ozon financial accrual history starts from 2022-01-01")
		if self.history_start_date and self.history_end_date:
			if getdate(self.history_start_date) > getdate(self.history_end_date):
				frappe.throw("History start date cannot be later than history end date")


def _get_configuration(name):
	ensure_database_connection()
	return frappe.get_doc(DOCTYPE, name)


def _update_configuration(name, **values):
	ensure_database_connection()
	valid = set(frappe.get_meta(DOCTYPE).get_valid_columns())
	values = {key: value for key, value in values.items() if key in valid}
	if values:
		frappe.db.set_value(DOCTYPE, name, values, update_modified=False)
		frappe.db.commit()


def _request_json(store, path, body, api_name):
	response = ozon_seller_request(store, "POST", path, json_data=body, timeout=90)
	ensure_database_connection()
	if response is None or response.status_code != 200:
		status = response.status_code if response is not None else "no response"
		raise RuntimeError(f"Ozon {api_name} failed (HTTP {status}): {response_error_summary(response, 1800)}")
	try:
		payload = response.json()
	except ValueError as exc:
		raise RuntimeError(f"Ozon {api_name} returned invalid JSON") from exc
	if not isinstance(payload, dict):
		raise RuntimeError(f"Ozon {api_name} returned an unexpected structure")
	return payload


def _storage_context(store):
	return frappe._dict({"店铺选项": store.cost_center, "ozon_id": store.ozon_id})


def _get_accrual_types(store):
	payload = _request_json(store, "/v1/finance/accrual/types", {}, "financial accrual types API")
	rows = payload.get("accrual_types") or (payload.get("result") or {}).get("accrual_types") or []
	result = {}
	for row in rows:
		if not isinstance(row, dict):
			continue
		type_id = row.get("id", row.get("type_id"))
		if type_id is not None:
			result[str(type_id)] = {
				"name": row.get("name") or "",
				"description": row.get("description") or row.get("name") or "",
			}
	return result


def _安全数字(value):
	if isinstance(value, dict):
		value = value.get("amount", value.get("value"))
	try:
		result = float(value)
		return result if math.isfinite(result) else 0.0
	except (TypeError, ValueError):
		return 0.0


def _币种(value, 默认="RUB"):
	if isinstance(value, dict):
		return str(value.get("currency") or value.get("currency_code") or 默认).strip() or 默认
	return 默认


def _data(value, length=140):
	return str(value or "").strip()[:length]


def _ozon时间转系统时间(value):
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


def _json文本(value):
	return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def _安全历史数组(value):
	try:
		result = json.loads(value or "[]")
		return result if isinstance(result, list) else []
	except (TypeError, ValueError, json.JSONDecodeError):
		return []


def _遍历费用(node, path=""):
	result = []
	if isinstance(node, dict):
		if "type_id" in node and ("accrued" in node or "amount" in node or "price" in node):
			result.append({"path": path, **node})
		for key, value in node.items():
			result.extend(_遍历费用(value, f"{path}.{key}" if path else key))
	elif isinstance(node, list):
		for index, value in enumerate(node):
			result.extend(_遍历费用(value, f"{path}[{index}]"))
	return result


def _费用金额(row):
	for key in ("accrued", "amount", "price", "total_amount"):
		if key in row:
			return _安全数字(row.get(key))
	return 0.0


def _财务分类(type_id, type_text, accrued_category=""):
	text = f"{type_text} {accrued_category}".lower()
	rules = (
		("推广和广告", ("advert", "promo", "promotion", "payperclick", "реклам", "продвиж")),
		("Ozon代理佣金", ("commission", "agentfee", "agent fee", "комисс", "агентск")),
		("其他服务与罚款", ("penalty", "fine", "штраф")),
		("赔偿和赔偿返还", ("compensation", "компенсац")),
		("折扣积分", ("point", "cashback", "балл", "кешбэк")),
		("WHD服务", ("whd",)),
		("借贷和托收信贷", ("loan", "credit", "collection", "кредит", "заём")),
		("销售和退货", ("return", "refund", "sale", "order", "возврат", "продаж")),
		("Ozon配送服务", ("delivery", "logistic", "lastmile", "достав", "логист")),
		("合作伙伴计划", ("partner program", "партнёрск", "партнерск")),
		("合作伙伴服务", ("service", "placement", "storage", "packing", "acquiring", "услуг", "хранен", "упаков", "эквайринг")),
	)
	for category, keywords in rules:
		if any(word in text for word in keywords):
			return category
	if str(type_id) in {"10", "25"}:
		return "赔偿和赔偿返还"
	if str(type_id) in {"29", "32", "98"}:
		return "Ozon配送服务"
	if str(type_id) in {"41", "54"}:
		return "推广和广告"
	if str(type_id) == "1":
		return "合作伙伴服务"
	if str(type_id) == "66":
		return "Ozon代理佣金"
	if str(type_id) in {"45", "59"}:
		return "销售和退货"
	if "posting" in text:
		return "销售和退货"
	return "其他应计项目"


def _提取商品明细(accrual):
	posting_products = (accrual.get("posting") or {}).get("products") or []
	item_fee_products = (accrual.get("item_fees") or {}).get("fees") or []
	source = posting_products if posting_products else item_fee_products
	return [row for row in source if isinstance(row, dict)]


def _主要费用信息(accrual, type_map, fee_details):
	root_type_id = accrual.get("type_id")
	if root_type_id not in (None, ""):
		type_ids = [str(root_type_id)]
	else:
		type_ids = list(
			dict.fromkeys(
				str(row.get("type_id"))
				for row in fee_details
				if row.get("type_id") not in (None, "")
			)
		)
	names = []
	descriptions = []
	category_amounts = {}
	for type_id in type_ids:
		info = type_map.get(str(type_id), {})
		name = str(info.get("name") or "").strip()
		description = str(info.get("description") or name).strip()
		if name and name not in names:
			names.append(name)
		if description and description not in descriptions:
			descriptions.append(description)
	for fee in fee_details:
		text = f"{fee.get('type_name')} {fee.get('type_description')}"
		category = _财务分类(fee.get("type_id"), text, accrual.get("accrued_category"))
		category_amounts[category] = category_amounts.get(category, 0.0) + abs(
			_安全数字(fee.get("normalized_amount"))
		)
	root_category = str(accrual.get("accrued_category") or "")
	if category_amounts:
		category = max(category_amounts, key=category_amounts.get)
	elif root_category == "POSTING":
		category = "销售和退货"
	else:
		category = _财务分类(root_type_id, " ".join(names + descriptions), root_category)
	if names:
		name = " / ".join(names)
	elif root_category == "POSTING":
		name = "商品销售与佣金"
	else:
		name = root_category or "未知应计"
	return {
		"type_ids": ",".join(type_ids) or root_category,
		"name": name,
		"description": " / ".join(descriptions) or name,
		"category": category,
	}


def _收支方向(amount):
	if amount > 0:
		return "收入"
	if amount < 0:
		return "支出"
	return "中性"


def _查找对应物料(store, sku, offer_id, product_id):
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
		item_code = frappe.db.get_value(MAPPING_DOCTYPE, filters, "物料id")
		if item_code:
			break
	if not item_code:
		return None, None, None
	item = frappe.db.get_value("Item", item_code, ["item_name", "image"], as_dict=True) or {}
	return item_code, item.get("item_name"), item.get("image")


def _稳定标识片段(accrual):
	posting = accrual.get("posting") or {}
	products = posting.get("products") or []
	return {
		"id": accrual.get("id") or accrual.get("accrual_id") or accrual.get("operation_id"),
		"date": accrual.get("date") or accrual.get("accrual_date"),
		"type_id": accrual.get("type_id"),
		"category": accrual.get("accrued_category"),
		"posting_number": posting.get("posting_number") or posting.get("number"),
		"order_id": posting.get("order_id"),
		"skus": sorted(str(row.get("sku") or "") for row in products if isinstance(row, dict)),
	}


def _交易唯一键(store, accrual):
	explicit_id = accrual.get("id") or accrual.get("accrual_id") or accrual.get("operation_id")
	if explicit_id not in (None, ""):
		source = f"accrual|{store}|{explicit_id}"
	else:
		source = f"accrual|{store}|{json.dumps(_稳定标识片段(accrual), ensure_ascii=False, sort_keys=True)}"
	return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _查找已有财务交易(unique_key, store, operation_id):
	"""Find both current-key rows and rows created by an older key algorithm."""
	existing_name = frappe.db.get_value(STORAGE_DOCTYPE, {"transaction_unique_key": unique_key}, "name")
	if existing_name or not operation_id:
		return existing_name
	return frappe.db.get_value(
		STORAGE_DOCTYPE,
		{"store": store, "operation_id": str(operation_id).strip()},
		"name",
		order_by="fetched_at desc, modified desc",
	)


def _生成费用明细(accrual, type_map):
	fees = _遍历费用(
		{
			"item_fees": accrual.get("item_fees"),
			"non_item_fee": accrual.get("non_item_fee"),
			"container_fees": accrual.get("container_fees"),
			"posting_delivery": (accrual.get("posting") or {}).get("products"),
		}
	)
	result = []
	for row in fees:
		type_id = row.get("type_id")
		info = type_map.get(str(type_id), {})
		result.append(
			{
				**row,
				"type_name": info.get("name") or "",
				"type_description": info.get("description") or "",
				"normalized_amount": _费用金额(row),
			}
		)
	return result


def _金额拆分(accrual, type_map, fee_details):
	products = _提取商品明细(accrual)
	sale_amount = 0.0
	sale_commission = 0.0
	for product in products:
		commission = (product or {}).get("commission") or {}
		sale_amount += _安全数字(commission.get("seller_price"))
		sale_commission += _安全数字(commission.get("sale_commission"))
	parts = {
		"delivery_charge": 0.0,
		"return_delivery_charge": 0.0,
		"advertising_amount": 0.0,
		"penalty_amount": 0.0,
		"compensation_amount": 0.0,
		"discount_points_amount": 0.0,
		"other_amount": 0.0,
	}
	services_amount = 0.0
	for fee in fee_details:
		amount = _安全数字(fee.get("normalized_amount"))
		category = _财务分类(fee.get("type_id"), f"{fee.get('type_name')} {fee.get('type_description')}")
		text = f"{fee.get('type_name')} {fee.get('type_description')}".lower()
		if category == "Ozon配送服务":
			if any(word in text for word in ("return", "refund", "возврат")):
				parts["return_delivery_charge"] += amount
			else:
				parts["delivery_charge"] += amount
		elif category == "推广和广告":
			parts["advertising_amount"] += amount
		elif category == "Ozon代理佣金":
			sale_commission += amount
		elif category == "其他服务与罚款":
			parts["penalty_amount"] += amount
		elif category == "赔偿和赔偿返还":
			parts["compensation_amount"] += amount
		elif category == "折扣积分":
			parts["discount_points_amount"] += amount
		elif category in {"合作伙伴服务", "合作伙伴计划", "WHD服务"}:
			services_amount += amount
		else:
			parts["other_amount"] += amount
	total_amount_object = accrual.get("total_amount") or accrual.get("amount") or {}
	total_amount = _安全数字(total_amount_object)
	return {
		"currency_code": _币种(total_amount_object),
		"transaction_amount": total_amount,
		"accruals_for_sale": sale_amount,
		"sale_commission": sale_commission,
		"services_amount": services_amount,
		"net_amount": total_amount,
		**parts,
	}


def _追加旧版本(doc, new_hash, changed_fields):
	old_json = str(doc.get("raw_json") or "").strip()
	old_hash = str(doc.get("raw_json_hash") or "").strip()
	if not old_json or not old_hash or old_hash == new_hash:
		return False
	history = _安全历史数组(doc.get("raw_json_history"))
	try:
		old_payload = json.loads(old_json)
	except (TypeError, ValueError, json.JSONDecodeError):
		old_payload = old_json
	history.append(
		{
			"version": cint(doc.get("data_version") or 1),
			"archived_at": str(now_datetime()),
			"changed_fields": changed_fields,
			"raw_json_hash": old_hash,
			"raw_json": old_payload,
		}
	)
	doc.raw_json_history = _json文本(history)
	return True


def _发生变化的字段(doc, values):
	ignored = {
		"fetched_at", "sync_type", "sync_status", "last_error", "raw_json",
		"raw_json_hash", "data_changed", "changed_fields",
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


def 保存ozon财务交易(accrual, context, type_map, sync_type):
	"""Upsert one financial accrual using the new financial controller."""
	if not isinstance(accrual, dict):
		raise ValueError("Ozon 财务应计数据必须是 JSON 对象")
	store = str(context.get("店铺选项") or "").strip()
	posting = accrual.get("posting") or {}
	products = _提取商品明细(accrual)
	first = products[0] if products else {}
	sku = str(first.get("sku") or "").strip()
	offer_id = str(first.get("offer_id") or first.get("offer_code") or "").strip()
	product_id = str(first.get("product_id") or first.get("id") or sku or "").strip()
	item_code, item_name, item_image = _查找对应物料(store, sku, offer_id, product_id)
	fee_details = _生成费用明细(accrual, type_map)
	primary_fee = _主要费用信息(accrual, type_map, fee_details)
	type_text = f"{primary_fee['name']} {primary_fee['description']}"
	category = primary_fee["category"]
	amounts = _金额拆分(accrual, type_map, fee_details)
	unique_key = _交易唯一键(store, accrual)
	raw_text = _json文本(accrual)
	raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
	operation_id = accrual.get("id") or accrual.get("accrual_id") or accrual.get("operation_id") or unique_key[:24]
	posting_number = posting.get("posting_number") or posting.get("number") or accrual.get("unit_number")
	quantity = sum(_安全数字(row.get("quantity")) for row in products)
	values = {
		"transaction_unique_key": unique_key,
		"operation_id": _data(operation_id),
		"operation_type": _data(primary_fee["type_ids"]),
		"operation_type_name": _data(primary_fee["name"]),
		"store": store,
		"ozon_id": _data(context.get("ozon_id")),
		"company_id": _data(context.get("ozon_id")),
		"sync_type": sync_type,
		"transaction_category": category,
		"transaction_subcategory": _data(primary_fee["name"]),
		"transaction_direction": _收支方向(amounts["transaction_amount"]),
		"transaction_status": "已入账",
		"description": primary_fee["description"],
		"is_refund": cint(category == "销售和退货" and any(word in type_text.lower() for word in ("return", "refund", "возврат"))),
		"is_reversal": cint(any(word in type_text.lower() for word in ("reversal", "reverse", "сторно"))),
		"is_adjustment": cint(any(word in type_text.lower() for word in ("adjust", "correction", "коррект"))),
		"posting_number": _data(posting_number),
		"order_number": _data(posting.get("order_number")),
		"order_id": _data(posting.get("order_id")),
		"delivery_schema": _data(posting.get("delivery_schema") or posting.get("posting_schema")),
		"warehouse_id": _data(posting.get("warehouse_id")),
		"warehouse_name": _data(posting.get("warehouse_name") or posting.get("warehouse")),
		"sku": _data(sku),
		"offer_id": _data(offer_id),
		"product_id": _data(product_id),
		"product_name": first.get("name") or first.get("product_name"),
		"quantity": quantity,
		"corresponding_item": item_code,
		"corresponding_item_name": item_name,
		"corresponding_item_image": item_image,
		"item_count": len(products),
		**amounts,
		"operation_date": _ozon时间转系统时间(accrual.get("date") or accrual.get("accrual_date")),
		"order_date": _ozon时间转系统时间(posting.get("order_date") or posting.get("created_at")),
		"source_updated_at": _ozon时间转系统时间(accrual.get("updated_at")),
		"fetched_at": now_datetime(),
		"raw_json_hash": raw_hash,
		"sync_status": "成功",
		"last_error": None,
		"items_json": _json文本(products),
		"services_json": _json文本(fee_details),
		"raw_json": raw_text,
	}
	values = {key: value for key, value in values.items() if value not in (None, "")}
	existing_name = _查找已有财务交易(unique_key, store, operation_id)
	if existing_name:
		doc = frappe.get_doc(STORAGE_DOCTYPE, existing_name)
		changed_fields = _发生变化的字段(doc, values)
		changed = str(doc.get("raw_json_hash") or "") != raw_hash
		archived = _追加旧版本(doc, raw_hash, changed_fields) if changed else False
		values.update(
			{
				"data_changed": cint(changed),
				"changed_fields": ", ".join(changed_fields),
				"last_changed_at": now_datetime() if changed else doc.get("last_changed_at"),
				"data_version": cint(doc.get("data_version") or 1) + (1 if changed else 0),
			}
		)
		doc.update(values)
		doc.save(ignore_permissions=True)
		return {"created": 0, "updated": 1, "archived": cint(archived), "name": doc.name}
	values.update(
		{
			"first_fetched_at": now_datetime(),
			"last_changed_at": now_datetime(),
			"data_version": 1,
			"data_changed": 0,
			"changed_fields": "",
		}
	)
	doc = frappe.get_doc({"doctype": STORAGE_DOCTYPE, **values})
	doc.insert(ignore_permissions=True)
	return {"created": 1, "updated": 0, "archived": 0, "name": doc.name}


def _empty_summary():
	return {"created": 0, "updated": 0, "archived": 0, "records": 0, "pages": 0, "days": 0}


def _add_save_result(summary, result):
	summary["created"] += cint(result.get("created"))
	summary["updated"] += cint(result.get("updated"))
	summary["archived"] += cint(result.get("archived"))


def _fetch_accrual_day(store, context, target_date, sync_label, type_map):
	summary = _empty_summary()
	last_id = ""
	seen_ids = set()
	for _ in range(MAX_PAGES):
		payload = _request_json(
			store,
			"/v1/finance/accrual/by-day",
			{"date": getdate(target_date).isoformat(), "last_id": last_id},
			"daily financial accrual API",
		)
		accruals = payload.get("accruals") or (payload.get("result") or {}).get("accruals") or []
		if not isinstance(accruals, list):
			raise RuntimeError("Ozon daily financial accrual API accruals field is not a list")
		for accrual in accruals:
			_add_save_result(summary, 保存ozon财务交易(accrual, context, type_map, sync_label))
		summary["records"] += len(accruals)
		summary["pages"] += 1
		frappe.db.commit()
		next_id = str(
			payload.get("last_id") or (payload.get("result") or {}).get("last_id") or ""
		).strip()
		if not next_id or not accruals:
			break
		if next_id == last_id or next_id in seen_ids:
			raise RuntimeError(f"Ozon financial pagination cursor did not advance for {target_date}")
		seen_ids.add(next_id)
		last_id = next_id
	else:
		raise RuntimeError(f"Ozon financial API exceeded the maximum page count for {target_date}")
	return summary


def _sync_date_range(store, context, start_date, end_date, sync_label, type_map, daily_callback=None):
	summary = _empty_summary()
	cursor = getdate(start_date)
	end = getdate(end_date)
	while cursor <= end:
		day_summary = _fetch_accrual_day(store, context, cursor, sync_label, type_map)
		for key in ("created", "updated", "archived", "records", "pages"):
			summary[key] += day_summary[key]
		summary["days"] += 1
		if daily_callback:
			daily_callback(cursor, summary)
		cursor += timedelta(days=1)
	return summary


def _history_progress(config, completed_date):
	start = getdate(config.history_start_date)
	end = getdate(config.history_end_date)
	total_days = max((end - start).days + 1, 1)
	completed_days = max(min((getdate(completed_date) - start).days + 1, total_days), 0)
	return round(completed_days / total_days * 100, 2)


def _next_recheck(now, interval_days):
	return get_datetime(now).replace(hour=2, minute=59, second=0, microsecond=0) + timedelta(
		days=max(cint(interval_days), 1)
	)


def _first_recheck(now):
	now = get_datetime(now)
	candidate = now.replace(hour=2, minute=59, second=0, microsecond=0)
	return candidate if candidate > now else candidate + timedelta(days=1)


def _task_lock(name):
	return frappe.cache().lock(
		frappe.cache().make_key(f"fengjing:ozon-financial-config:{name}"),
		timeout=TASK_TIMEOUT,
		blocking_timeout=0,
	)


def _enqueue(name, sync_type):
	digest = hashlib.sha256(f"{name}|{sync_type}".encode("utf-8")).hexdigest()[:20]
	frappe.enqueue(
		execute_financial_sync,
		queue="long",
		timeout=TASK_TIMEOUT,
		enqueue_after_commit=True,
		job_id=f"ozon-financial-config-{digest}",
		deduplicate=True,
		configuration_name=name,
		sync_type=sync_type,
	)


@frappe.whitelist()
def start_history_sync(name):
	config = _get_configuration(name)
	if not cint(config.enabled):
		frappe.throw("Please enable financial sync first")
	if not config.history_start_date or not config.history_end_date:
		frappe.throw("Please set the history start and end dates first")
	get_store(config.ozon_store)
	values = {
		"history_status": "Waiting",
		"current_task_status": "Waiting",
		"current_execution_type": "history",
		"history_last_error": "",
		"last_error": "",
	}
	if config.history_status == "Completed":
		values.update(
			{
				"history_checkpoint": None,
				"history_progress": 0,
				"history_inserted_count": 0,
				"history_updated_count": 0,
				"history_archived_count": 0,
				"history_summary": "",
			}
		)
	_update_configuration(name, **values)
	_enqueue(name, "history")
	return {"status": "queued", "message": "Ozon financial history sync has been queued"}


@frappe.whitelist()
def start_incremental_sync(name):
	config = _get_configuration(name)
	if not cint(config.enabled):
		frappe.throw("Please enable financial sync first")
	get_store(config.ozon_store)
	_update_configuration(
		name,
		current_task_status="Waiting",
		current_execution_type="incremental",
		last_error="",
	)
	_enqueue(name, "incremental")
	return {"status": "queued", "message": "Ozon financial incremental sync has been queued"}


@frappe.whitelist()
def start_recheck_sync(name, days):
	days = cint(days)
	if days not in RECHECK_DAYS:
		frappe.throw("Unsupported Ozon financial recheck range")
	config = _get_configuration(name)
	if not cint(config.enabled):
		frappe.throw("Please enable financial sync first")
	get_store(config.ozon_store)
	_update_configuration(
		name,
		current_task_status="Waiting",
		current_execution_type=f"recheck_{days}",
		last_error="",
	)
	_enqueue(name, f"recheck_{days}")
	return {"status": "queued", "message": f"Ozon financial {days}-day recheck has been queued"}


def execute_financial_sync(configuration_name, sync_type):
	if sync_type not in SYNC_TYPE_LABELS:
		raise ValueError(f"Unsupported Ozon financial sync type: {sync_type}")
	lock = _task_lock(configuration_name)
	if not lock.acquire(blocking=False):
		return {"status": "busy", "message": "This Ozon financial configuration is already running"}

	try:
		_update_configuration(
			configuration_name,
			current_task_status="Running",
			current_execution_type=sync_type,
			current_task_started_at=now_datetime(),
			last_error="",
			**({"history_status": "Running", "history_last_error": ""} if sync_type == "history" else {}),
		)
		config = _get_configuration(configuration_name)
		if not cint(config.enabled):
			raise ValueError("Ozon financial synchronization is disabled")
		store = get_store(config.ozon_store)
		context = _storage_context(store)

		type_map = _get_accrual_types(store)
		if sync_type == "history":
			if not config.history_start_date or not config.history_end_date:
				raise ValueError("History start and end dates are required")
			start = getdate(config.history_start_date)
			end = getdate(config.history_end_date)
			cursor = getdate(config.history_checkpoint) + timedelta(days=1) if config.history_checkpoint else start
			available_end = min(end, getdate())
			if cursor > available_end:
				completed = cursor > end
				_update_configuration(
					configuration_name,
					history_status="Completed" if completed else "Waiting",
					history_progress=100 if completed else config.history_progress,
					current_task_status="Success" if completed else "Waiting",
					current_execution_type="",
					current_task_completed_at=now_datetime(),
				)
				return {"status": "complete" if completed else "waiting", "summary": _empty_summary()}

			summary = _empty_summary()
			summary["created"] = cint(config.history_inserted_count)
			summary["updated"] = cint(config.history_updated_count)
			summary["archived"] = cint(config.get("history_archived_count"))

			def daily_completed(completed_date, running_summary):
				_update_configuration(
					configuration_name,
					history_checkpoint=completed_date,
					history_progress=_history_progress(config, completed_date),
					history_inserted_count=summary["created"] + running_summary["created"],
					history_updated_count=summary["updated"] + running_summary["updated"],
					history_archived_count=summary["archived"] + running_summary["archived"],
					history_summary=json.dumps(running_summary, ensure_ascii=False),
				)

			run_summary = _sync_date_range(
				store,
				context,
				cursor,
				available_end,
				SYNC_TYPE_LABELS[sync_type],
				type_map,
				daily_callback=daily_completed,
			)
			for key in ("created", "updated", "archived", "records", "pages", "days"):
				summary[key] += run_summary[key]
			completed = available_end >= end
			text = json.dumps({"status": "Success", **summary}, ensure_ascii=False)
			_update_configuration(
				configuration_name,
				history_status="Completed" if completed else "Waiting",
				history_progress=100 if completed else _history_progress(config, available_end),
				history_inserted_count=summary["created"],
				history_updated_count=summary["updated"],
				history_archived_count=summary["archived"],
				history_summary=text,
				current_task_status="Success" if completed else "Waiting",
				current_execution_type="",
				current_task_completed_at=now_datetime(),
				last_run_result=text,
				history_last_error="",
				last_error="",
			)
			return {"status": "success", "summary": summary}

		today_date = getdate()
		if sync_type == "incremental":
			days = max(cint(config.incremental_lookback_days), 1)
		else:
			days = int(sync_type.rsplit("_", 1)[1])
		start_date = today_date - timedelta(days=days - 1)
		summary = _sync_date_range(
			store,
			context,
			start_date,
			today_date,
			SYNC_TYPE_LABELS[sync_type],
			type_map,
		)
		finished_at = now_datetime()
		text = json.dumps({"status": "Success", **summary}, ensure_ascii=False)
		values = {
			"current_task_status": "Success",
			"current_execution_type": "",
			"current_task_completed_at": finished_at,
			"last_run_result": text,
			"last_error": "",
		}
		if sync_type == "incremental":
			values.update(
				{
					"incremental_checkpoint": today_date,
					"incremental_last_at": finished_at,
					"incremental_next_at": finished_at
					+ timedelta(minutes=max(cint(config.incremental_interval_minutes), 1)),
					"incremental_summary": text,
				}
			)
		else:
			days = int(sync_type.rsplit("_", 1)[1])
			values.update(
				{
					f"finance_recheck_{days}_last_at": finished_at,
					f"finance_recheck_{days}_next_at": _next_recheck(
						finished_at, config.get(f"finance_recheck_{days}_interval_days")
					),
				}
			)
		_update_configuration(configuration_name, **values)
		return {"status": "success", "summary": summary}
	except Exception as exc:
		now = now_datetime()
		values = {
			"current_task_status": "Failed",
			"current_execution_type": "",
			"current_task_completed_at": now,
			"last_run_result": f"Failed: {str(exc)[:1800]}",
			"last_error": str(exc)[:2000],
		}
		if sync_type == "history":
			values.update({"history_status": "Failed", "history_last_error": str(exc)[:2000]})
		elif sync_type == "incremental":
			values.update(
				{
					"incremental_last_at": now,
					"incremental_next_at": now + timedelta(minutes=15),
					"incremental_summary": f"Failed: {str(exc)[:1800]}",
				}
			)
		else:
			days = int(sync_type.rsplit("_", 1)[1])
			values[f"finance_recheck_{days}_next_at"] = now + timedelta(minutes=30)
		_update_configuration(configuration_name, **values)
		frappe.logger("ozon_finance_v2", allow_site=True).exception(
			"Ozon financial sync failed: configuration=%s type=%s", configuration_name, sync_type
		)
		raise
	finally:
		try:
			lock.release()
		except Exception:
			pass


def run_scheduled_financial_sync():
	"""Queue due jobs after this function is explicitly connected to hooks later."""
	now = now_datetime()
	for name in frappe.get_all(DOCTYPE, filters={"enabled": 1}, pluck="name"):
		try:
			config = _get_configuration(name)
			get_store(config.ozon_store)
			if config.current_task_status == "Waiting":
				waiting_type = str(config.current_execution_type or "").strip()
				if waiting_type in SYNC_TYPE_LABELS:
					_enqueue(name, waiting_type)
					continue
				_update_configuration(name, current_task_status="Idle", current_execution_type="")
				config = _get_configuration(name)
			if config.current_task_status == "Running":
				started = get_datetime(config.current_task_started_at) if config.current_task_started_at else None
				if not started or (now - started).total_seconds() < TASK_TIMEOUT:
					continue
				_update_configuration(
					name,
					current_task_status="Failed",
					current_execution_type="",
					current_task_completed_at=now,
					last_error="Previous task exceeded twelve hours and was released by the scheduler",
				)
				config = _get_configuration(name)

			if (
				config.history_status in {"Waiting", "Running"}
				and config.history_start_date
				and config.history_end_date
			):
				cursor = (
					getdate(config.history_checkpoint) + timedelta(days=1)
					if config.history_checkpoint
					else getdate(config.history_start_date)
				)
				configured_end = getdate(config.history_end_date)
				if cursor <= min(configured_end, getdate()) or cursor > configured_end:
					_enqueue(name, "history")
					continue

			if (
				not config.incremental_next_at or get_datetime(config.incremental_next_at) <= now
			):
				_enqueue(name, "incremental")
				continue

			initialized = {}
			for days in RECHECK_DAYS:
				if cint(config.get(f"finance_enable_recheck_{days}")) and not config.get(
					f"finance_recheck_{days}_next_at"
				):
					initialized[f"finance_recheck_{days}_next_at"] = _first_recheck(now)
			if initialized:
				_update_configuration(name, **initialized)
				continue

			queued = False
			for days in RECHECK_DAYS:
				if not cint(config.get(f"finance_enable_recheck_{days}")):
					continue
				next_at = config.get(f"finance_recheck_{days}_next_at")
				if next_at and get_datetime(next_at) <= now:
					_enqueue(name, f"recheck_{days}")
					queued = True
					break
			if queued:
				continue

		except Exception:
			frappe.logger("ozon_finance_v2", allow_site=True).exception(
				"Ozon financial scheduler failed: configuration=%s", name
			)


启动Ozon历史财务同步 = start_history_sync
启动Ozon最新财务同步 = start_incremental_sync
定时执行Ozon财务同步 = run_scheduled_financial_sync

