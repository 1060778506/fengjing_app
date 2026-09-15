# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import hashlib
import json
import re
import time
from datetime import date, datetime, time as datetime_time, timedelta

import frappe
import requests
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate, now_datetime, today


class Ozonrankingstorage(Document):
	pass


配置主表 = "Fengjing - Product Corresponding Platform - Configuration"
配置子表 = "Ozon Store API Sub-table"
存储单据 = "Ozon ranking storage"
平台对应表 = "Fengjing - Product Corresponding Platform - Main Table"
商品汇总接口 = "https://api-seller.ozon.ru/v1/analytics/product-queries"
关键词明细接口 = "https://api-seller.ozon.ru/v1/analytics/product-queries/details"
商品库存接口 = "https://api-seller.ozon.ru/v4/product/info/stocks"
单批商品数 = 1000


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
		frappe.db.set_value(
			配置子表, 配置行名称, 有效值, update_modified=False
		)
		frappe.db.commit()


def _排名任务锁(配置行名称):
	cache = frappe.cache()
	return cache.lock(
		cache.make_key(f"fengjing:ozon-ranking:{配置行名称}"),
		timeout=6 * 60 * 60,
		blocking_timeout=0,
	)


def _错误摘要(响应):
	try:
		数据 = 响应.json()
		内容 = (
			数据.get("message")
			or 数据.get("error_description")
			or 数据.get("error")
			or 数据
		)
		if isinstance(内容, (dict, list)):
			内容 = json.dumps(内容, ensure_ascii=False)
		return str(内容)[:1800]
	except (ValueError, TypeError, AttributeError):
		return str(getattr(响应, "text", "") or "无响应内容")[:1800]


def _发送请求(配置行, 地址, 数据):
	headers = {
		"Client-Id": str(配置行.get("ozon_id") or "").strip(),
		"Api-Key": str(配置行.get("ozon_秘钥") or "").strip(),
		"Content-Type": "application/json",
	}
	if not headers["Client-Id"] or not headers["Api-Key"]:
		raise ValueError("Ozon 排名同步缺少 Ozon ID 或 Ozon 秘钥")

	最后响应 = None
	for 序号, 默认等待秒数 in enumerate((2, 5, 15, 30, 60, 120)):
		try:
			响应 = requests.post(
				地址, headers=headers, json=数据, timeout=90
			)
		except requests.RequestException:
			if 序号 == 5:
				raise
			time.sleep(默认等待秒数)
			continue
		最后响应 = 响应
		if 响应.status_code == 429 and 序号 < 5:
			try:
				等待秒数 = max(
					float(响应.headers.get("Retry-After")), 默认等待秒数
				)
			except (TypeError, ValueError):
				等待秒数 = 默认等待秒数
			time.sleep(min(等待秒数, 300))
			continue
		if 响应.status_code in {500, 502, 503, 504} and 序号 < 5:
			time.sleep(默认等待秒数)
			continue
		return 响应
	return 最后响应


def _请求json(配置行, 地址, 数据, 接口名称):
	响应 = _发送请求(配置行, 地址, 数据)
	if 响应 is None or 响应.status_code != 200:
		状态码 = 响应.status_code if 响应 is not None else "无响应"
		详情 = _错误摘要(响应) if 响应 is not None else ""
		raise RuntimeError(
			f"Ozon {接口名称}失败（HTTP {状态码}）：{详情}"
		)
	try:
		结果 = 响应.json()
	except ValueError as exc:
		raise RuntimeError(f"Ozon {接口名称}返回的不是有效 JSON") from exc
	if not isinstance(结果, dict):
		raise RuntimeError(f"Ozon {接口名称}返回结构异常")
	return 结果


def _分批(values, size=单批商品数):
	for start in range(0, len(values), size):
		yield values[start : start + size]


def _提取库存sku(商品):
	结果 = []
	for 库存 in 商品.get("stocks") or []:
		sku = str(库存.get("sku") or "").strip()
		if sku and sku != "0":
			结果.append(sku)
	return 结果


