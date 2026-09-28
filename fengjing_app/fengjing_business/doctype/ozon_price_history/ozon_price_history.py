# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import hashlib
import json
import math
import time
import uuid
from datetime import timedelta

import frappe
import requests
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, now_datetime


class OzonPriceHistory(Document):
	pass


配置主表 = "Fengjing - Product Corresponding Platform - Configuration"
配置子表 = "Ozon Store API Sub-table"
存储单据 = "Ozon Price History"
平台对应表 = "Fengjing - Product Corresponding Platform - Main Table"
价格接口 = "https://api-seller.ozon.ru/v5/product/info/prices"
商品信息接口 = "https://api-seller.ozon.ru/v3/product/info/list"
单页数量 = 1000


def _取得配置行(配置行名称):
	主表 = frappe.get_single(配置主表)
	配置行 = next(
		(row for row in (主表.get("table_wckx") or []) if row.name == 配置行名称),
		None,
	)
	if not 配置行:
		raise ValueError(f"找不到 Ozon 店铺配置行：{配置行名称}")
	return 主表, 配置行


def _任务锁(配置行名称):
	cache = frappe.cache()
	return cache.lock(
		cache.make_key(f"fengjing:ozon-price:{配置行名称}"),
		timeout=2 * 60 * 60,
		blocking_timeout=0,
	)


def _错误摘要(响应):
	try:
		数据 = 响应.json()
		内容 = 数据.get("message") or 数据.get("error") or 数据
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
		raise ValueError("Ozon 价格记录缺少 Ozon ID 或 Ozon 秘钥")
	最后响应 = None
	for 序号, 等待秒数 in enumerate((2, 5, 15, 30, 60, 120)):
		try:
			响应 = requests.post(
				地址, headers=headers, json=请求体, timeout=(10, 90)
			)
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
		return 数据, 序号 + 1
	状态码 = 最后响应.status_code if 最后响应 is not None else "无响应"
	raise RuntimeError(f"Ozon {接口名称}重试后仍然失败（HTTP {状态码}）")


def _数字(value):
	try:
		value = float(value)
		return value if math.isfinite(value) else None
	except (TypeError, ValueError):
		return None


def _第一个数字(*values):
	for value in values:
		结果 = _数字(value)
		if 结果 is not None:
			return 结果
	return None


def _读取全部价格(配置行):
	结果, 游标, 已见游标 = [], "", set()
	while True:
		请求体 = {
			"filter": {"visibility": "ALL"},
			"limit": 单页数量,
			"cursor": 游标,
		}
		数据, 尝试次数 = _请求json(配置行, 价格接口, 请求体, "商品价格接口")
		items = 数据.get("items") or (数据.get("result") or {}).get("items") or []
		if not isinstance(items, list):
			raise RuntimeError("Ozon 商品价格接口 items 结构异常")
		for item in items:
			if isinstance(item, dict):
				副本 = dict(item)
				副本["_attempt_count"] = 尝试次数
				结果.append(副本)
		下一个游标 = str(
			数据.get("cursor") or (数据.get("result") or {}).get("cursor") or ""
		).strip()
		if len(items) < 单页数量 or not 下一个游标:
			break
		if 下一个游标 == 游标 or 下一个游标 in 已见游标:
			raise RuntimeError("Ozon 商品价格接口分页游标没有推进")
		已见游标.add(下一个游标)
		游标 = 下一个游标
	return 结果


def _分批(values, size=1000):
	for start in range(0, len(values), size):
		yield values[start : start + size]


def _读取商品信息(配置行, 商品编号列表):
	结果 = {}
	for 一批 in _分批(list(dict.fromkeys(商品编号列表))):
		if not 一批:
			continue
		数据, _ = _请求json(
			配置行,
			商品信息接口,
			{"product_id": [int(v) if str(v).isdigit() else v for v in 一批]},
			"商品信息接口",
		)
		items = 数据.get("items") or (数据.get("result") or {}).get("items") or []
		for item in items:
			编号 = str(item.get("id") or item.get("product_id") or "").strip()
			if 编号:
				结果[编号] = item
	return 结果


def _取得sku列表(价格项, 商品信息):
	values = [价格项.get("sku"), 商品信息.get("sku")]
	values.extend(
		(source.get("sku") for source in (商品信息.get("sources") or []) if isinstance(source, dict))
	)
	return list(dict.fromkeys(str(v).strip() for v in values if str(v or "").strip()))


def _加载物料映射(店铺):
	return frappe.get_all(
		平台对应表,
		filters={"店铺": 店铺, "启用": 1},
		fields=["平台asin", "平台sku", "物料id", "物料名称"],
		limit_page_length=0,
	)


