# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import hashlib
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import frappe
import requests
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, get_system_timezone, getdate, now_datetime


class OzonFinancialStorage(Document):
	pass


配置主表 = "Fengjing - Product Corresponding Platform - Configuration"
配置子表 = "Ozon Store API Sub-table"
存储单据 = "Ozon Financial Storage"
平台对应表 = "Fengjing - Product Corresponding Platform - Main Table"

应计接口 = "https://api-seller.ozon.ru/v1/finance/accrual/by-day"
应计类型接口 = "https://api-seller.ozon.ru/v1/finance/accrual/types"
结算报告接口 = "https://api-seller.ozon.ru/v1/finance/cash-flow-statement/list"

最大分页数 = 10000
任务最长秒数 = 12 * 60 * 60


def _取得配置行(配置行名称):
	主表 = frappe.get_single(配置主表)
	配置行 = next(
		(row for row in (主表.get("table_wckx") or []) if row.name == 配置行名称),
		None,
	)
	if not 配置行:
		raise ValueError(f"找不到 Ozon 店铺配置行：{配置行名称}")
	return 主表, 配置行


def _更新配置状态(配置行名称, **字段):
	有效字段 = set(frappe.get_meta(配置子表).get_valid_columns())
	有效值 = {key: value for key, value in 字段.items() if key in 有效字段}
	if 有效值:
		frappe.db.set_value(配置子表, 配置行名称, 有效值, update_modified=False)
		frappe.db.commit()


def _任务锁(配置行名称):
	cache = frappe.cache()
	return cache.lock(
		cache.make_key(f"fengjing:ozon-finance:{配置行名称}"),
		timeout=任务最长秒数,
		blocking_timeout=任务最长秒数,
	)


def _错误摘要(响应):
	try:
		数据 = 响应.json()
		内容 = 数据.get("message") or 数据.get("error_description") or 数据.get("error") or 数据
		if isinstance(内容, (dict, list)):
			内容 = json.dumps(内容, ensure_ascii=False)
		return str(内容)[:1800]
	except (ValueError, TypeError, AttributeError):
		return str(getattr(响应, "text", "") or "无响应内容")[:1800]


def _请求json(配置行, 地址, 请求体, 接口名称):
	headers = {
		"Client-Id": str(配置行.get("ozon_id") or "").strip(),
		"Api-Key": str(配置行.get("ozon_秘钥") or "").strip(),
		"Content-Type": "application/json",
	}
	if not headers["Client-Id"] or not headers["Api-Key"]:
		raise ValueError("Ozon 财务同步缺少 Ozon ID 或 Ozon 秘钥")

	最后响应 = None
	for 序号, 等待秒数 in enumerate((2, 5, 15, 30, 60, 120)):
		try:
			响应 = requests.post(地址, headers=headers, json=请求体, timeout=(10, 90))
		except requests.RequestException:
			if 序号 == 5:
				raise
			time.sleep(等待秒数)
			continue
		最后响应 = 响应
		if 响应.status_code == 429 and 序号 < 5:
			try:
				等待秒数 = max(float(响应.headers.get("Retry-After")), 等待秒数)
			except (TypeError, ValueError):
				pass
			time.sleep(min(等待秒数, 300))
			continue
		if 响应.status_code in {500, 502, 503, 504} and 序号 < 5:
			time.sleep(等待秒数)
			continue
		if 响应.status_code != 200:
			raise RuntimeError(
				f"Ozon {接口名称}失败（HTTP {响应.status_code}）：{_错误摘要(响应)}"
			)
		try:
			数据 = 响应.json()
		except ValueError as exc:
			raise RuntimeError(f"Ozon {接口名称}返回的不是有效 JSON") from exc
		if not isinstance(数据, dict):
			raise RuntimeError(f"Ozon {接口名称}返回结构异常")
		return 数据

	状态码 = 最后响应.status_code if 最后响应 is not None else "无响应"
	raise RuntimeError(f"Ozon {接口名称}重试后仍然失败（HTTP {状态码}）")


def _安全数字(value):
	if isinstance(value, dict):
		value = value.get("amount", value.get("value"))
	try:
		结果 = float(value)
		return 结果 if math.isfinite(结果) else 0.0
	except (TypeError, ValueError):
		return 0.0