def _读取店铺商品(配置行):
	"""Return all Ozon SKUs and lightweight metadata without modifying ERPNext."""
	商品映射 = {}
	游标 = None
	已见游标 = set()
	while True:
		请求体 = {"filter": {"visibility": "ALL"}, "limit": 1000}
		if 游标:
			请求体["cursor"] = 游标
		payload = _请求json(配置行, 商品库存接口, 请求体, "商品列表接口")
		items = payload.get("items") or []
		for 商品 in items:
			for sku in _提取库存sku(商品):
				商品映射.setdefault(
					sku,
					{
						"sku": sku,
						"offer_id": str(商品.get("offer_id") or "").strip(),
						"product_id": str(商品.get("product_id") or "").strip(),
					},
				)
		下一个游标 = str(payload.get("cursor") or "").strip()
		if len(items) < 1000 or not 下一个游标 or 下一个游标 in 已见游标:
			break
		已见游标.add(下一个游标)
		游标 = 下一个游标

	# 商品库存接口极少数情况下可能暂时不返回库存来源，使用已存订单兜底。
	if not 商品映射:
		for row in frappe.get_all(
			"Ozon order storage",
			filters={"store": 配置行.get("店铺选项")},
			fields=["sku", "offer_id", "product_id"],
			limit_page_length=0,
		):
			sku = str(row.get("sku") or "").strip()
			if sku:
				商品映射.setdefault(sku, row)
	return 商品映射


def _解析指定sku(value):
	return {
		part.strip()
		for part in re.split(r"[\s,，;；]+", str(value or ""))
		if part.strip()
	}


def _取得抓取商品(配置行):
	商品映射 = _读取店铺商品(配置行)
	范围 = str(配置行.get("排名商品范围") or "全部在售商品").strip()
	if 范围 == "指定 Ozon SKU":
		指定 = _解析指定sku(配置行.get("指定ozon_sku"))
		if not 指定:
			raise ValueError("排名商品范围选择了指定 Ozon SKU，但 SKU 清单为空")
		for sku in 指定:
			商品映射.setdefault(sku, {"sku": sku})
		商品映射 = {sku: 商品映射[sku] for sku in 指定}
	elif 范围 == "仅已绑定物料商品":
		店铺 = str(配置行.get("店铺选项") or "").strip()
		映射行 = frappe.get_all(
			平台对应表,
			filters={"启用": 1, "店铺": 店铺},
			fields=["平台sku", "平台asin"],
			limit_page_length=0,
		)
		允许值 = {
			str(value).strip()
			for row in 映射行
			for value in (row.get("平台sku"), row.get("平台asin"))
			if value not in (None, "")
		}
		商品映射 = {
			sku: info for sku, info in 商品映射.items() if sku in 允许值
		}
	if not 商品映射:
		raise ValueError("没有找到符合排名商品范围的 Ozon SKU")
	return dict(sorted(商品映射.items()))


def _查找对应物料(店铺, sku, offer_id=None, product_id=None):
	base = {"启用": 1, "店铺": 店铺}
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
	item = frappe.db.get_value(
		"Item", item_code, ["item_name", "image"], as_dict=True
	) or {}
	return item_code, item.get("item_name"), item.get("image")


def _安全历史数组(value):
	try:
		result = json.loads(value or "[]")
		return result if isinstance(result, list) else []
	except (TypeError, ValueError, json.JSONDecodeError):
		return []


def _归档旧json(doc, 新hash):
	旧json = str(doc.get("raw_json") or "").strip()
	旧hash = str(doc.get("raw_json_hash") or "").strip()
	if not 旧json or not 旧hash or 旧hash == 新hash:
		return False
	历史 = _安全历史数组(doc.get("raw_json_history"))
	try:
		旧内容 = json.loads(旧json)
	except (TypeError, ValueError, json.JSONDecodeError):
		旧内容 = 旧json
	历史.append(
		{
			"archived_at": str(now_datetime()),
			"position": doc.get("position"),
			"sync_type": doc.get("sync_type"),
			"raw_json_hash": 旧hash,
			"raw_json": 旧内容,
		}
	)
	doc.raw_json_history = json.dumps(
		历史, ensure_ascii=False, sort_keys=True, indent=2
	)
	return True


def _排名唯一键(店铺, sku, 搜索词, 周期开始, 周期结束, 数据层级):
	内容 = "|".join(
		str(value or "").strip()
		for value in (店铺, sku, 搜索词, 周期开始, 周期结束, 数据层级)
	)
	return hashlib.sha256(内容.encode("utf-8")).hexdigest()


def _有效币种(value):
	币种 = str(value or "").strip().upper()
	return 币种 if 币种 and frappe.db.exists("Currency", 币种) else None


