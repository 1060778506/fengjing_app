# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import frappe
import requests
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, get_system_timezone, now_datetime


class AmazonFinancialTransaction(Document):
    pass


配置主表 = "Fengjing - Product Corresponding Platform - Configuration"
配置子表 = "Amazon Financial Transaction Configuration - Sub-table"
配置表字段 = "亚马逊财务交易配置表"
存储单据 = "Amazon Financial Transaction"

# listTransactions 要求结束时间至少早于当前时间2分钟。
安全延迟分钟 = 3
增量重叠分钟 = 10
默认历史分段天数 = 30
单段最大天数 = 179
财务接口最小间隔秒 = 2.1
任务超时秒 = 6 * 60 * 60


def _系统时间转utc(时间值):
    if not 时间值:
        return None
    时间 = get_datetime(时间值)
    if 时间.tzinfo is None:
        时间 = 时间.replace(tzinfo=ZoneInfo(get_system_timezone()))
    return 时间.astimezone(timezone.utc)


def _utc转系统时间(时间值):
    if not 时间值:
        return None
    if 时间值.tzinfo is None:
        时间值 = 时间值.replace(tzinfo=timezone.utc)
    return 时间值.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)


def _utc字符串(时间值):
    return 时间值.astimezone(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _amazon时间转系统(时间值):
    if not 时间值:
        return None
    文本 = str(时间值).strip()
    if 文本.endswith("Z"):
        文本 = 文本[:-1] + "+00:00"
    时间 = get_datetime(文本)
    if 时间.tzinfo is None:
        时间 = 时间.replace(tzinfo=timezone.utc)
    return _utc转系统时间(时间.astimezone(timezone.utc))


def _确保数据库连接():
    """限流等待可能较长，写入前恢复可能已失效的MariaDB连接。"""
    try:
        frappe.db.sql("select 1")
    except Exception:
        try:
            frappe.db.close()
        except Exception:
            pass
        frappe.connect()


def _取得配置行(行名称):
    _确保数据库连接()
    主表 = frappe.get_single(配置主表)
    行 = next(
        (row for row in (主表.get(配置表字段) or []) if row.name == 行名称),
        None,
    )
    if not 行:
        raise ValueError(f"找不到Amazon财务交易配置行：{行名称}")
    return 主表, 行


def _更新配置状态(行名称, **字段):
    _确保数据库连接()
    有效字段 = set(frappe.get_meta(配置子表).get_valid_columns())
    字段 = {key: value for key, value in 字段.items() if key in 有效字段}
    if 字段:
        frappe.db.set_value(配置子表, 行名称, 字段, update_modified=False)
        frappe.db.commit()


def _匹配api配置(主表, 配置行):
    店铺 = str(配置行.get("店铺") or "").strip()
    站点id = str(配置行.get("marketplace_id") or "").strip().upper()
    for api行 in 主表.get("亚马逊api") or []:
        if (
            str(api行.get("店铺选项") or "").strip() == 店铺
            and str(api行.get("站点id") or "").strip().upper() == 站点id
        ):
            for 字段 in ("刷新令牌", "客户端编码", "客户端密钥"):
                if not str(api行.get(字段) or "").strip():
                    raise ValueError(f"店铺 {店铺} 的Amazon API缺少{字段}")
            return api行
    raise ValueError(f"找不到店铺 {店铺}、站点 {站点id} 对应的Amazon API配置")


def _获取访问令牌(api行):
    from fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log import 亚马逊请求

    响应 = 亚马逊请求(
        "POST",
        "https://api.amazon.com/auth/o2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": str(api行.get("刷新令牌") or "").strip(),
            "client_id": str(api行.get("客户端编码") or "").strip(),
            "client_secret": str(api行.get("客户端密钥") or "").strip(),
        },
        timeout=20,
        retries=3,
    )
    if 响应.status_code != 200:
        raise RuntimeError(f"Amazon授权失败（HTTP {响应.status_code}）：{响应.text[:500]}")
    令牌 = (响应.json() or {}).get("access_token")
    if not 令牌:
        raise RuntimeError("Amazon授权成功但没有返回Access Token")
    return 令牌