def _币种(value, 默认="RUB"):
	if isinstance(value, dict):
		return str(value.get("currency") or value.get("currency_code") or 默认).strip() or 默认
	return 默认


def _data(value, length=140):
	"""Keep API text inside Frappe Data/Link field limits."""
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


def _utc字符串(value):
	if not value:
		return None
	parsed = get_datetime(value)
	if parsed.tzinfo is None:
		parsed = parsed.replace(tzinfo=ZoneInfo(get_system_timezone()))
	return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json文本(value):
	return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def _安全历史数组(value):
	try:
		结果 = json.loads(value or "[]")
		return 结果 if isinstance(结果, list) else []
	except (TypeError, ValueError, json.JSONDecodeError):
		return []


def _取得应计类型(配置行):
	数据 = _请求json(配置行, 应计类型接口, {}, "财务应计类型接口")
	类型列表 = 数据.get("accrual_types") or (数据.get("result") or {}).get("accrual_types") or []
	结果 = {}
	for row in 类型列表:
		if not isinstance(row, dict):
			continue
		编号 = row.get("id", row.get("type_id"))
		if 编号 is not None:
			结果[str(编号)] = {
				"name": row.get("name") or "",
				"description": row.get("description") or row.get("name") or "",
			}
	return 结果


def _遍历费用(node, path=""):
	结果 = []
	if isinstance(node, dict):
		if "type_id" in node and ("accrued" in node or "amount" in node or "price" in node):
			结果.append({"path": path, **node})
		for key, value in node.items():
			结果.extend(_遍历费用(value, f"{path}.{key}" if path else key))
	elif isinstance(node, list):
		for index, value in enumerate(node):
			结果.extend(_遍历费用(value, f"{path}[{index}]"))
	return 结果


def _费用金额(row):
	for key in ("accrued", "amount", "price", "total_amount"):
		if key in row:
			return _安全数字(row.get(key))
	return 0.0


def _类型文本(type_id, 类型表):
	类型 = 类型表.get(str(type_id), {})
	return " ".join(str(v or "") for v in (类型.get("name"), 类型.get("description"))).strip()


def _财务分类(type_id, 类型文本, accrued_category=""):
	text = f"{类型文本} {accrued_category}".lower()
	规则 = (
		("推广和广告", ("advert", "promo", "promotion", "payperclick", "реклам", "продвиж")),
		("Ozon代理佣金", ("commission", "комисс")),
		("其他服务与罚款", ("penalty", "fine", "штраф")),
		("赔偿和赔偿返还", ("compensation", "компенсац")),
		("折扣积分", ("point", "cashback", "балл", "кешбэк")),
		("WHD服务", ("whd",)),
		("借贷和托收信贷", ("loan", "credit", "collection", "кредит", "заём")),
		("销售和退货", ("return", "refund", "sale", "order", "возврат", "продаж")),
		("Ozon配送服务", ("delivery", "logistic", "lastmile", "достав", "логист")),
		("合作伙伴计划", ("partner program", "партнёрск", "партнерск")),
		("合作伙伴服务", ("service", "placement", "storage", "packing", "услуг", "хранен", "упаков")),
	)
	for 分类, 关键词 in 规则:
		if any(word in text for word in 关键词):
			return 分类
	if str(type_id) in {"10", "25"}:
		return "赔偿和赔偿返还"
	if str(type_id) in {"29", "32", "98"}:
		return "Ozon配送服务"
	if str(type_id) in {"41", "54"}:
		return "推广和广告"
	if str(type_id) in {"45", "59"}:
		return "销售和退货"
	if "posting" in text:
		return "销售和退货"
	return "其他应计项目"


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
		item_code = frappe.db.get_value(平台对应表, filters, "物料id")
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
	显式id = accrual.get("id") or accrual.get("accrual_id") or accrual.get("operation_id")
	if 显式id not in (None, ""):
		源 = f"accrual|{store}|{显式id}"
	else:
		源 = f"accrual|{store}|{json.dumps(_稳定标识片段(accrual), ensure_ascii=False, sort_keys=True)}"
	return hashlib.sha256(源.encode("utf-8")).hexdigest()