def _保存排名记录(
	数据,
	数据层级,
	配置行,
	同步类型,
	周期开始,
	周期结束,
	商品信息=None,
	分析周期=None,
):
	if not isinstance(数据, dict):
		raise ValueError("Ozon 排名数据必须是 JSON 对象")
	商品信息 = 商品信息 or {}
	分析周期 = 分析周期 or {}
	sku = str(数据.get("sku") or 商品信息.get("sku") or "").strip()
	if not sku:
		raise ValueError("Ozon 排名记录缺少 SKU")
	搜索词原文 = str(数据.get("query") or "").strip()
	店铺 = str(配置行.get("店铺选项") or "").strip()
	开始文本 = str(分析周期.get("date_from") or 周期开始)
	结束文本 = str(分析周期.get("date_to") or 周期结束)
	唯一键 = _排名唯一键(
		店铺, sku, 搜索词原文, 开始文本, 结束文本, 数据层级
	)
	原始内容 = {
		"analytics_period": 分析周期,
		"data_level": 数据层级,
		"data": 数据,
	}
	原始json = json.dumps(原始内容, ensure_ascii=False, sort_keys=True, indent=2)
	原始hash = hashlib.sha256(原始json.encode("utf-8")).hexdigest()
	offer_id = str(数据.get("offer_id") or 商品信息.get("offer_id") or "").strip()
	product_id = str(数据.get("product_id") or 商品信息.get("product_id") or "").strip()
	物料, 物料名称, 物料图片 = _查找对应物料(
		店铺, sku, offer_id, product_id
	)
	来源接口 = (
		"/v1/analytics/product-queries/details"
		if 数据层级 == "关键词明细"
		else "/v1/analytics/product-queries"
	)
	字段 = {
		"ranking_record_key": 唯一键,
		"store": 店铺,
		"ozon_id": str(配置行.get("ozon_id") or "").strip(),
		"data_level": 数据层级,
		"sku": sku,
		"offer_id": offer_id,
		"product_id": product_id,
		"product_name": 数据.get("name") or 商品信息.get("name"),
		"category": 数据.get("category") or 商品信息.get("category"),
		"ozon_product_image": 数据.get("image") or 商品信息.get("image"),
		"corresponding_item": 物料,
		"corresponding_item_name": 物料名称,
		"corresponding_item_image": 物料图片,
		"statistics_date": 周期结束,
		"analytics_period_from": f"{周期开始} 00:00:00",
		"analytics_period_to": f"{周期结束} 23:59:59",
		"fetched_at": now_datetime(),
		"search_query": 搜索词原文[:140],
		"position": flt(数据.get("position")),
		"query_index": flt(数据.get("query_index")),
		"unique_search_users": cint(数据.get("unique_search_users")),
		"unique_view_users": cint(数据.get("unique_view_users")),
		"view_conversion": flt(数据.get("view_conversion")),
		"currency_code": _有效币种(数据.get("currency")),
		"gmv": flt(数据.get("gmv")),
		"order_count": cint(数据.get("order_count")),
		"source_endpoint": 来源接口,
		"sync_type": 同步类型,
		"raw_json_hash": 原始hash,
		"sync_status": "成功",
		"last_error": None,
		"raw_json": 原始json,
	}
	字段 = {key: value for key, value in 字段.items() if value not in (None, "")}
	已有名称 = frappe.db.get_value(
		存储单据, {"ranking_record_key": 唯一键}, "name"
	)
	if 已有名称:
		doc = frappe.get_doc(存储单据, 已有名称)
		已归档 = _归档旧json(doc, 原始hash)
		doc.update(字段)
		doc.save(ignore_permissions=True)
		return {"created": 0, "updated": 1, "archived": cint(已归档)}
	doc = frappe.get_doc({"doctype": 存储单据, **字段})
	doc.insert(ignore_permissions=True)
	return {"created": 1, "updated": 0, "archived": 0}


def _累计(汇总, 保存结果):
	汇总["新建"] += 保存结果.get("created", 0)
	汇总["更新"] += 保存结果.get("updated", 0)
	汇总["历史归档"] += 保存结果.get("archived", 0)


def _接口日期范围(开始日期, 结束日期):
	return (
		f"{开始日期.isoformat()}T00:00:00Z",
		f"{结束日期.isoformat()}T23:59:59Z",
	)


