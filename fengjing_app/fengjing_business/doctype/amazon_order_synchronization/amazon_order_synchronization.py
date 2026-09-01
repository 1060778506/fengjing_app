# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import hashlib
import json
from datetime import timezone

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, get_system_timezone, now_datetime
from zoneinfo import ZoneInfo


class Amazonordersynchronization(Document):
	pass


MARKETPLACE_COUNTRIES = {
    "ATVPDKIKX0DER": "美国", "A2EUQ1WTGCTBG2": "加拿大",
    "A1AM78C64UM0Y8": "墨西哥", "A2Q3Y263D00KWC": "巴西",
    "A28R8C7NBKEWEA": "爱尔兰", "A1RKKUPIHCS9HS": "西班牙",
    "A1F83G8C2ARO7P": "英国", "A13V1IB3VIYZZH": "法国",
    "AMEN7PMS3EDWL": "比利时", "A1805IZSGTT6HS": "荷兰",
    "A1PA6795UKMFR9": "德国", "APJ6JRA9NG5V4": "意大利",
    "A2NODRKZP88ZB9": "瑞典", "AE08WJ6YKNBMC": "南非",
    "A1C3SOZRARQ6R3": "波兰", "ARBP9OOSHTCHU": "埃及",
    "A33AVAJ2PDY3EV": "土耳其", "A17E79C6D8DWNP": "沙特阿拉伯",
    "A2VIGQ35RCS4UG": "阿联酋", "A21TJRUUN4KGV": "印度",
    "A19VAU5U5O7RUS": "新加坡", "A39IBJ37TRP1C6": "澳大利亚",
    "A1VC38T7YXB528": "日本",
}