def _生成费用明细(accrual, 类型表):
	费用 = _遍历费用(
		{
			"item_fees": accrual.get("item_fees"),
			"non_item_fee": accrual.get("non_item_fee"),
			"container_fees": accrual.get("container_fees"),
			"posting_delivery": (accrual.get("posting") or {}).get("products"),
		}
	)
	结果 = []
	for row in 费用:
		type_id = row.get("type_id")
		类型 = 类型表.get(str(type_id), {})
		结果.append(
			{
				**row,
				"type_name": 类型.get("name") or "",
				"type_description": 类型.get("description") or "",
				"normalized_amount": _费用金额(row),
			}
		)
	return 结果


def _金额拆分(accrual, 类型表, 费用明细):
	posting = accrual.get("posting") or {}
	products = posting.get("products") or []
	销售金额 = 0.0
	销售佣金 = 0.0
	for product in products:
		commission = (product or {}).get("commission") or {}
		销售金额 += _安全数字(commission.get("seller_price"))
		销售佣金 += _安全数字(commission.get("sale_commission"))

	拆分 = {
		"delivery_charge": 0.0,
		"return_delivery_charge": 0.0,
		"advertising_amount": 0.0,
		"penalty_amount": 0.0,
		"compensation_amount": 0.0,
		"discount_points_amount": 0.0,
		"other_amount": 0.0,
	}
	服务合计 = 0.0
	for fee in 费用明细:
		amount = _安全数字(fee.get("normalized_amount"))
		服务合计 += amount
		category = _财务分类(fee.get("type_id"), f"{fee.get('type_name')} {fee.get('type_description')}")
		text = f"{fee.get('type_name')} {fee.get('type_description')}".lower()
		if category == "Ozon配送服务":
			if any(word in text for word in ("return", "refund", "возврат")):
				拆分["return_delivery_charge"] += amount
			else:
				拆分["delivery_charge"] += amount
		elif category == "推广和广告":
			拆分["advertising_amount"] += amount
		elif category == "其他服务与罚款":
			拆分["penalty_amount"] += amount
		elif category == "赔偿和赔偿返还":
			拆分["compensation_amount"] += amount
		elif category == "折扣积分":
			拆分["discount_points_amount"] += amount
		else:
			拆分["other_amount"] += amount
	总额对象 = accrual.get("total_amount") or accrual.get("amount") or {}
	总额 = _安全数字(总额对象)
	return {
		"currency_code": _币种(总额对象),
		"transaction_amount": 总额,
		"accruals_for_sale": 销售金额,
		"sale_commission": 销售佣金,
		"services_amount": 服务合计,
		"net_amount": 总额,
		**拆分,
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
	忽略 = {"fetched_at", "sync_type", "sync_status", "last_error", "raw_json", "raw_json_hash", "data_changed", "changed_fields"}
	变化 = []
	for key, value in values.items():
		if key in 忽略:
			continue
		旧值 = doc.get(key)
		if isinstance(value, float):
			if abs(flt(旧值) - value) > 0.000001:
				变化.append(key)
		elif str(旧值 or "") != str(value or ""):
			变化.append(key)
	return 变化


def 保存ozon财务交易(accrual, 配置行, 类型表, 同步类型):
	if not isinstance(accrual, dict):
		raise ValueError("Ozon 财务应计数据必须是 JSON 对象")
	store = str(配置行.get("店铺选项") or "").strip()
	posting = accrual.get("posting") or {}
	products = posting.get("products") or []
	products = [row for row in products if isinstance(row, dict)]
	first = products[0] if products else {}
	sku = str(first.get("sku") or "").strip()
	offer_id = str(first.get("offer_id") or first.get("offer_code") or "").strip()
	product_id = str(first.get("product_id") or first.get("id") or sku or "").strip()
	item_code, item_name, item_image = _查找对应物料(store, sku, offer_id, product_id)

	type_id = accrual.get("type_id")
	type_info = 类型表.get(str(type_id), {})
	type_text = _类型文本(type_id, 类型表)
	category = _财务分类(type_id, type_text, accrual.get("accrued_category"))
	费用明细 = _生成费用明细(accrual, 类型表)
	金额 = _金额拆分(accrual, 类型表, 费用明细)
	unique_key = _交易唯一键(store, accrual)
	raw_text = _json文本(accrual)
	raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
	operation_id = accrual.get("id") or accrual.get("accrual_id") or accrual.get("operation_id") or unique_key[:24]
	posting_number = posting.get("posting_number") or posting.get("number")
	quantity = sum(_安全数字(row.get("quantity")) for row in products)

	values = {
		"transaction_unique_key": unique_key,
		"operation_id": _data(operation_id),
		"operation_type": _data(type_id or accrual.get("accrued_category")),
		"operation_type_name": _data(type_info.get("name") or type_text or accrual.get("accrued_category") or "未知应计"),
		"store": store,
		"ozon_id": _data(配置行.get("ozon_id")),
		"company_id": _data(配置行.get("ozon_id")),
		"sync_type": 同步类型,
		"transaction_category": category,
		"transaction_subcategory": _data(type_info.get("name") or type_info.get("description")),
		"transaction_direction": _收支方向(金额["transaction_amount"]),
		"transaction_status": "已入账",
		"description": type_info.get("description") or type_info.get("name") or str(accrual.get("accrued_category") or ""),
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
		**金额,
		"operation_date": _ozon时间转系统时间(accrual.get("date") or accrual.get("accrual_date")),
		"order_date": _ozon时间转系统时间(posting.get("order_date") or posting.get("created_at")),
		"source_updated_at": _ozon时间转系统时间(accrual.get("updated_at")),
		"fetched_at": now_datetime(),
		"raw_json_hash": raw_hash,
		"sync_status": "成功",
		"last_error": None,
		"items_json": _json文本(products),
		"services_json": _json文本(费用明细),
		"raw_json": raw_text,
	}
	values = {key: value for key, value in values.items() if value not in (None, "")}
	existing_name = frappe.db.get_value(存储单据, {"transaction_unique_key": unique_key}, "name")
	if existing_name:
		doc = frappe.get_doc(存储单据, existing_name)
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
	doc = frappe.get_doc({"doctype": 存储单据, **values})
	doc.insert(ignore_permissions=True)
	return {"created": 1, "updated": 0, "archived": 0, "name": doc.name}


def _累计(汇总, 保存结果):
	汇总["新建"] += cint(保存结果.get("created"))
	汇总["更新"] += cint(保存结果.get("updated"))
	汇总["历史归档"] += cint(保存结果.get("archived"))


def _抓取单日应计(配置行, 日期, 同步类型, 类型表):
	汇总 = {"新建": 0, "更新": 0, "历史归档": 0, "记录": 0, "分页": 0}
	last_id = ""
	seen = set()
	for _ in range(最大分页数):
		数据 = _请求json(
			配置行,
			应计接口,
			{"date": getdate(日期).isoformat(), "last_id": last_id},
			"每日财务应计接口",
		)
		accruals = 数据.get("accruals") or (数据.get("result") or {}).get("accruals") or []
		if not isinstance(accruals, list):
			raise RuntimeError("Ozon 每日财务应计接口 accruals 结构异常")
		for accrual in accruals:
			_累计(汇总, 保存ozon财务交易(accrual, 配置行, 类型表, 同步类型))
		汇总["记录"] += len(accruals)
		汇总["分页"] += 1
		frappe.db.commit()
		next_id = str(数据.get("last_id") or (数据.get("result") or {}).get("last_id") or "").strip()
		if not next_id or not accruals:
			break
		if next_id == last_id or next_id in seen:
			raise RuntimeError(f"Ozon 财务应计接口在 {日期} 的分页游标没有推进")
		seen.add(next_id)
		last_id = next_id
	else:
		raise RuntimeError(f"Ozon 财务应计接口在 {日期} 超过最大分页数")
	return 汇总


def _同步日期范围(配置行, 开始日期, 结束日期, 同步类型, 类型表=None, 每日回调=None):
	类型表 = 类型表 if 类型表 is not None else _取得应计类型(配置行)
	汇总 = {"新建": 0, "更新": 0, "历史归档": 0, "记录": 0, "分页": 0, "天数": 0}
	当前 = getdate(开始日期)
	结束 = getdate(结束日期)
	while 当前 <= 结束:
		本日 = _抓取单日应计(配置行, 当前, 同步类型, 类型表)
		for key in ("新建", "更新", "历史归档", "记录", "分页"):
			汇总[key] += 本日[key]
		汇总["天数"] += 1
		if 每日回调:
			每日回调(当前, 汇总)
		当前 += timedelta(days=1)
	return 汇总


def _历史进度(配置行, 完成日期):
	开始 = getdate(配置行.get("finance_history_start_date"))
	结束 = getdate(配置行.get("finance_history_end_date"))
	总天数 = max((结束 - 开始).days + 1, 1)
	完成天数 = max(min((getdate(完成日期) - 开始).days + 1, 总天数), 0)
	return round(完成天数 / 总天数 * 100, 2)


def _固定核对下次时间(当前时间, 间隔天数):
	return get_datetime(当前时间).replace(hour=2, minute=59, second=0, microsecond=0) + timedelta(days=max(cint(间隔天数), 1))


def _保存结算报告(配置行, details):
	if not isinstance(details, dict):
		return {"created": 0, "updated": 0, "archived": 0}
	store = str(配置行.get("店铺选项") or "").strip()
	period = details.get("period") or {}
	period_id = period.get("id")
	period_begin = period.get("begin")
	period_end = period.get("end")
	源 = f"statement|{store}|{period_id or period_begin}|{period_end}"
	unique_key = hashlib.sha256(源.encode("utf-8")).hexdigest()
	raw_text = _json文本(details)
	raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
	payments = details.get("payments") or {}
	delivery = details.get("delivery") or {}
	returns = details.get("return") or {}
	services = details.get("services") or {}
	others = details.get("others") or {}
	begin_balance = _安全数字(details.get("begin_balance_amount"))
	end_balance = _安全数字(details.get("end_balance_amount"))
	values = {
		"transaction_unique_key": unique_key,
		"operation_id": f"statement-{period_id or unique_key[:16]}",
		"operation_type": "cash_flow_statement",
		"operation_type_name": "Ozon半月结算报告",
		"store": store,
		"ozon_id": str(配置行.get("ozon_id") or "").strip(),
		"company_id": str(配置行.get("ozon_id") or "").strip(),
		"sync_type": "半月结算报告",
		"transaction_category": "结算与提现",
		"transaction_subcategory": "现金流结算报告",
		"transaction_direction": _收支方向(_安全数字(payments.get("payment"))),
		"transaction_status": "已生成报告",
		"description": f"Ozon结算周期：{period_begin or '未知'} 至 {period_end or '未知'}",
		"statement_id": str(period_id or ""),
		"statement_number": str(period_id or ""),
		"currency_code": payments.get("currency_code") or "RUB",
		"transaction_amount": _安全数字(payments.get("payment")),
		"delivery_charge": _安全数字(delivery.get("total")),
		"return_delivery_charge": _安全数字(returns.get("total")),
		"services_amount": _安全数字(services.get("total")),
		"other_amount": _安全数字(others.get("total")),
		"net_amount": end_balance - begin_balance,
		"operation_date": _ozon时间转系统时间(period_end or period_begin),
		"payment_date": _ozon时间转系统时间(details.get("payment_date")),
		"settlement_period_start": _ozon时间转系统时间(period_begin),
		"settlement_period_end": _ozon时间转系统时间(period_end),
		"fetched_at": now_datetime(),
		"raw_json_hash": raw_hash,
		"sync_status": "成功",
		"services_json": _json文本({"delivery": delivery, "return": returns, "services": services, "others": others, "rfbs": details.get("rfbs")}),
		"raw_json": raw_text,
	}
	values = {key: value for key, value in values.items() if value not in (None, "")}
	existing_name = frappe.db.get_value(存储单据, {"transaction_unique_key": unique_key}, "name")
	if existing_name:
		doc = frappe.get_doc(存储单据, existing_name)
		changed_fields = _发生变化的字段(doc, values)
		changed = str(doc.get("raw_json_hash") or "") != raw_hash
		archived = _追加旧版本(doc, raw_hash, changed_fields) if changed else False
		values.update({
			"data_changed": cint(changed),
			"changed_fields": ", ".join(changed_fields),
			"last_changed_at": now_datetime() if changed else doc.get("last_changed_at"),
			"data_version": cint(doc.get("data_version") or 1) + (1 if changed else 0),
		})
		doc.update(values)
		doc.save(ignore_permissions=True)
		return {"created": 0, "updated": 1, "archived": cint(archived)}
	values.update({"first_fetched_at": now_datetime(), "last_changed_at": now_datetime(), "data_version": 1})
	frappe.get_doc({"doctype": 存储单据, **values}).insert(ignore_permissions=True)
	return {"created": 1, "updated": 0, "archived": 0}


def _同步结算报告(配置行):
	回看期数 = max(cint(配置行.get("finance_statement_lookback_periods") or 2), 1)
	结束 = now_datetime()
	开始 = 结束 - timedelta(days=回看期数 * 16 + 2)
	汇总 = {"新建": 0, "更新": 0, "历史归档": 0, "报告": 0, "分页": 0}
	page = 1
	while page <= 最大分页数:
		数据 = _请求json(
			配置行,
			结算报告接口,
			{"page": page, "page_size": 100, "date": {"from": _utc字符串(开始), "to": _utc字符串(结束)}, "with_details": True},
			"半月结算报告接口",
		)
		result = 数据.get("result") or {}
		details = result.get("details") or []
		if isinstance(details, dict):
			details = [details]
		for row in details:
			_累计(汇总, _保存结算报告(配置行, row))
		汇总["报告"] += len(details)
		汇总["分页"] += 1
		frappe.db.commit()
		page_count = cint(result.get("page_count"))
		if not details or page_count <= page:
			break
		page += 1
	else:
		raise RuntimeError("Ozon 半月结算报告超过最大分页数")
	return 汇总


@frappe.whitelist()
def 启动ozon历史财务同步(配置行名称):
	_, 行 = _取得配置行(配置行名称)
	if not cint(行.get("enable_finance_sync")):
		frappe.throw("请先开启财务同步总开关并保存")
	if not 行.get("finance_history_start_date") or not 行.get("finance_history_end_date"):
		frappe.throw("请先填写历史财务同步开始日期和结束日期")
	if getdate(行.get("finance_history_start_date")) < date(2022, 1, 1):
		frappe.throw("Ozon每日财务应计接口最早支持从2022-01-01开始同步")
	if getdate(行.get("finance_history_start_date")) > getdate(行.get("finance_history_end_date")):
		frappe.throw("历史财务同步开始日期不能晚于结束日期")
	_更新配置状态(
		配置行名称,
		finance_history_status="等待执行",
		finance_current_task_status="等待执行",
		finance_current_execution_type="历史财务",
		finance_history_last_error="",
		finance_last_error="",
	)
	_任务入队(配置行名称, "历史财务")
	return {"status": "queued", "message": "Ozon历史财务同步已进入后台队列"}


@frappe.whitelist()
def 启动ozon最新财务同步(配置行名称):
	_, 行 = _取得配置行(配置行名称)
	if not cint(行.get("enable_finance_sync")):
		frappe.throw("请先开启财务同步总开关并保存")
	_更新配置状态(
		配置行名称,
		finance_current_task_status="等待执行",
		finance_current_execution_type="最新财务增量",
		finance_last_error="",
	)
	_任务入队(配置行名称, "最新财务增量")
	return {"status": "queued", "message": "Ozon最新财务同步已进入后台队列"}


@frappe.whitelist()
def 启动ozon结算报告同步(配置行名称):
	_, 行 = _取得配置行(配置行名称)
	if not cint(行.get("enable_finance_sync")):
		frappe.throw("请先开启财务同步总开关并保存")
	if not cint(行.get("enable_finance_statement_sync")):
		frappe.throw("请先开启结算报告同步并保存")
	_更新配置状态(
		配置行名称,
		finance_current_task_status="等待执行",
		finance_current_execution_type="半月结算报告",
		finance_last_error="",
	)
	_任务入队(配置行名称, "半月结算报告")
	return {"status": "queued", "message": "Ozon结算报告同步已进入后台队列"}


def 执行ozon财务同步任务(配置行名称, 同步类型):
	lock = _任务锁(配置行名称)
	if not lock.acquire(blocking=True):
		raise RuntimeError("等待同一店铺的其他 Ozon 财务任务超时")
	try:
		_更新配置状态(
			配置行名称,
			finance_current_task_status="运行中",
			finance_current_execution_type=同步类型,
			finance_current_task_started_at=now_datetime(),
			finance_last_error="",
		)
		_, 行 = _取得配置行(配置行名称)
		if not cint(行.get("enable_finance_sync")):
			raise ValueError("Ozon财务同步总开关已经关闭")

		if 同步类型 == "历史财务":
			_更新配置状态(配置行名称, finance_history_status="运行中")
			开始 = getdate(行.get("finance_history_start_date"))
			结束 = getdate(行.get("finance_history_end_date"))
			游标 = getdate(行.get("finance_history_completed_to")) + timedelta(days=1) if 行.get("finance_history_completed_to") else 开始
			可用结束 = min(结束, getdate())
			if 游标 > 可用结束:
				完成 = 游标 > 结束
				_更新配置状态(
					配置行名称,
					finance_history_status="已完成" if 完成 else "已暂停",
					finance_history_progress=100 if 完成 else 行.get("finance_history_progress"),
					finance_current_task_status="成功",
					finance_current_execution_type="",
				)
				return {"status": "complete" if 完成 else "waiting"}

			def 每日完成(日期, 汇总):
				_更新配置状态(
					配置行名称,
					finance_history_completed_to=日期,
					finance_history_progress=_历史进度(行, 日期),
					finance_history_inserted_count=汇总["新建"],
					finance_history_updated_count=汇总["更新"],
				)

			汇总 = _同步日期范围(行, 游标, 可用结束, 同步类型, 每日回调=每日完成)
			完成 = 可用结束 >= 结束
			_更新配置状态(
				配置行名称,
				finance_history_status="已完成" if 完成 else "已暂停",
				finance_history_progress=100 if 完成 else _历史进度(行, 可用结束),
				finance_current_task_status="成功",
				finance_current_execution_type="",
				finance_history_last_error="",
			)
			return {"status": "success", "summary": 汇总}

		if 同步类型 == "半月结算报告":
			汇总 = _同步结算报告(行)
			当前 = now_datetime()
			_更新配置状态(
				配置行名称,
				finance_current_task_status="成功",
				finance_current_execution_type="",
				finance_statement_last_sync_at=当前,
				finance_statement_next_sync_at=frappe.utils.add_to_date(当前, days=1),
				finance_statement_last_result=_json文本({"状态": "成功", **汇总}),
				finance_last_error="",
			)
			return {"status": "success", "summary": 汇总}

		今天 = getdate()
		if 同步类型 == "最新财务增量":
			天数 = max(cint(行.get("finance_latest_lookback_days") or 2), 1)
			开始 = 今天 - timedelta(days=天数 - 1)
		else:
			天数 = {"7天财务核对": 7, "14天财务核对": 14, "30天财务核对": 30, "90天财务核对": 90, "180天财务核对": 180}[同步类型]
			开始 = 今天 - timedelta(days=天数 - 1)
		汇总 = _同步日期范围(行, 开始, 今天, 同步类型)
		当前 = now_datetime()
		更新字段 = {
			"finance_current_task_status": "成功",
			"finance_current_execution_type": "",
			"finance_last_error": "",
		}
		if 同步类型 == "最新财务增量":
			间隔 = max(cint(行.get("finance_latest_interval_minutes") or 15), 1)
			更新字段.update({
				"finance_auto_checkpoint": 今天,
				"finance_last_auto_sync_at": 当前,
				"finance_next_auto_sync_at": frappe.utils.add_to_date(当前, minutes=间隔),
				"finance_last_auto_sync_result": _json文本({"状态": "成功", **汇总}),
			})
		else:
			前缀 = 同步类型.replace("财务核对", "")
			间隔天数 = max(cint(行.get(f"finance_recheck_{前缀}_interval_days") or 1), 1)
			更新字段[f"finance_recheck_{前缀}_last_at"] = 当前
			更新字段[f"finance_recheck_{前缀}_next_at"] = _固定核对下次时间(当前, 间隔天数)
		_更新配置状态(配置行名称, **更新字段)
		return {"status": "success", "summary": 汇总}
	except Exception as exc:
		错误 = str(exc)[:2000]
		更新字段 = {
			"finance_current_task_status": "失败",
			"finance_current_execution_type": "",
			"finance_last_error": 错误,
		}
		if 同步类型 == "历史财务":
			更新字段.update({"finance_history_status": "失败", "finance_history_last_error": 错误})
		elif 同步类型 == "最新财务增量":
			更新字段.update({
				"finance_last_auto_sync_at": now_datetime(),
				"finance_next_auto_sync_at": frappe.utils.add_to_date(now_datetime(), minutes=15),
				"finance_last_auto_sync_result": f"失败：{错误[:1800]}",
			})
		elif 同步类型 == "半月结算报告":
			更新字段.update({
				"finance_statement_last_sync_at": now_datetime(),
				"finance_statement_next_sync_at": frappe.utils.add_to_date(now_datetime(), minutes=30),
				"finance_statement_last_result": f"失败：{错误[:1800]}",
			})
		else:
			前缀 = 同步类型.replace("财务核对", "")
			更新字段[f"finance_recheck_{前缀}_next_at"] = frappe.utils.add_to_date(now_datetime(), minutes=30)
		_更新配置状态(配置行名称, **更新字段)
		frappe.logger("ozon_finance", allow_site=True).exception(
			"Ozon财务同步失败：配置行=%s，类型=%s", 配置行名称, 同步类型
		)
		raise
	finally:
		try:
			lock.release()
		except Exception:
			pass


def _任务入队(配置行名称, 同步类型):
	job_suffix = hashlib.sha256(f"{配置行名称}|{同步类型}".encode("utf-8")).hexdigest()[:20]
	frappe.enqueue(
		执行ozon财务同步任务,
		queue="long",
		timeout=任务最长秒数,
		enqueue_after_commit=True,
		job_id=f"ozon-finance-{job_suffix}",
		deduplicate=True,
		配置行名称=配置行名称,
		同步类型=同步类型,
	)


def 定时执行ozon财务同步():
	主表 = frappe.get_single(配置主表)
	当前 = now_datetime()
	for 行 in 主表.get("table_wckx") or []:
		if not cint(行.get("enable_finance_sync")):
			continue
		try:
			状态 = 行.get("finance_current_task_status")
			if 状态 in {"等待执行", "运行中"}:
				开始时间 = 行.get("finance_current_task_started_at")
				if 状态 == "等待执行" or not 开始时间 or (当前 - get_datetime(开始时间)).total_seconds() < 任务最长秒数:
					continue

			历史状态 = 行.get("finance_history_status")
			if 历史状态 in {"等待执行", "运行中", "已暂停"} and 行.get("finance_history_start_date") and 行.get("finance_history_end_date"):
				下一个日期 = getdate(行.get("finance_history_completed_to")) + timedelta(days=1) if 行.get("finance_history_completed_to") else getdate(行.get("finance_history_start_date"))
				if 下一个日期 <= min(getdate(行.get("finance_history_end_date")), getdate()):
					_更新配置状态(行.name, finance_history_status="等待执行", finance_current_task_status="等待执行", finance_current_execution_type="历史财务")
					_任务入队(行.name, "历史财务")
					continue

			if cint(行.get("enable_auto_finance_sync")):
				下次 = 行.get("finance_next_auto_sync_at")
				if not 下次 or get_datetime(下次) <= 当前:
					_更新配置状态(行.name, finance_current_task_status="等待执行", finance_current_execution_type="最新财务增量")
					_任务入队(行.name, "最新财务增量")
					continue

			for 天数 in (7, 14, 30, 90, 180):
				if not cint(行.get(f"enable_finance_recheck_{天数}")):
					continue
				下次 = 行.get(f"finance_recheck_{天数}_next_at")
				if not 下次 or get_datetime(下次) <= 当前:
					同步类型 = f"{天数}天财务核对"
					_更新配置状态(行.name, finance_current_task_status="等待执行", finance_current_execution_type=同步类型)
					_任务入队(行.name, 同步类型)
					break
			else:
				if cint(行.get("enable_finance_statement_sync")):
					下次 = 行.get("finance_statement_next_sync_at")
					if not 下次 or get_datetime(下次) <= 当前:
						_更新配置状态(行.name, finance_current_task_status="等待执行", finance_current_execution_type="半月结算报告")
						_任务入队(行.name, "半月结算报告")
		except Exception:
			frappe.logger("ozon_finance", allow_site=True).exception(
				"Ozon财务定时任务入队失败：配置行=%s", 行.name
			)