def _限流键(api行):
    授权标识 = "|".join((
        str(api行.get("客户端编码") or "").strip(),
        str(api行.get("刷新令牌") or "").strip(),
    ))
    摘要 = hashlib.sha256(授权标识.encode("utf-8")).hexdigest()[:24]
    return f"fengjing:amazon-finances-rate:{摘要}"


def _等待财务接口额度(api行):
    """在Redis中为同一套Amazon授权串行预留请求时间。"""
    cache = frappe.cache()
    键 = _限流键(api行)
    with cache.lock(cache.make_key(f"{键}:lock"), timeout=30, blocking_timeout=30):
        当前 = time.time()
        try:
            上次预留 = float(cache.get_value(键) or 0)
        except (TypeError, ValueError):
            上次预留 = 0
        本次预留 = max(当前, 上次预留 + 财务接口最小间隔秒)
        cache.set_value(键, 本次预留, expires_in_sec=24 * 60 * 60)
    等待秒数 = 本次预留 - time.time()
    if 等待秒数 > 0:
        time.sleep(等待秒数)


def _发送财务api请求(api行, url, **kwargs):
    from fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log import 亚马逊请求

    等待序列 = (5, 10, 20, 40, 60, 120)
    最后响应 = None
    for 序号, 等待秒数 in enumerate(等待序列):
        _等待财务接口额度(api行)
        try:
            响应 = 亚马逊请求("GET", url, retries=1, **kwargs)
        except requests.RequestException:
            if 序号 == len(等待序列) - 1:
                raise
            time.sleep(等待秒数)
            continue
        最后响应 = 响应
        if 响应.status_code == 429 or 响应.status_code in {500, 502, 503, 504}:
            if 序号 < len(等待序列) - 1:
                retry_after = 响应.headers.get("Retry-After")
                try:
                    实际等待 = max(float(retry_after), 等待秒数) if retry_after else 等待秒数
                except (TypeError, ValueError):
                    实际等待 = 等待秒数
                time.sleep(min(实际等待, 300))
                continue
        return 响应
    return 最后响应


def _标识字典(列表, 名称键="relatedIdentifierName", 值键="relatedIdentifierValue"):
    结果 = {}
    for 项 in 列表 or []:
        名称 = str(项.get(名称键) or "").strip().upper()
        值 = str(项.get(值键) or "").strip()
        if 名称 and 值 and 名称 not in 结果:
            结果[名称] = 值
    return 结果


def _金额(对象):
    if not isinstance(对象, dict):
        return 0.0
    return flt(对象.get("currencyAmount") or 对象.get("amount"))


def _币种(对象):
    if not isinstance(对象, dict):
        return ""
    return str(对象.get("currencyCode") or "").strip().upper()


def _叶子费用(列表):
    if isinstance(列表, dict):
        # 兼容Amazon某些响应把breakdowns多包一层对象的情况。
        列表 = 列表.get("breakdowns") or [列表]
    for 项 in 列表 or []:
        if not isinstance(项, dict):
            continue
        子项 = 项.get("breakdowns") or []
        if 子项:
            yield from _叶子费用(子项)
        else:
            yield 项


def _费用分类(费用列表):
    """只统计breakdown叶子节点，避免同时累加父级汇总和子级明细。"""
    结果 = {
        "product_sales": 0.0,
        "product_sales_tax": 0.0,
        "shipping_credits": 0.0,
        "shipping_credits_tax": 0.0,
        "promotional_rebates": 0.0,
        "promotional_rebates_tax": 0.0,
        "marketplace_withheld_tax": 0.0,
        "selling_fees": 0.0,
        "fba_fees": 0.0,
        "other_transaction_fees": 0.0,
        "other_amount": 0.0,
    }
    for 项 in _叶子费用(费用列表):
        原类型 = str(项.get("breakdownType") or "")
        类型 = "".join(ch for ch in 原类型.lower() if ch.isalnum())
        值 = _金额(项.get("breakdownAmount") or {})
        if not 类型:
            结果["other_amount"] += 值
        elif any(key in 类型 for key in ("withheldtax", "marketplacefacilitator", "taxwithholding")):
            结果["marketplace_withheld_tax"] += 值
        elif "shipping" in 类型 and "tax" in 类型:
            结果["shipping_credits_tax"] += 值
        elif "shipping" in 类型 and not any(key in 类型 for key in ("fee", "fba", "fulfillment")):
            结果["shipping_credits"] += 值
        elif any(key in 类型 for key in ("promotion", "promotional", "rebate", "discount")) and "tax" in 类型:
            结果["promotional_rebates_tax"] += 值
        elif any(key in 类型 for key in ("promotion", "promotional", "rebate", "discount")):
            结果["promotional_rebates"] += 值
        elif any(key in 类型 for key in ("fba", "fulfillment", "storage", "warehouse")):
            结果["fba_fees"] += 值
        elif any(key in 类型 for key in ("commission", "referral", "sellingfee")):
            结果["selling_fees"] += 值
        elif "fee" in 类型:
            结果["other_transaction_fees"] += 值
        elif "tax" in 类型:
            结果["product_sales_tax"] += 值
        elif any(key in 类型 for key in ("principal", "productcharge", "productsale", "itemprice")):
            结果["product_sales"] += 值
        else:
            结果["other_amount"] += 值
    return 结果