def _amazon_time_to_system(value):
    """Parse an Amazon RFC3339 timestamp and return ERPNext system local time."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = get_datetime(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)


def _追加旧订单json到历史(doc, 新校验值):
    """Amazon原始JSON变化时，把覆盖前的旧版本追加到历史JSON数组。"""
    旧json文本 = str(doc.get("raw_json") or "").strip()
    旧校验值 = str(doc.get("raw_json_hash") or "").strip()
    if not 旧json文本 or not 旧校验值 or 旧校验值 == 新校验值:
        return

    try:
        历史数组 = json.loads(doc.get("raw_json_history") or "[]")
        if not isinstance(历史数组, list):
            历史数组 = []
    except (TypeError, ValueError, json.JSONDecodeError):
        # 历史字段即使曾被写入异常文本，也不能阻断最新订单同步。
        历史数组 = []

    try:
        旧json内容 = json.loads(旧json文本)
    except (TypeError, ValueError, json.JSONDecodeError):
        旧json内容 = 旧json文本

    历史数组.append({
        "archived_at": str(now_datetime()),
        "order_status": doc.get("order_status"),
        "source_updated_at": str(doc.get("source_updated_at") or ""),
        "sync_type": doc.get("sync_type"),
        "raw_json_hash": 旧校验值,
        "raw_json": 旧json内容,
    })
    doc.raw_json_history = json.dumps(
        历史数组, ensure_ascii=False, sort_keys=True, indent=2
    )


def 保存亚马逊订单(订单, 店铺, 站点id, api区域, 同步类型):
    """Create or update one order by AmazonOrderId and preserve its raw JSON."""
    if not isinstance(订单, dict):
        raise ValueError("Amazon订单数据必须是JSON对象")
    订单号 = str(订单.get("AmazonOrderId") or 订单.get("orderId") or "").strip()
    if not 订单号:
        raise ValueError("Amazon订单缺少AmazonOrderId")

    站点id = str(
        订单.get("MarketplaceId")
        or (订单.get("salesChannel") or {}).get("marketplaceId")
        or 站点id
        or ""
    ).strip().upper()
    原始文本 = json.dumps(订单, ensure_ascii=False, sort_keys=True, indent=2)
    原始校验值 = hashlib.sha256(原始文本.encode("utf-8")).hexdigest()
    履约 = 订单.get("fulfillment") or {}
    发货窗口 = 履约.get("shipByWindow") or {}
    送达窗口 = 履约.get("deliverByWindow") or {}
    金额 = (
        订单.get("OrderTotal")
        or 订单.get("orderTotal")
        or (订单.get("proceeds") or {}).get("grandTotal")
        or {}
    )
    地址 = (
        订单.get("ShippingAddress")
        or 订单.get("shippingAddress")
        or (订单.get("recipient") or {}).get("deliveryAddress")
        or {}
    )
    项目 = 订单.get("orderItems") or []
    已发货数量 = sum(cint((item.get("fulfillment") or {}).get("quantityFulfilled")) for item in 项目)
    未发货数量 = sum(cint((item.get("fulfillment") or {}).get("quantityUnfulfilled")) for item in 项目)
    程序标记 = set(订单.get("programs") or [])
    关联订单 = 订单.get("associatedOrders") or []

    数据 = {
        "amazon_order_id": 订单号,
        "marketplace_id": 站点id,
        "store": 店铺,
        "country": MARKETPLACE_COUNTRIES.get(站点id, 站点id),
        "api_region": api区域,
        "sync_type": 同步类型,
        "order_status": 订单.get("OrderStatus") or 履约.get("fulfillmentStatus"),
        "fulfillment_channel": 订单.get("FulfillmentChannel") or 履约.get("fulfilledBy"),
        "sales_channel": 订单.get("SalesChannel") or (订单.get("salesChannel") or {}).get("channelName"),
        "order_type": 订单.get("OrderType"),
        "is_prime": cint(订单.get("IsPrime") or "PRIME" in 程序标记),
        "is_business_order": cint(订单.get("IsBusinessOrder") or "AMAZON_BUSINESS" in 程序标记),
        "is_replacement_order": cint(订单.get("IsReplacementOrder") or any(
            row.get("associationType") == "REPLACEMENT_ORIGINAL_ID" for row in 关联订单
        )),
        "purchase_date": _amazon_time_to_system(订单.get("PurchaseDate") or 订单.get("createdTime")),
        "last_update_date": _amazon_time_to_system(订单.get("LastUpdateDate") or 订单.get("lastUpdatedTime")),
        "earliest_ship_date": _amazon_time_to_system(
            订单.get("EarliestShipDate") or 发货窗口.get("earliestDateTime")
        ),
        "latest_ship_date": _amazon_time_to_system(
            订单.get("LatestShipDate") or 发货窗口.get("latestDateTime")
        ),
        "earliest_delivery_date": _amazon_time_to_system(
            订单.get("EarliestDeliveryDate") or 送达窗口.get("earliestDateTime")
        ),
        "latest_delivery_date": _amazon_time_to_system(
            订单.get("LatestDeliveryDate") or 送达窗口.get("latestDateTime")
        ),
        "currency_code": 金额.get("CurrencyCode") or 金额.get("currencyCode"),
        "order_total": flt(金额.get("Amount") or 金额.get("amount")),
        "number_of_items_shipped": cint(订单.get("NumberOfItemsShipped") or 已发货数量),
        "number_of_items_unshipped": cint(订单.get("NumberOfItemsUnshipped") or 未发货数量),
        "ship_service_level": 订单.get("ShipServiceLevel") or 履约.get("fulfillmentServiceLevel"),
        "shipping_country_code": 地址.get("CountryCode") or 地址.get("countryCode"),
        "shipping_state_or_region": 地址.get("StateOrRegion") or 地址.get("stateOrRegion"),
        "shipping_city": 地址.get("City") or 地址.get("city"),
        "shipping_postal_code": 地址.get("PostalCode") or 地址.get("postalCode"),
        "fetched_at": now_datetime(),
        "source_updated_at": _amazon_time_to_system(订单.get("LastUpdateDate") or 订单.get("lastUpdatedTime")),
        "raw_json_hash": 原始校验值,
        "sync_status": "成功",
        "last_error": None,
        "raw_json": 原始文本,
    }
    数据 = {key: value for key, value in 数据.items() if value is not None}

    if frappe.db.exists("Amazon order synchronization", 订单号):
        doc = frappe.get_doc("Amazon order synchronization", 订单号)
        _追加旧订单json到历史(doc, 原始校验值)
        doc.update(数据)
        doc.save(ignore_permissions=True)
        return "updated", doc.name

    doc = frappe.get_doc({"doctype": "Amazon order synchronization", **数据})
    doc.insert(ignore_permissions=True)
    return "created", doc.name
