# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import hashlib
import json
from datetime import timezone
from zoneinfo import ZoneInfo

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, get_system_timezone, now_datetime


class AmazonFinancialTransaction(Document):
    pass


存储单据 = "Amazon Financial Transaction"

# 通过ISO代码查找ERPNext Country主键，避免受界面翻译语言影响。
MARKETPLACE_COUNTRY_CODES = {
    "ATVPDKIKX0DER": "us", "A2EUQ1WTGCTBG2": "ca",
    "A1AM78C64UM0Y8": "mx", "A2Q3Y263D00KWC": "br",
    # Financial Transactions API may label US off-Amazon activity with this ID.
    "A2ZV50J4W1RKNI": "us",
    "A28R8C7NBKEWEA": "ie", "A1RKKUPIHCS9HS": "es",
    "A1F83G8C2ARO7P": "gb", "A13V1IB3VIYZZH": "fr",
    "AMEN7PMS3EDWL": "be", "A1805IZSGTT6HS": "nl",
    "A1PA6795UKMFR9": "de", "APJ6JRA9NG5V4": "it",
    "A2NODRKZP88ZB9": "se", "AE08WJ6YKNBMC": "za",
    "A1C3SOZRARQ6R3": "pl", "ARBP9OOSHTCHU": "eg",
    "A33AVAJ2PDY3EV": "tr", "A17E79C6D8DWNP": "sa",
    "A2VIGQ35RCS4UG": "ae", "A21TJRUUN4KGV": "in",
    "A19VAU5U5O7RUS": "sg", "A39IBJ37TRP1C6": "au",
    "A1VC38T7YXB528": "jp",
}


def _utc转系统时间(时间值):
    if not 时间值:
        return None
    if 时间值.tzinfo is None:
        时间值 = 时间值.replace(tzinfo=timezone.utc)
    return 时间值.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)


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


def _取国家(站点id):
    """Marketplace ID是站点国家的唯一信任源，不接受人工覆盖。"""
    国家代码 = MARKETPLACE_COUNTRY_CODES.get(str(站点id or "").strip().upper())
    if not 国家代码:
        return None
    return frappe.db.get_value("Country", {"code": 国家代码}, "name")


def 修复亚马逊财务交易国家():
    """Migration helper: repair existing rows by Marketplace ID without touching raw JSON."""
    if not frappe.db.table_exists("Amazon Financial Transaction"):
        return
    for 站点id, 国家代码 in MARKETPLACE_COUNTRY_CODES.items():
        国家 = frappe.db.get_value("Country", {"code": 国家代码}, "name")
        if not 国家:
            continue
        frappe.db.sql(
            """
            UPDATE `tabAmazon Financial Transaction`
               SET country = %s
             WHERE UPPER(TRIM(COALESCE(marketplace_id, ''))) = %s
               AND COALESCE(country, '') != %s
            """,
            (国家, 站点id, 国家),
        )


def 修复亚马逊财务交易国家_v2():
    """补充修复Financial Transactions API返回的Non-Amazon US站点。"""
    修复亚马逊财务交易国家()


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


def 保存亚马逊财务交易(交易, 店铺, 配置站点id, api区域, 同步类型):
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
        "country": _取国家(站点id),
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