def _请求分页(
	配置行, 地址, 基础请求, 结果字段, 接口名称, 每页数量=1000
):
	page = 0
	while True:
		请求体 = {**基础请求, "page": page, "page_size": 每页数量}
		payload = _请求json(配置行, 地址, 请求体, 接口名称)
		rows = payload.get(结果字段) or []
		if not isinstance(rows, list):
			raise RuntimeError(f"Ozon {接口名称}返回的 {结果字段} 不是列表")
		yield payload, rows
		page_count = max(cint(payload.get("page_count")), 1)
		if page + 1 >= page_count or not rows:
			break
		page += 1


def _同步一个统计周期(配置行, 同步类型, 开始日期, 结束日期, 商品映射):
	日期开始, 日期结束 = _接口日期范围(开始日期, 结束日期)
	skus = list(商品映射)
	汇总 = {
		"新建": 0,
		"更新": 0,
		"历史归档": 0,
		"商品汇总记录": 0,
		"关键词记录": 0,
		"分页": 0,
	}
	商品汇总映射 = {}
	for sku批次 in _分批(skus):
		基础请求 = {
			"date_from": 日期开始,
			"date_to": 日期结束,
			"skus": sku批次,
			"sort_by": "BY_SEARCHES",
			"sort_dir": "DESCENDING",
		}
		if cint(配置行.get("抓取商品排名汇总")):
			for payload, rows in _请求分页(
				配置行, 商品汇总接口, 基础请求, "items", "商品排名汇总接口"
			):
				分析周期 = payload.get("analytics_period") or {}
				for row in rows:
					sku = str(row.get("sku") or "").strip()
					商品信息 = {**商品映射.get(sku, {}), **row}
					商品汇总映射[sku] = 商品信息
					保存结果 = _保存排名记录(
						row,
						"商品汇总",
						配置行,
						同步类型,
						开始日期,
						结束日期,
						商品信息,
						分析周期,
					)
					_累计(汇总, 保存结果)
					汇总["商品汇总记录"] += 1
				frappe.db.commit()
				汇总["分页"] += 1

		if cint(配置行.get("抓取搜索关键词明细")):
			关键词上限 = max(cint(配置行.get("每个商品最多关键词数")), 1)
			# Ozon 接口规定 limit_by_sku 只能在 1–15 之间。
			明细请求 = {**基础请求, "limit_by_sku": min(关键词上限, 15)}
			for payload, rows in _请求分页(
				配置行,
				关键词明细接口,
				明细请求,
				"queries",
				"关键词排名明细接口",
				每页数量=100,
			):
				分析周期 = payload.get("analytics_period") or {}
				for row in rows:
					sku = str(row.get("sku") or "").strip()
					商品信息 = 商品汇总映射.get(sku) or 商品映射.get(sku) or {}
					保存结果 = _保存排名记录(
						row,
						"关键词明细",
						配置行,
						同步类型,
						开始日期,
						结束日期,
						商品信息,
						分析周期,
					)
					_累计(汇总, 保存结果)
					汇总["关键词记录"] += 1
				frappe.db.commit()
				汇总["分页"] += 1
	return 汇总


def _合并汇总(总汇总, 本次):
	for key, value in 本次.items():
		总汇总[key] = 总汇总.get(key, 0) + value


def _可用排名结束日期(配置行):
	延迟天数 = max(cint(配置行.get("排名数据延迟天数")), 0)
	return getdate(today()) - timedelta(days=延迟天数)


def _历史下一窗口(配置行):
	开始原值 = 配置行.get("排名历史同步开始日期")
	结束原值 = 配置行.get("排名历史同步结束日期")
	if not 开始原值 or not 结束原值:
		raise ValueError("请先填写排名历史同步开始日期和结束日期")
	配置开始 = getdate(开始原值)
	配置结束 = getdate(结束原值)
	if 配置开始 > 配置结束:
		raise ValueError("排名历史同步开始日期不能晚于结束日期")
	已完成 = 配置行.get("排名历史已完整同步到")
	游标 = getdate(已完成) + timedelta(days=1) if 已完成 else 配置开始
	可用结束 = min(配置结束, _可用排名结束日期(配置行))
	if 游标 > 可用结束:
		return {
			"status": "complete" if 游标 > 配置结束 else "waiting",
			"available_end": 可用结束,
			"configured_end": 配置结束,
		}
	# 最近一个月按天保存；更早的数据按 7 天统计周期保存。
	最近边界 = getdate(today()) - timedelta(days=30)
	周期天数 = 7 if 游标 < 最近边界 else 1
	return {
		"status": "ready",
		"start": 游标,
		"end": min(游标 + timedelta(days=周期天数 - 1), 可用结束),
		"available_end": 可用结束,
		"configured_end": 配置结束,
	}