def _上下文值(列表, 字段):
    for 项 in 列表 or []:
        if 项.get(字段) not in (None, ""):
            return 项.get(字段)
    return None


def _取国家(站点id, 配置国家=None):
    if 配置国家 and frappe.db.exists("Country", 配置国家):
        return 配置国家
    from fengjing_app.fengjing_business.doctype.amazon_order_synchronization.amazon_order_synchronization import MARKETPLACE_COUNTRIES

    国家 = MARKETPLACE_COUNTRIES.get(站点id)
    return 国家 if 国家 and frappe.db.exists("Country", 国家) else None


def _追加历史json(doc):
    旧文本 = str(doc.get("raw_json") or "").strip()
    if not 旧文本:
        return
    try:
        历史 = json.loads(doc.get("raw_json_history") or "[]")
        if not isinstance(历史, list):
            历史 = []
    except (TypeError, ValueError, json.JSONDecodeError):
        历史 = []
    try:
        旧内容 = json.loads(旧文本)
    except (TypeError, ValueError, json.JSONDecodeError):
        旧内容 = 旧文本
    历史.append({
        "archived_at": str(now_datetime()),
        "transaction_status": doc.get("transaction_status"),
        "posted_date": str(doc.get("posted_date") or ""),
        "raw_json_hash": doc.get("raw_json_hash"),
        "raw_json": 旧内容,
    })
    doc.raw_json_history = json.dumps(历史, ensure_ascii=False, sort_keys=True, indent=2)


