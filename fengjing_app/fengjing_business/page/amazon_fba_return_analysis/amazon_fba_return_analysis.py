"""FBA customer return overview. Source marketplace labels are not trusted."""

from collections import Counter, defaultdict
import json

import frappe
from frappe.utils import cint, getdate

from fengjing_app.fengjing_business.page.amazon_finance.amazon_finance import (
    _release_relationships,
    _report_time,
)


RETURN_DOCTYPE = "Amazon FBA Customer Return"
FINANCE_DOCTYPE = "Amazon Financial Transaction"
ORDER_DOCTYPE = "Amazon order synchronization"
MAPPING_DOCTYPE = "Fengjing - Product Corresponding Platform - Main Table"
MAX_ROWS = 100000
RETURN_FIELDS = [
    "name", "amazon_store", "order_id", "seller_sku", "asin", "fnsku",
    "product_name", "return_at", "return_date_raw", "quantity",
    "fulfillment_center_id", "detailed_disposition", "reason", "status",
    "license_plate_number", "customer_comments", "fetched_at",
]
FINANCE_FIELDS = [
    "name", "transaction_id", "amazon_order_id", "amazon_order_item_id",
    "store", "marketplace_id", "transaction_type", "description",
    "transaction_status", "posted_date", "currency_code", "net_amount",
    "sku", "asin", "raw_json",
]


def _unique_returns():
    """Merge identical reports from different requests; retain within-report multiplicity."""
    rows = frappe.get_all(
        RETURN_DOCTYPE, fields=RETURN_FIELDS, order_by="return_at desc",
        limit_page_length=MAX_ROWS + 1,
    )
    truncated = len(rows) > MAX_ROWS
    groups = defaultdict(lambda: defaultdict(list))
    for row in rows[:MAX_ROWS]:
        identity = (
            row.order_id, row.seller_sku, row.return_date_raw or str(row.return_at),
            row.fulfillment_center_id, row.license_plate_number,
        )
        groups[identity][row.amazon_store].append(row)

    unique = []
    for stores in groups.values():
        selected_store = max(stores, key=lambda store: (
            len(stores[store]),
            sum(bool(row.status) + bool(row.reason) + bool(row.detailed_disposition)
                for row in stores[store]),
            max(str(row.fetched_at or "") for row in stores[store]),
        ))
        for row in stores[selected_store]:
            pacific, timezone = _report_time(row.return_at, row.return_date_raw)
            row.report_date = str(pacific or "")
            row.report_timezone = timezone or ""
            row.report_day = row.report_date[:10]
            row.report_month = row.report_date[:7]
            row.category = ("unknown" if not row.detailed_disposition else
                            "sellable" if row.detailed_disposition == "SELLABLE" else "unsellable")
            unique.append(row)
    unique.sort(key=lambda row: (row.report_date, row.name), reverse=True)
    return unique, truncated


def _item_info(rows):
    """Use an ERPNext item only when the SKU or ASIN maps unambiguously."""
    skus = {row.seller_sku for row in rows if row.seller_sku}
    asins = {row.asin for row in rows if row.asin}
    mapping_rows = []
    if skus:
        mapping_rows.extend(frappe.get_all(
            MAPPING_DOCTYPE,
            filters={"启用": 1, "平台sku": ["in", list(skus)]},
            fields=["平台sku", "平台asin", "物料id"], limit_page_length=0,
        ))
    if asins:
        mapping_rows.extend(frappe.get_all(
            MAPPING_DOCTYPE,
            filters={"启用": 1, "平台asin": ["in", list(asins)]},
            fields=["平台sku", "平台asin", "物料id"], limit_page_length=0,
        ))
    by_sku, by_asin = defaultdict(set), defaultdict(set)
    for mapping in mapping_rows:
        if mapping.物料id:
            if mapping.平台sku:
                by_sku[mapping.平台sku].add(mapping.物料id)
            if mapping.平台asin:
                by_asin[mapping.平台asin].add(mapping.物料id)
    item_ids = {item for group in (*by_sku.values(), *by_asin.values()) for item in group}
    items = {item.name: item for item in frappe.get_all(
        "Item", filters={"name": ["in", list(item_ids)]},
        fields=["name", "item_name", "image"], limit_page_length=0,
    )} if item_ids else {}
    for row in rows:
        candidates = by_sku.get(row.seller_sku) or by_asin.get(row.asin) or set()
        item = items.get(next(iter(candidates))) if len(candidates) == 1 else None
        row.item_id = item.name if item else ""
        row.item_name = item.item_name if item else ""
        row.item_image = item.image if item else ""