def _历史进度(配置行, 已完成日期):
	开始 = getdate(配置行.get("排名历史同步开始日期"))
	结束 = getdate(配置行.get("排名历史同步结束日期"))
	总天数 = max((结束 - 开始).days + 1, 1)
	完成天数 = max(min((已完成日期 - 开始).days + 1, 总天数), 0)
	return round(完成天数 / 总天数 * 100, 2)


def _清理过期排名(配置行):
	保留天数 = cint(配置行.get("排名日志保留天数")) or 1825
	截止日期 = getdate(today()) - timedelta(days=max(保留天数, 1))
	frappe.db.delete(
		存储单据,
		{"store": 配置行.get("店铺选项"), "statistics_date": ("<", 截止日期)},
	)
	frappe.db.commit()


@frappe.whitelist()
def 启动ozon历史排名同步(配置行名称):
	_, 配置行 = _取得配置行(配置行名称)
	if not cint(配置行.get("开启ozon商品排名同步")):
		frappe.throw("请先开启 Ozon 商品排名同步并保存")
	if not 配置行.get("排名历史同步开始日期") or not 配置行.get("排名历史同步结束日期"):
		frappe.throw("请先填写排名历史同步开始日期和结束日期")
	if getdate(配置行.get("排名历史同步开始日期")) > getdate(
		配置行.get("排名历史同步结束日期")
	):
		frappe.throw("排名历史同步开始日期不能晚于结束日期")
	_更新配置状态(
		配置行名称,
		排名历史同步状态="等待执行",
		排名当前任务状态="等待执行",
		排名当前执行类型="历史排名",
		排名历史最近错误="",
		排名最近错误="",
	)
	_排名任务入队(配置行名称, "历史排名")
	return {"status": "queued", "message": "Ozon 历史排名同步已进入后台队列"}


@frappe.whitelist()
def 启动ozon最新排名同步(配置行名称):
	_, 配置行 = _取得配置行(配置行名称)
	if not cint(配置行.get("开启ozon商品排名同步")):
		frappe.throw("请先开启 Ozon 商品排名同步并保存")
	_更新配置状态(
		配置行名称,
		排名当前任务状态="等待执行",
		排名当前执行类型="最新排名",
		排名最近错误="",
	)
	_排名任务入队(配置行名称, "最新排名")
	return {"status": "queued", "message": "Ozon 最新排名同步已进入后台队列"}