def 保存亚马逊财务交易(交易, 店铺, 配置站点id, api区域, 同步类型, 配置国家=None):
    """按“交易ID + SKU”展平保存，一个无SKU交易至少保存一行。"""
    if not isinstance(交易, dict):
        raise ValueError("Amazon财务交易必须是JSON对象")

    原始文本 = json.dumps(交易, ensure_ascii=False, sort_keys=True, indent=2)
    原始哈希 = hashlib.sha256(原始文本.encode("utf-8")).hexdigest()
    相关标识 = _标识字典(交易.get("relatedIdentifiers"))
    元数据 = 交易.get("sellingPartnerMetadata") or {}
    市场 = 交易.get("marketplaceDetails") or {}
    站点id = str(
        元数据.get("marketplaceId")
        or 市场.get("marketplaceId")
        or 配置站点id
        or ""
    ).strip().upper()
    交易id = str(交易.get("transactionId") or "").strip()
    if not 交易id:
        稳定身份 = json.dumps({
            "type": 交易.get("transactionType"),
            "postedDate": 交易.get("postedDate"),
            "identifiers": 相关标识,
            "totalAmount": 交易.get("totalAmount"),
        }, ensure_ascii=False, sort_keys=True)
        交易id = "generated-" + hashlib.sha256(稳定身份.encode("utf-8")).hexdigest()

    交易上下文 = 交易.get("contexts") or []
    交易总额 = 交易.get("totalAmount") or {}
    发布时间 = _amazon时间转系统(交易.get("postedDate"))
    基础数据 = {
        "transaction_id": 交易id,
        "amazon_order_id": 相关标识.get("ORDER_ID"),
        "financial_event_group_id": 相关标识.get("FINANCIAL_EVENT_GROUP_ID"),
        "settlement_id": 相关标识.get("SETTLEMENT_ID"),
        "invoice_id": 相关标识.get("INVOICE_ID"),
        "store": 店铺,
        "country": _取国家(站点id, 配置国家),
        "marketplace_id": 站点id,
        "api_region": api区域,
        "sync_type": 同步类型,
        "transaction_type": 交易.get("transactionType"),
        "transaction_status": 交易.get("transactionStatus"),
        "description": 交易.get("description"),
        "fulfillment_channel": _上下文值(交易上下文, "channel"),
        "store_name": _上下文值(交易上下文, "storeName") or 市场.get("marketplaceName"),
        "posted_date": 发布时间,
        "release_date": _amazon时间转系统(_上下文值(交易上下文, "maturityDate")),
        "fetched_at": now_datetime(),
        "currency_code": _币种(交易总额),
        "total_amount": _金额(交易总额),
        "net_amount": _金额(交易总额),
        "raw_json_hash": 原始哈希,
        "sync_status": "成功",
        "last_error": None,
        "raw_json": 原始文本,
    }

    物料列表 = 交易.get("items") or []
    if not 物料列表:
        物料列表 = [{"contexts": 交易上下文, "breakdowns": 交易.get("breakdowns") or []}]

    # 同一交易中同一SKU只保留一行，数量和金额合并。
    分组 = {}
    for 物料行 in 物料列表:
        上下文 = 物料行.get("contexts") or []
        sku = str(_上下文值(上下文, "sku") or "").strip()
        分组.setdefault(sku, []).append(物料行)

    统计 = {"created": 0, "updated": 0, "unchanged": 0}
    from fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log import 获取平台映射物料

    for sku, 同sku行 in 分组.items():
        首行 = 同sku行[0]
        所有上下文 = [ctx for row in 同sku行 for ctx in (row.get("contexts") or [])]
        asin = str(_上下文值(所有上下文, "asin") or "").strip().upper()
        数量 = sum(cint(_上下文值(row.get("contexts") or [], "quantityShipped")) for row in 同sku行)
        明细总额 = sum(_金额(row.get("totalAmount") or {}) for row in 同sku行)
        明细费用 = []
        for row in 同sku行:
            row_breakdowns = row.get("breakdowns") or []
            if isinstance(row_breakdowns, dict):
                row_breakdowns = row_breakdowns.get("breakdowns") or [row_breakdowns]
            明细费用.extend(row_breakdowns)
        if not 明细费用 and len(分组) == 1:
            明细费用 = 交易.get("breakdowns") or []
        分类金额 = _费用分类(明细费用)
        明细标识 = {}
        for row in 同sku行:
            明细标识.update(_标识字典(
                row.get("relatedIdentifiers"),
                "itemRelatedIdentifierName",
                "itemRelatedIdentifierValue",
            ))
        # 加入店铺和站点边界，避免不同卖家授权恰好返回相同交易ID时相互覆盖。
        唯一原文 = f"{店铺}::{站点id}::{交易id}::{sku or '__NO_SKU__'}"
        唯一键 = hashlib.sha256(唯一原文.encode("utf-8")).hexdigest()
        对应物料 = 获取平台映射物料(店铺, 站点id, asin, sku)
        对应物料名称 = frappe.db.get_value("Item", 对应物料, "item_name") if 对应物料 else None
        行数据 = {
            **基础数据,
            "transaction_sku_key": 唯一键,
            "sku": sku or None,
            "asin": asin or None,
            "amazon_order_item_id": (
                明细标识.get("ORDER_ITEM_ID")
                or 明细标识.get("ORDER_ADJUSTMENT_ITEM_ID")
                or 明细标识.get("REMOVAL_SHIPMENT_ITEM_ID")
            ),
            "product_name": 首行.get("description"),
            "quantity": 数量,
            "corresponding_item": 对应物料,
            "corresponding_item_name": 对应物料名称,
            "fulfillment_channel": _上下文值(所有上下文, "fulfillmentNetwork") or 基础数据.get("fulfillment_channel"),
            "release_date": _amazon时间转系统(_上下文值(所有上下文, "maturityDate")) or 基础数据.get("release_date"),
            "item_amount": 明细总额,
            "currency_code": _币种(首行.get("totalAmount") or {}) or 基础数据["currency_code"],
            "breakdown_json": json.dumps({
                "transaction_breakdowns": 交易.get("breakdowns") or [],
                "item_breakdowns": 明细费用,
            }, ensure_ascii=False, sort_keys=True, indent=2),
            **分类金额,
        }
        文档名 = frappe.db.get_value(存储单据, {"transaction_sku_key": 唯一键}, "name")
        if not 文档名:
            doc = frappe.get_doc({"doctype": 存储单据, **行数据})
            doc.insert(ignore_permissions=True)
            统计["created"] += 1
            continue

        doc = frappe.get_doc(存储单据, 文档名)
        if str(doc.get("raw_json_hash") or "") == 原始哈希:
            # 原始交易未变也要刷新派生字段，例如用户后续新增了SKU对应物料。
            frappe.db.set_value(存储单据, 文档名, 行数据, update_modified=False)
            统计["unchanged"] += 1
            continue
        _追加历史json(doc)
        doc.update(行数据)
        doc.data_version = cint(doc.get("data_version")) + 1
        doc.save(ignore_permissions=True)
        统计["updated"] += 1
    return 统计