def _event_label(row):
    if row.transaction_type == "Shipment":
        return "销售交易"
    if row.transaction_type in {"Refund", "RefundEvent", "Chargeback", "ChargebackEvent"}:
        return "退款交易"
    if row.transaction_type == "RemovalShipment":
        return "移除 / 清算相关交易"
    if row.description == "FBADisposal":
        return "FBA 处置费"
    return "库存赔偿"


def _financial_events(order_ids):
    if not order_ids:
        return {}
    rows = frappe.get_all(
        FINANCE_DOCTYPE,
        filters={"amazon_order_id": ["in", list(order_ids)]},
        fields=FINANCE_FIELDS, order_by="posted_date asc", limit_page_length=0,
    )
    rows = [row for row in rows if row.transaction_type in {
        "Shipment", "Refund", "RefundEvent", "Chargeback", "ChargebackEvent",
        "RemovalShipment", "FBAInventoryReimbursement",
    } or row.description == "FBADisposal"]
    if not rows:
        return {}
    all_rows, release_parents, releases = _release_relationships(rows, FINANCE_FIELDS)
    originals = [row for row in rows if row.name not in release_parents]
    transaction_counts = Counter(
        (row.store, row.transaction_id or row.name, row.transaction_type)
        for row in originals
    )
    by_order = defaultdict(list)
    for row in originals:
        try:
            raw = json.loads(row.raw_json or "{}")
        except (TypeError, ValueError):
            raw = {}
        pacific, timezone = _report_time(row.posted_date, raw.get("postedDate"))
        repeated = transaction_counts[(row.store, row.transaction_id or row.name, row.transaction_type)] > 1
        by_order[row.amazon_order_id].append({
            "kind": "finance", "label": _event_label(row),
            "date": str(pacific or ""), "timezone": timezone or "",
            "month": str(pacific or "")[:7], "source": row.name,
            "description": row.description or "", "sku": row.sku or "",
            "amount": None if repeated else float(row.net_amount or 0),
            "currency": row.currency_code or "",
            "amount_note": "多商品交易，金额请查看原单" if repeated else "交易净额",
            "release_count": len(releases.get(row.name, [])),
            "marketplace_id": row.marketplace_id or "",
        })
    return by_order


def _return_event(row):
    return {
        "kind": "return", "label": "FBA 收到退货", "date": row.report_date,
        "timezone": row.report_timezone, "month": row.report_month,
        "source": row.name, "sku": row.seller_sku or "",
        "quantity": cint(row.quantity), "disposition": row.detailed_disposition or "",
        "status": row.status or "", "reason": row.reason or "",
    }


def _original_order_months(rows):
    """Use the Orders API purchase time, expressed in the finance page's Pacific timezone."""
    order_ids = {row.order_id for row in rows if row.order_id}
    if not order_ids:
        return {}
    orders = frappe.get_all(
        ORDER_DOCTYPE,
        filters={"amazon_order_id": ["in", list(order_ids)]},
        fields=["amazon_order_id", "purchase_date"],
        limit_page_length=0,
    )
    months = {}
    for order in orders:
        if not order.purchase_date:
            continue
        pacific, _ = _report_time(order.purchase_date)
        month = str(pacific or "")[:7]
        if month and (order.amazon_order_id not in months or month < months[order.amazon_order_id]):
            months[order.amazon_order_id] = month
    return months