def _匹配物料(映射表, 商品编号, 货号, sku列表):
	商品编号, 货号 = str(商品编号 or "").upper(), str(货号 or "").upper()
	候选sku = {str(v).upper() for v in sku列表 if v}
	if 货号:
		候选sku.add(货号)
	候选 = []
	for row in 映射表:
		平台商品 = str(row.get("平台asin") or "").upper()
		平台sku = str(row.get("平台sku") or "").upper()
		分数 = 0
		if 商品编号 and 平台商品 == 商品编号 and 平台sku in 候选sku:
			分数 = 4
		elif 平台sku and 平台sku in 候选sku:
			分数 = 3
		elif 商品编号 and 平台商品 == 商品编号:
			分数 = 2
		if 分数 and row.get("物料id"):
			候选.append((分数, row))
	return max(候选, key=lambda value: value[0])[1] if 候选 else None


def _市场价格数据(价格项, 卖家币种=None):
	indexes = 价格项.get("price_indexes") or {}
	候选 = []
	for key in ("external_index_data", "ozon_index_data", "self_marketplaces_index_data"):
		entry = indexes.get(key) or {}
		# Ozon 同时返回卢布价格和按卖家后台币种换算后的价格。
		# 本单据其它价格均使用卖家币种，因此优先使用 min_price_in_seller。
		卖家最低价 = _第一个数字(entry.get("min_price_in_seller"))
		卖家最低价币种 = str(entry.get("min_price_in_seller_currency") or "").upper()
		原始最低价 = _第一个数字(entry.get("minimal_price"), entry.get("min_price"))
		原始最低价币种 = str(entry.get("min_price_currency") or "").upper()
		if 卖家最低价 is not None and 卖家最低价 > 0 and (
			not 卖家币种 or not 卖家最低价币种 or 卖家最低价币种 == 卖家币种
		):
			最低价 = 卖家最低价
		elif 原始最低价 is not None and 原始最低价 > 0 and (
			not 卖家币种 or not 原始最低价币种 or 原始最低价币种 == 卖家币种
		):
			最低价 = 原始最低价
		else:
			最低价 = None
		指数 = _第一个数字(entry.get("price_index_value"), entry.get("index_value"))
		if 最低价 is not None and 最低价 > 0:
			候选.append((最低价, 指数))
	if not 候选:
		return None, None
	return min(候选, key=lambda value: value[0])


def _佣金比例(价格项):
	commissions = 价格项.get("commissions") or {}
	if isinstance(commissions, dict):
		return _第一个数字(
			commissions.get("sales_percent_rfbs"),
			commissions.get("sales_percent_fbs"),
			commissions.get("sales_percent_fbo"),
		)
	if isinstance(commissions, list):
		for item in commissions:
			if isinstance(item, dict):
				value = _第一个数字(item.get("percent"), item.get("value"))
				if value is not None:
					return value
	return None


def _上一价格(店铺, 商品编号, 当前时间桶):
	rows = frappe.get_all(
		存储单据,
		filters={
			"store": 店铺,
			"ozon_product_id": 商品编号,
			"hour_bucket": ["<", 当前时间桶],
		},
		fields=["buyer_price", "seller_price"],
		order_by="hour_bucket desc",
		limit_page_length=1,
	)
	if not rows:
		return None
	return _第一个数字(rows[0].get("buyer_price"), rows[0].get("seller_price"))


def _日期时间(value):
	if not value:
		return None
	try:
		return get_datetime(value)
	except Exception:
		return None