def _规划历史窗口(配置行, 当前utc=None):
    if not 配置行.get("历史同步开始时间") or not 配置行.get("历史同步结束时间"):
        raise ValueError("必须同时填写历史同步开始时间和结束时间")
    开始 = _系统时间转utc(配置行.get("历史同步开始时间"))
    目标结束 = _系统时间转utc(配置行.get("历史同步结束时间"))
    if 目标结束 <= 开始:
        raise ValueError("历史同步结束时间必须晚于开始时间")
    现在 = 当前utc or datetime.now(timezone.utc)
    if 现在.tzinfo is None:
        现在 = 现在.replace(tzinfo=timezone.utc)
    安全结束 = 现在.astimezone(timezone.utc) - timedelta(minutes=安全延迟分钟)
    可抓结束 = min(目标结束, 安全结束)
    游标 = _系统时间转utc(配置行.get("历史已完整同步到")) or 开始
    游标 = max(游标, 开始)
    if 游标 >= 可抓结束:
        return {
            "status": "waiting" if 目标结束 > 安全结束 else "complete",
            "cursor": 游标,
            "target_end": 目标结束,
            "progress": min(max((游标 - 开始).total_seconds() / max((目标结束 - 开始).total_seconds(), 1) * 100, 0), 100),
        }
    分段天数 = min(max(cint(配置行.get("历史分段天数")) or 默认历史分段天数, 1), 单段最大天数)
    本次结束 = min(可抓结束, 游标 + timedelta(days=分段天数))
    return {
        "status": "ready",
        "start": 游标,
        "end": 本次结束,
        "target_start": 开始,
        "target_end": 目标结束,
        "progress": min(max((游标 - 开始).total_seconds() / max((目标结束 - 开始).total_seconds(), 1) * 100, 0), 100),
    }