@frappe.whitelist()
def get_finance_return_data(date_from=None, date_to=None, search=None, page=1, page_size=20):
    """Return a deduplicated FBA receipt list for the finance page's Pacific dates."""
    start_day = str(getdate(date_from)) if date_from else ""
    end_day = str(getdate(date_to)) if date_to else ""
    if start_day and end_day and start_day > end_day:
        frappe.throw("退货开始日期不能晚于结束日期")
    all_rows, truncated = _unique_returns()
    query = str(search or "").strip().lower()
    rows = [row for row in all_rows if
            (not start_day or row.report_day >= start_day) and
            (not end_day or row.report_day <= end_day) and
            (not query or query in " ".join(str(row.get(key) or "") for key in (
                "order_id", "seller_sku", "asin", "fnsku", "product_name",
            )).lower())]
    page_size = min(max(cint(page_size), 1), 100)
    pages = max(1, (len(rows) + page_size - 1) // page_size)
    page = min(max(cint(page), 1), pages)
    visible = rows[(page - 1) * page_size:page * page_size]
    _item_info(visible)
    order_months = _original_order_months(visible)
    return {
        "summary": {
            "records": len(rows),
            "units": sum(cint(row.quantity) for row in rows),
            "orders": len({row.order_id for row in rows if row.order_id}),
            "sellable": sum(cint(row.quantity) for row in rows if row.category == "sellable"),
            "unsellable": sum(cint(row.quantity) for row in rows if row.category == "unsellable"),
            "unknown": sum(cint(row.quantity) for row in rows if row.category == "unknown"),
            "liquidation": sum(cint(row.quantity) for row in rows if row.status == "IMMEDIATE_LIQUIDATION"),
            "returned_to_inventory": sum(cint(row.quantity) for row in rows if row.status == "Unit returned to inventory"),
        },
        "rows": [{**{key: row.get(key) for key in (
            "name", "order_id", "seller_sku", "asin", "fnsku", "product_name",
            "quantity", "fulfillment_center_id", "detailed_disposition",
            "reason", "status", "item_id", "item_name", "item_image",
            "report_date", "report_timezone",
        )}, "order_month": order_months.get(row.order_id, "")} for row in visible],
        "pagination": {"page": page, "pages": pages, "total": len(rows), "page_size": page_size},
        "truncated": truncated,
    }


@frappe.whitelist()
def get_analysis(month=None, category=None, search=None, page=1, page_size=20):
    all_rows, truncated = _unique_returns()
    _item_info(all_rows)
    available_months = sorted({row.report_month for row in all_rows if row.report_month}, reverse=True)
    selected_month = month if month in available_months or month == "all" else (available_months[0] if available_months else "")
    query = str(search or "").strip().lower()
    base = [row for row in all_rows if (selected_month == "all" or row.report_month == selected_month)
            and (not query or query in " ".join(str(row.get(key) or "") for key in (
                "order_id", "seller_sku", "asin", "fnsku", "product_name", "item_id", "item_name",
            )).lower())]
    categories = {
        "all": len(base),
        "sellable": sum(row.category == "sellable" for row in base),
        "unsellable": sum(row.category == "unsellable" for row in base),
        "unknown": sum(row.category == "unknown" for row in base),
        "liquidation": sum(row.status == "IMMEDIATE_LIQUIDATION" for row in base),
        "disposal": sum(row.status == "IMMEDIATE_DISPOSAL" for row in base),
    }
    selected_category = category if category in categories else "all"
    filtered = [row for row in base if selected_category == "all"
                or (selected_category in {"sellable", "unsellable", "unknown"} and row.category == selected_category)
                or (selected_category == "liquidation" and row.status == "IMMEDIATE_LIQUIDATION")
                or (selected_category == "disposal" and row.status == "IMMEDIATE_DISPOSAL")]

    daily = defaultdict(lambda: {"sellable": 0, "unsellable": 0, "unknown": 0})
    statuses = Counter()
    products = {}
    for row in filtered:
        quantity = cint(row.quantity)
        daily[row.report_day][row.category] += quantity
        statuses[row.status or "未提供"] += quantity
        key = row.seller_sku or row.fnsku or "未提供 SKU"
        product = products.setdefault(key, {
            "sku": key, "asin": row.asin or "", "product_name": row.product_name or "",
            "item_id": row.item_id, "item_name": row.item_name,
            "image": row.item_image, "returns": 0, "quantity": 0,
            "sellable": 0, "unsellable": 0, "unknown": 0,
        })
        product["returns"] += 1
        product["quantity"] += quantity
        product[row.category] += quantity

    by_order = defaultdict(list)
    for row in filtered:
        by_order[row.order_id or f"未提供订单-{row.name}"].append(row)
    ordered = sorted(by_order.items(), key=lambda pair: max(row.report_date for row in pair[1]), reverse=True)
    page = max(cint(page), 1)
    page_size = min(max(cint(page_size), 1), 100)
    pages = max(1, (len(ordered) + page_size - 1) // page_size)
    page = min(page, pages)
    selected_orders = ordered[(page - 1) * page_size:page * page_size]
    finance = _financial_events({order_id for order_id, _ in selected_orders if not order_id.startswith("未提供订单-")})
    all_by_order = defaultdict(list)
    for row in all_rows:
        all_by_order[row.order_id].append(row)
    orders = []
    for order_id, current_returns in selected_orders:
        timeline = [_return_event(row) for row in all_by_order[order_id]] if not order_id.startswith("未提供订单-") else [_return_event(row) for row in current_returns]
        timeline.extend(finance.get(order_id, []))
        timeline.sort(key=lambda event: (event["date"], event["kind"]))
        marketplaces = {event["marketplace_id"] for event in timeline if event.get("marketplace_id")}
        orders.append({
            "order_id": order_id if not order_id.startswith("未提供订单-") else "",
            "marketplace_id": next(iter(marketplaces)) if len(marketplaces) == 1 else "",
            "marketplace_source": "财务订单" if len(marketplaces) == 1 else "待核实",
            "quantity": sum(cint(row.quantity) for row in current_returns),
            "returns": [
                {key: row.get(key) for key in (
                    "name", "order_id", "seller_sku", "asin", "fnsku", "product_name",
                    "quantity", "fulfillment_center_id", "detailed_disposition",
                    "reason", "status", "item_id", "item_name", "item_image",
                    "report_date", "report_timezone",
                )} for row in current_returns
            ],
            "timeline": timeline,
        })

    return {
        "available_months": available_months, "selected_month": selected_month,
        "selected_category": selected_category, "categories": categories,
        "summary": {
            "records": len(filtered), "units": sum(cint(row.quantity) for row in filtered),
            "orders": len(by_order),
            "sellable": sum(cint(row.quantity) for row in filtered if row.category == "sellable"),
            "unsellable": sum(cint(row.quantity) for row in filtered if row.category == "unsellable"),
            "unknown": sum(cint(row.quantity) for row in filtered if row.category == "unknown"),
            "liquidation": sum(cint(row.quantity) for row in filtered if row.status == "IMMEDIATE_LIQUIDATION"),
            "disposal": sum(cint(row.quantity) for row in filtered if row.status == "IMMEDIATE_DISPOSAL"),
        },
        "daily": [{"day": day, **daily[day]} for day in sorted(daily)],
        "statuses": [{"status": label, "quantity": quantity} for label, quantity in statuses.most_common()],
        "products": sorted(products.values(), key=lambda row: (row["quantity"], row["returns"]), reverse=True),
        "orders": orders,
        "pagination": {"page": page, "pages": pages, "total": len(ordered), "page_size": page_size},
        "source_rows": len(all_rows), "truncated": truncated,
    }