def _保存价格快照(配置行, 价格项, 商品信息, 映射表, 批次id, 记录时间, 时间桶):
	商品编号 = str(
		价格项.get("product_id") or 商品信息.get("id") or 商品信息.get("product_id") or ""
	).strip()
	if not 商品编号:
		raise ValueError("Ozon 价格记录缺少 Product ID")
	店铺 = 配置行.get("店铺选项")
	货号 = str(价格项.get("offer_id") or 商品信息.get("offer_id") or "").strip()
	sku列表 = _取得sku列表(价格项, 商品信息)
	映射 = _匹配物料(映射表, 商品编号, 货号, sku列表)
	price = 价格项.get("price") or {}
	卖家价格 = _第一个数字(price.get("price"), 价格项.get("price"))
	买家价格 = _第一个数字(
		price.get("marketing_price"), price.get("marketing_seller_price"), 卖家价格
	)
	原价 = _第一个数字(price.get("old_price"))
	促销价 = _第一个数字(price.get("marketing_seller_price"))
	币种 = str(price.get("currency_code") or 价格项.get("currency_code") or "").upper()
	市场最低价, 价格指数 = _市场价格数据(价格项, 币种)
	佣金比例 = _佣金比例(价格项)
	上一价格 = _上一价格(店铺, 商品编号, 时间桶)
	比较价格 = 买家价格 if 买家价格 is not None else 卖家价格
	变化金额 = None if 上一价格 is None or 比较价格 is None else 比较价格 - 上一价格
	变化百分比 = None if 上一价格 in (None, 0) or 变化金额 is None else 变化金额 / 上一价格 * 100
	if 上一价格 is None:
		变化方向 = "首次记录"
	elif abs(变化金额 or 0) < 0.000001:
		变化方向 = "不变"
	elif 变化金额 > 0:
		变化方向 = "上涨"
	else:
		变化方向 = "下降"
	图片 = 商品信息.get("primary_image") or 商品信息.get("images") or ""
	if isinstance(图片, list):
		图片 = 图片[0] if 图片 else ""
	促销信息 = 价格项.get("marketing_actions") or {}
	有促销 = bool(
		促销信息.get("actions")
		or (原价 is not None and 比较价格 is not None and 比较价格 < 原价)
	)
	快照键 = hashlib.sha256(
		f"{店铺}|{商品编号}|{时间桶.isoformat()}".encode("utf-8")
	).hexdigest()
	raw_item = {key: value for key, value in 价格项.items() if key != "_attempt_count"}
	raw_json = json.dumps(raw_item, ensure_ascii=False, sort_keys=True, default=str)
	values = {
		"snapshot_key": 快照键,
		"store": 店铺,
		"ozon_product_id": 商品编号,
		"sku_id": sku列表[0] if sku列表 else "",
		"offer_id": 货号,
		"corresponding_item": 映射.get("物料id") if 映射 else None,
		"product_name": 商品信息.get("name") or 价格项.get("name"),
		"ozon_image_url": 图片,
		"recorded_at": 记录时间,
		"hour_bucket": 时间桶,
		"source_updated_at": _日期时间(价格项.get("updated_at") or price.get("updated_at")),
		"fetched_at": 记录时间,
		"currency_code": 币种,
		"seller_price": 卖家价格,
		"buyer_price": 买家价格,
		"old_price": 原价,
		"marketing_seller_price": 促销价,
		"premium_price": _第一个数字(price.get("premium_price")),
		"minimum_price": _第一个数字(price.get("min_price"), price.get("minimum_price")),
		"market_min_price": 市场最低价,
		"recommended_price": _第一个数字(price.get("recommended_price")),
		"ozon_cost_price": _第一个数字(price.get("net_price")),
		"price_index": 价格指数,
		"commission_percent": 佣金比例,
		"commission_amount": (比较价格 * 佣金比例 / 100) if 比较价格 is not None and 佣金比例 is not None else None,
		"promotion_discount": max((原价 or 0) - (比较价格 or 0), 0) if 原价 is not None and 比较价格 is not None else None,
		"has_promotion": 1 if 有促销 else 0,
		"auto_action_enabled": cint(price.get("auto_action_enabled")),
		"visibility": 价格项.get("visibility") or 商品信息.get("visibility"),
		"product_status": 价格项.get("status") or 商品信息.get("status_name") or 商品信息.get("status"),
		"previous_price": 上一价格,
		"price_change_amount": 变化金额,
		"price_change_percent": 变化百分比,
		"change_direction": 变化方向,
		"price_changed": 1 if 变化方向 in {"上涨", "下降"} else 0,
		"sync_batch_id": 批次id,
		"attempt_count": cint(价格项.get("_attempt_count")) or 1,
		"sync_status": "成功",
		"raw_json_hash": hashlib.sha256(raw_json.encode("utf-8")).hexdigest(),
		"last_error": "",
		"raw_json": raw_json,
	}
	name = frappe.db.get_value(存储单据, {"snapshot_key": 快照键}, "name")
	if name:
		doc = frappe.get_doc(存储单据, name)
		doc.update(values)
		doc.save(ignore_permissions=True)
		return "updated"
	frappe.get_doc({"doctype": 存储单据, **values}).insert(ignore_permissions=True)
	return "inserted"