def _同步查询窗口(配置行, api行, 同步类型, 开始utc, 结束utc):
    from fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log import (
        SP_API站点区域,
        获取SP_API区域地址,
    )

    站点id = str(配置行.get("marketplace_id") or "").strip().upper()
    店铺 = str(配置行.get("店铺") or "").strip()
    api地址 = 获取SP_API区域地址(站点id)
    if not api地址:
        raise ValueError(f"无法识别Marketplace ID {站点id} 所属的SP-API区域")
    令牌 = _获取访问令牌(api行)
    url = f"{api地址}/finances/2024-06-19/transactions"
    基础参数 = {
        "postedAfter": _utc字符串(开始utc),
        "postedBefore": _utc字符串(结束utc),
        "marketplaceId": 站点id,
    }
    下一页 = None
    已刷新访问令牌 = False
    已见令牌 = set()
    汇总 = {"新建": 0, "更新": 0, "未变化": 0, "交易": 0, "分页": 0}
    while True:
        参数 = dict(基础参数)
        if 下一页:
            参数["nextToken"] = 下一页
        响应 = _发送财务api请求(
            api行,
            url,
            headers={
                "X-Amz-Access-Token": 令牌,
                "Accept": "application/json",
                "User-Agent": "FengjingAmazonFinances/1.0",
            },
            params=参数,
            timeout=60,
        )
        _确保数据库连接()
        if 响应 is not None and 响应.status_code == 401 and not 已刷新访问令牌:
            令牌 = _获取访问令牌(api行)
            已刷新访问令牌 = True
            continue
        if 响应 is None or 响应.status_code != 200:
            状态码 = 响应.status_code if 响应 is not None else "无响应"
            内容 = 响应.text[:1200] if 响应 is not None else "Amazon未返回响应"
            if 状态码 == 403:
                内容 += "；请确认SP-API已批准Finance and Accounting角色"
            raise RuntimeError(f"Finances API失败（HTTP {状态码}）：{内容}")
        返回体 = 响应.json() or {}
        # Amazon生产环境实际返回包含payload层，同时兼容文档旧结构。
        payload = 返回体.get("payload") if isinstance(返回体.get("payload"), dict) else 返回体
        交易列表 = payload.get("transactions") or []
        for 交易 in 交易列表:
            结果 = 保存亚马逊财务交易(
                交易,
                店铺,
                站点id,
                SP_API站点区域.get(站点id, ""),
                同步类型,
                配置行.get("国家"),
            )
            汇总["新建"] += 结果["created"]
            汇总["更新"] += 结果["updated"]
            汇总["未变化"] += 结果["unchanged"]
            汇总["交易"] += 1
        frappe.db.commit()
        汇总["分页"] += 1
        下一页 = payload.get("nextToken")
        if not 下一页:
            break
        if 下一页 in 已见令牌:
            raise RuntimeError("Finances API返回了重复的nextToken，已停止以避免死循环")
        已见令牌.add(下一页)
    return 汇总


def _计算下次增量时间(当前时间, 间隔分钟):
    return frappe.utils.add_to_date(当前时间, minutes=max(cint(间隔分钟), 1))


def _计算下次核对时间(当前时间, 间隔天数):
    当前时间 = get_datetime(当前时间)
    return 当前时间.replace(hour=2, minute=59, second=0, microsecond=0) + timedelta(
        days=max(cint(间隔天数), 1)
    )


def _首次核对时间(当前时间):
    """首次定期核对安排在下一个系统时区02:59，避免启用后立即连续跑5组大窗口。"""
    当前时间 = get_datetime(当前时间)
    候选 = 当前时间.replace(hour=2, minute=59, second=0, microsecond=0)
    return 候选 if 候选 > 当前时间 else 候选 + timedelta(days=1)


def _整体下次时间(配置行, 覆盖=None, 当前时间=None):
    覆盖 = 覆盖 or {}
    当前时间 = 当前时间 or now_datetime()
    def 取值(字段):
        return 覆盖.get(字段, 配置行.get(字段))
    候选 = [取值("增量下次同步时间") or 当前时间]
    for 前缀 in ("7天", "14天", "30天", "90天", "180天"):
        if cint(取值(f"启用{前缀}核对")):
            候选.append(取值(f"{前缀}下次核对时间") or 当前时间)
    return min(get_datetime(value) for value in 候选)


def _任务锁(行名称):
    return frappe.cache().lock(
        frappe.cache().make_key(f"fengjing:amazon-finances:{行名称}"),
        timeout=任务超时秒,
        blocking_timeout=0,
    )


def _历史待执行键(行名称):
    return f"fengjing:amazon-finances-history-pending:{行名称}"


def _标记历史待执行(行名称):
    frappe.cache().set_value(
        _历史待执行键(行名称),
        1,
        expires_in_sec=30 * 24 * 60 * 60,
    )


def _历史正在等待(行名称):
    return cint(frappe.cache().get_value(_历史待执行键(行名称))) == 1


def _清除历史待执行(行名称):
    frappe.cache().delete_value(_历史待执行键(行名称))


def _任务入队(行名称, 同步类型):
    frappe.enqueue(
        执行亚马逊财务交易同步,
        queue="long",
        timeout=任务超时秒,
        enqueue_after_commit=True,
        job_id=f"amazon-finances-{行名称}-{同步类型}",
        deduplicate=True,
        配置行名称=行名称,
        同步类型=同步类型,
    )