def 执行ozon排名同步任务(配置行名称, 同步类型):
	lock = _排名任务锁(配置行名称)
	if not lock.acquire(blocking=False):
		return {"status": "busy", "message": "该 Ozon 店铺已有排名同步任务运行"}
	try:
		_更新配置状态(
			配置行名称,
			排名当前任务状态="运行中",
			排名当前执行类型=同步类型,
			排名当前任务开始时间=now_datetime(),
			排名最近错误="",
		)
		_, 配置行 = _取得配置行(配置行名称)
		if not cint(配置行.get("开启ozon商品排名同步")):
			raise ValueError("Ozon 商品排名同步总开关已经关闭")
		if not cint(配置行.get("抓取商品排名汇总")) and not cint(
			配置行.get("抓取搜索关键词明细")
		):
			raise ValueError("请至少开启商品排名汇总或搜索关键词明细中的一种")

		商品映射 = _取得抓取商品(配置行)
		总汇总 = {
			"新建": 0,
			"更新": 0,
			"历史归档": 0,
			"商品汇总记录": 0,
			"关键词记录": 0,
			"分页": 0,
			"统计周期": 0,
			"商品数": len(商品映射),
		}
		if 同步类型 == "历史排名":
			_更新配置状态(配置行名称, 排名历史同步状态="运行中")
			while True:
				_, 配置行 = _取得配置行(配置行名称)
				计划 = _历史下一窗口(配置行)
				if 计划["status"] != "ready":
					已完成 = 计划["status"] == "complete"
					_清理过期排名(配置行)
					_更新配置状态(
						配置行名称,
						排名历史同步状态="已完成" if 已完成 else "已暂停",
						排名历史同步进度=100 if 已完成 else 配置行.get("排名历史同步进度"),
						排名当前任务状态="成功",
						排名当前执行类型="",
						排名历史最近错误="",
					)
					return {"status": 计划["status"], "summary": 总汇总}
				本次 = _同步一个统计周期(
					配置行,
					"历史排名",
					计划["start"],
					计划["end"],
					商品映射,
				)
				_合并汇总(总汇总, 本次)
				总汇总["统计周期"] += 1
				_更新配置状态(
					配置行名称,
					排名历史已完整同步到=计划["end"],
					排名历史同步进度=_历史进度(配置行, 计划["end"]),
					排名历史新增数量=总汇总["新建"],
					排名历史更新数量=总汇总["更新"],
				)

		_, 配置行 = _取得配置行(配置行名称)
		结束日期 = _可用排名结束日期(配置行)
		回看天数 = max(cint(配置行.get("排名自动回看天数")), 1)
		开始日期 = 结束日期 - timedelta(days=回看天数 - 1)
		游标 = 开始日期
		while 游标 <= 结束日期:
			本次 = _同步一个统计周期(
				配置行, "日常同步", 游标, 游标, 商品映射
			)
			_合并汇总(总汇总, 本次)
			总汇总["统计周期"] += 1
			游标 += timedelta(days=1)

		当前时间 = now_datetime()
		间隔小时 = max(cint(配置行.get("排名自动同步间隔小时")), 1)
		_清理过期排名(配置行)
		_更新配置状态(
			配置行名称,
			排名当前任务状态="成功",
			排名当前执行类型="",
			上次排名同步时间=当前时间,
			下次排名同步时间=当前时间 + timedelta(hours=间隔小时),
			上次排名同步结果=json.dumps(
				{"状态": "成功", **总汇总}, ensure_ascii=False
			),
			排名最近错误="",
		)
		return {"status": "success", "summary": 总汇总}
	except Exception as exc:
		错误 = str(exc)[:2000]
		更新字段 = {
			"排名当前任务状态": "失败",
			"排名当前执行类型": "",
			"排名最近错误": 错误,
		}
		if 同步类型 == "历史排名":
			更新字段.update(
				{"排名历史同步状态": "失败", "排名历史最近错误": 错误}
			)
		else:
			更新字段.update(
				{
					"上次排名同步时间": now_datetime(),
					"下次排名同步时间": now_datetime() + timedelta(minutes=30),
					"上次排名同步结果": f"失败：{错误[:1800]}",
				}
			)
		_更新配置状态(配置行名称, **更新字段)
		frappe.logger("ozon_ranking", allow_site=True).exception(
			"Ozon 排名同步失败：配置行=%s，类型=%s",
			配置行名称,
			同步类型,
		)
		raise
	finally:
		try:
			lock.release()
		except Exception:
			pass


def _排名任务入队(配置行名称, 同步类型):
	任务摘要 = hashlib.sha256(
		f"{配置行名称}|{同步类型}".encode("utf-8")
	).hexdigest()[:20]
	frappe.enqueue(
		执行ozon排名同步任务,
		queue="long",
		timeout=6 * 60 * 60,
		enqueue_after_commit=True,
		job_id=f"ozon-ranking-{任务摘要}",
		deduplicate=True,
		配置行名称=配置行名称,
		同步类型=同步类型,
	)


def 定时执行ozon排名同步():
	"""Queue due historical or daily ranking jobs for each enabled Ozon store."""
	主表 = frappe.get_single(配置主表)
	当前时间 = now_datetime()
	for 配置行 in 主表.get("table_wckx") or []:
		if not cint(配置行.get("开启ozon商品排名同步")):
			continue
		try:
			if 配置行.get("排名当前任务状态") == "运行中":
				开始时间 = 配置行.get("排名当前任务开始时间")
				if not 开始时间 or (
					当前时间 - frappe.utils.get_datetime(开始时间)
				).total_seconds() < 6 * 60 * 60:
					continue
			if 配置行.get("排名历史同步状态") in {"等待执行", "已暂停"}:
				if 配置行.get("排名历史同步开始日期") and 配置行.get("排名历史同步结束日期"):
					计划 = _历史下一窗口(配置行)
					if 计划["status"] == "ready":
						_排名任务入队(配置行.name, "历史排名")
						continue
			if not cint(配置行.get("开启自动同步排名")):
				continue
			下次时间 = 配置行.get("下次排名同步时间")
			if not 下次时间 or frappe.utils.get_datetime(下次时间) <= 当前时间:
				_排名任务入队(配置行.name, "最新排名")
		except Exception:
			frappe.logger("ozon_ranking", allow_site=True).exception(
				"Ozon 排名定时任务入队失败：配置行=%s", 配置行.name
			)