def 执行ozon价格抓取任务(配置行名称, 手动触发=0):
	lock = _任务锁(配置行名称)
	if not lock.acquire(blocking=False):
		return {"status": "busy", "message": "该 Ozon 店铺已有价格抓取任务运行"}
	try:
		_, 配置行 = _取得配置行(配置行名称)
		if not cint(配置行.get("开启价格记录")):
			raise ValueError("Ozon 价格记录开关已经关闭")
		记录时间 = now_datetime()
		时间桶 = 记录时间.replace(minute=0, second=0, microsecond=0)
		批次id = uuid.uuid4().hex
		价格列表 = _读取全部价格(配置行)
		商品编号列表 = [
			str(item.get("product_id") or "").strip()
			for item in 价格列表 if item.get("product_id")
		]
		try:
			商品信息 = _读取商品信息(配置行, 商品编号列表)
		except Exception:
			商品信息 = {}
			frappe.logger("ozon_price", allow_site=True).exception(
				"读取Ozon商品名称与图片失败，价格快照继续保存：配置行=%s", 配置行名称
			)
		映射表 = _加载物料映射(配置行.get("店铺选项"))
		汇总 = {"商品数": len(价格列表), "新建": 0, "更新": 0, "失败": 0}
		失败详情 = []
		for index, item in enumerate(价格列表, 1):
			商品编号 = str(item.get("product_id") or "").strip()
			try:
				结果 = _保存价格快照(
					配置行, item, 商品信息.get(商品编号, {}), 映射表,
					批次id, 记录时间, 时间桶,
				)
				汇总["新建" if 结果 == "inserted" else "更新"] += 1
			except Exception as exc:
				汇总["失败"] += 1
				失败详情.append(f"{商品编号 or '未知商品'}：{str(exc)[:300]}")
			if index % 200 == 0:
				frappe.db.commit()
		frappe.db.commit()
		if 价格列表 and 汇总["失败"] == len(价格列表):
			raise RuntimeError("全部商品价格记录失败：" + "；".join(失败详情[:5]))
		frappe.logger("ozon_price", allow_site=True).info(
			"Ozon价格抓取完成：配置行=%s，店铺=%s，批次=%s，汇总=%s",
			配置行名称, 配置行.get("店铺选项"), 批次id,
			json.dumps(汇总, ensure_ascii=False),
		)
		return {"status": "success", "batch_id": 批次id, "summary": 汇总}
	except Exception:
		frappe.db.rollback()
		frappe.logger("ozon_price", allow_site=True).exception(
			"Ozon价格抓取失败：配置行=%s，手动触发=%s", 配置行名称, 手动触发
		)
		frappe.log_error(frappe.get_traceback(), "Ozon商品价格抓取失败")
		raise
	finally:
		try:
			lock.release()
		except Exception:
			pass


def _任务入队(配置行名称, 手动触发=0):
	摘要 = hashlib.sha256(str(配置行名称).encode("utf-8")).hexdigest()[:20]
	frappe.enqueue(
		执行ozon价格抓取任务,
		queue="long",
		timeout=2 * 60 * 60,
		enqueue_after_commit=True,
		job_id=f"ozon-price-{摘要}",
		deduplicate=True,
		配置行名称=配置行名称,
		手动触发=cint(手动触发),
	)


@frappe.whitelist()
def 启动ozon价格抓取(配置行名称):
	_, 配置行 = _取得配置行(配置行名称)
	if not cint(配置行.get("开启价格记录")):
		frappe.throw("请先开启“开启价格记录”并保存")
	if not str(配置行.get("ozon_id") or "").strip() or not str(
		配置行.get("ozon_秘钥") or ""
	).strip():
		frappe.throw("请先填写 Ozon ID 和 Ozon 秘钥")
	_任务入队(配置行名称, 手动触发=1)
	return {"status": "queued", "message": "Ozon 商品价格抓取已进入后台队列"}


def 定时执行ozon价格记录():
	"""每分钟检查一次配置；每个店铺按自己的分钟间隔入队。"""
	主表 = frappe.get_single(配置主表)
	当前时间 = now_datetime()
	cache = frappe.cache()
	for 配置行 in 主表.get("table_wckx") or []:
		if not cint(配置行.get("开启价格记录")):
			continue
		try:
			间隔分钟 = max(cint(配置行.get("价格记录间隔分钟")) or 60, 5)
			节流键 = cache.make_key(f"fengjing:ozon-price-enqueue:{配置行.name}")
			if cache.get_value(节流键):
				continue
			最近 = frappe.get_all(
				存储单据,
				filters={"store": 配置行.get("店铺选项")},
				fields=["recorded_at"],
				order_by="recorded_at desc",
				limit_page_length=1,
			)
			if 最近 and 最近[0].recorded_at:
				经过分钟 = (当前时间 - get_datetime(最近[0].recorded_at)).total_seconds() / 60
				if 经过分钟 < 间隔分钟:
					continue
			_任务入队(配置行.name)
			cache.set_value(节流键, 1, expires_in_sec=max(300, 间隔分钟 * 60))
		except Exception:
			frappe.logger("ozon_price", allow_site=True).exception(
				"Ozon价格定时任务入队失败：配置行=%s", 配置行.name
			)