@frappe.whitelist()
def 启动亚马逊历史财务交易同步(配置行名称):
    _, 行 = _取得配置行(配置行名称)
    if not 行.get("历史同步开始时间") or not 行.get("历史同步结束时间"):
        frappe.throw("请先填写历史同步开始时间和结束时间")
    _标记历史待执行(配置行名称)
    if 行.get("同步状态") == "同步中":
        return {
            "status": "waiting",
            "message": f"当前正在执行{行.get('当前执行类型') or '其他财务同步'}，历史交易已排队，完成后会自动继续。",
        }
    # 已完成后再手工点击，视为从起点重新核对；失败或暂停则从断点续跑。
    更新 = {
        "同步状态": "等待执行",
        "当前执行类型": "历史交易",
        "最近错误": "",
        "下次运行时间": now_datetime(),
    }
    if str(行.get("同步状态") or "") == "成功" and 行.get("历史已完整同步到"):
        更新.update({"历史已完整同步到": None, "历史同步进度": 0, "历史同步摘要": ""})
    _更新配置状态(配置行名称, **更新)
    _任务入队(配置行名称, "历史交易")
    return {"status": "queued", "message": "Amazon历史财务交易同步已进入后台队列"}


def 执行亚马逊财务交易同步(配置行名称, 同步类型):
    lock = _任务锁(配置行名称)
    if not lock.acquire(blocking=False):
        return {"status": "busy", "message": "该店铺已有Amazon财务同步任务运行"}
    开始时间 = now_datetime()
    汇总 = {"新建": 0, "更新": 0, "未变化": 0, "交易": 0, "分页": 0, "窗口": 0}
    try:
        _更新配置状态(
            配置行名称,
            同步状态="同步中",
            当前执行类型=同步类型,
            上次运行时间=开始时间,
            最近执行时间=开始时间,
            最近错误="",
        )
        主表, 行 = _取得配置行(配置行名称)
        api行 = _匹配api配置(主表, 行)

        if 同步类型 == "历史交易":
            while True:
                _, 行 = _取得配置行(配置行名称)
                计划 = _规划历史窗口(行)
                if 计划["status"] != "ready":
                    最终状态 = "成功" if 计划["status"] == "complete" else "等待执行"
                    汇总文本 = json.dumps({"状态": 最终状态, **汇总}, ensure_ascii=False)
                    _更新配置状态(
                        配置行名称,
                        同步状态=最终状态,
                        当前执行类型="",
                        历史同步进度=100 if 计划["status"] == "complete" else 计划["progress"],
                        历史同步摘要=汇总文本,
                        上次运行结果=汇总文本,
                        最近完成时间=now_datetime(),
                        下次运行时间=(
                            frappe.utils.add_to_date(now_datetime(), minutes=15)
                            if 计划["status"] == "waiting" else now_datetime()
                        ),
                    )
                    if 计划["status"] == "complete":
                        _清除历史待执行(配置行名称)
                    return {"status": 计划["status"], "summary": 汇总}
                本段 = _同步查询窗口(行, api行, 同步类型, 计划["start"], 计划["end"])
                for key in ("新建", "更新", "未变化", "交易", "分页"):
                    汇总[key] += 本段[key]
                汇总["窗口"] += 1
                总秒 = max((计划["target_end"] - 计划["target_start"]).total_seconds(), 1)
                进度 = min(max((计划["end"] - 计划["target_start"]).total_seconds() / 总秒 * 100, 0), 100)
                _更新配置状态(
                    配置行名称,
                    历史已完整同步到=_utc转系统时间(计划["end"]),
                    历史同步进度=进度,
                    历史同步摘要=json.dumps(汇总, ensure_ascii=False),
                )

        _, 行 = _取得配置行(配置行名称)
        结束utc = datetime.now(timezone.utc) - timedelta(minutes=安全延迟分钟)
        if 同步类型 == "15分钟增量":
            基准 = 行.get("增量最后完整同步时间") or 行.get("历史同步结束时间")
            开始utc = (_系统时间转utc(基准) if 基准 else 结束utc - timedelta(days=7)) - timedelta(minutes=增量重叠分钟)
        else:
            天数 = {"7天核对": 7, "14天核对": 14, "30天核对": 30, "90天核对": 90, "180天核对": 180}[同步类型]
            开始utc = 结束utc - timedelta(days=天数)
        本段 = _同步查询窗口(行, api行, 同步类型, 开始utc, 结束utc)
        汇总.update(本段)
        汇总["窗口"] = 1
        现在 = now_datetime()
        更新字段 = {
            "同步状态": "成功",
            "当前执行类型": "",
            "最近完成时间": 现在,
            "上次运行结果": json.dumps({"状态": "成功", **汇总}, ensure_ascii=False),
        }
        if 同步类型 == "15分钟增量":
            下次 = _计算下次增量时间(现在, 行.get("增量同步间隔分钟") or 15)
            更新字段.update({
                "增量最后完整同步时间": _utc转系统时间(结束utc),
                "增量下次同步时间": 下次,
                "增量同步摘要": json.dumps(汇总, ensure_ascii=False),
            })
        else:
            前缀 = 同步类型.replace("核对", "")
            下次 = _计算下次核对时间(现在, 行.get(f"{前缀}核对间隔天数") or 1)
            更新字段.update({f"{前缀}最后核对时间": 现在, f"{前缀}下次核对时间": 下次})
        更新字段["下次运行时间"] = _整体下次时间(行, 更新字段, 现在)
        _更新配置状态(配置行名称, **更新字段)
        return {"status": "success", "summary": 汇总}
    except Exception as exc:
        _更新配置状态(
            配置行名称,
            同步状态="失败",
            当前执行类型="",
            最近完成时间=now_datetime(),
            最近错误=str(exc)[:2000],
            上次运行结果=f"失败：{str(exc)[:1800]}",
            下次运行时间=frappe.utils.add_to_date(now_datetime(), minutes=15),
        )
        frappe.logger("amazon_finances", allow_site=True).exception(
            "Amazon财务交易同步失败：配置行=%s，类型=%s", 配置行名称, 同步类型
        )
        raise
    finally:
        try:
            lock.release()
        except Exception:
            pass


def 定时执行亚马逊财务交易同步():
    """每分钟检查到期任务，每个店铺每次最多入队一种同步。"""
    主表 = frappe.get_single(配置主表)
    现在 = now_datetime()
    for 行 in 主表.get(配置表字段) or []:
        try:
            if 行.get("同步状态") == "同步中":
                continue
            if 行.get("同步状态") == "失败" and 行.get("下次运行时间") and get_datetime(行.get("下次运行时间")) > 现在:
                continue
            _匹配api配置(主表, 行)
            if _历史正在等待(行.name):
                if (
                    行.get("同步状态") == "等待执行"
                    and 行.get("下次运行时间")
                    and get_datetime(行.get("下次运行时间")) > 现在
                ):
                    continue
                _更新配置状态(
                    行.name,
                    同步状态="等待执行",
                    当前执行类型="历史交易",
                )
                _任务入队(行.name, "历史交易")
                continue
            if (
                行.get("同步状态") == "等待执行"
                and 行.get("历史同步开始时间")
                and 行.get("历史同步结束时间")
                and (
                    not 行.get("下次运行时间")
                    or get_datetime(行.get("下次运行时间")) <= 现在
                )
            ):
                _任务入队(行.name, "历史交易")
                continue
            if not cint(行.get("启用自动同步")):
                continue
            if not 行.get("增量下次同步时间") or get_datetime(行.get("增量下次同步时间")) <= 现在:
                _任务入队(行.name, "15分钟增量")
                continue
            初始化时间 = {}
            for 前缀 in ("7天", "14天", "30天", "90天", "180天"):
                if cint(行.get(f"启用{前缀}核对")) and not 行.get(f"{前缀}下次核对时间"):
                    初始化时间[f"{前缀}下次核对时间"] = _首次核对时间(现在)
            if 初始化时间:
                候选时间 = list(初始化时间.values())
                if 行.get("增量下次同步时间"):
                    候选时间.append(get_datetime(行.get("增量下次同步时间")))
                初始化时间["下次运行时间"] = min(候选时间)
                _更新配置状态(行.name, **初始化时间)
                continue
            for 前缀 in ("7天", "14天", "30天", "90天", "180天"):
                if not cint(行.get(f"启用{前缀}核对")):
                    continue
                下次 = 行.get(f"{前缀}下次核对时间")
                if not 下次 or get_datetime(下次) <= 现在:
                    _任务入队(行.name, f"{前缀}核对")
                    break
        except Exception:
            frappe.logger("amazon_finances", allow_site=True).exception(
                "Amazon财务交易定时任务入队失败：配置行=%s", 行.name
            )
