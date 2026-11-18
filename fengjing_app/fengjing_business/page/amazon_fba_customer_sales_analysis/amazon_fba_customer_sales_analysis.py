"""Read-only analysis of Amazon FBA customer shipment sales report rows."""

import json
from datetime import datetime

import frappe
from frappe.utils import cint

from fengjing_app.fengjing_business.page.amazon_fba_shipment_analysis.amazon_fba_shipment_analysis import _attach_items
from fengjing_app.fengjing_business.page.amazon_finance.amazon_finance import _report_boundary, _report_time


DOCTYPE = "Amazon FBA Customer Shipment Sale"
TABLE = "`tabAmazon FBA Customer Shipment Sale`"


def _month_window(month):
    start = datetime.strptime(month, "%Y-%m")
    end = datetime(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return _report_boundary(start.date()), _report_boundary(end.date())


def _available_months():
    bounds = frappe.db.sql(
        f"SELECT MIN(shipment_at) AS first, MAX(shipment_at) AS last FROM {TABLE}",
        as_dict=True,
    )[0]
    if not bounds.first:
        return []
    first, _ = _report_time(bounds.first)
    last, _ = _report_time(bounds.last)
    cursor = datetime(first.year, first.month, 1)
    final = datetime(last.year, last.month, 1)
    months = []
    while cursor <= final:
        month = cursor.strftime("%Y-%m")
        start, end = _month_window(month)
        if frappe.db.sql(
            f"SELECT 1 FROM {TABLE} WHERE shipment_at >= %s AND shipment_at < %s LIMIT 1",
            (start, end),
        ):
            months.append(month)
        cursor = datetime(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return list(reversed(months))


@frappe.whitelist()
def get_analysis(month=None, store=None, search=None, page=1, page_size=30):
    if not frappe.has_permission(DOCTYPE, "read"):
        frappe.throw("没有查看亚马逊FBA已完成订单销售的权限", frappe.PermissionError)

    months = _available_months()
    stores = [row.amazon_store for row in frappe.db.sql(
        f"SELECT DISTINCT amazon_store FROM {TABLE} "
        "WHERE amazon_store IS NOT NULL AND amazon_store != '' ORDER BY amazon_store",
        as_dict=True,
    )]
    selected_month = month if month == "all" or month in months else (months[0] if months else "all")
    selected_store = store if store in stores or store == "pending" else ""
    query = str(search or "").strip()[:120]

    clauses, args = [], []
    if selected_month != "all":
        start, end = _month_window(selected_month)
        clauses.append("shipment_at >= %s AND shipment_at < %s")
        args.extend([start, end])
    if selected_store == "pending":
        clauses.append("attribution_status = '待分配'")
    elif selected_store:
        clauses.append("amazon_store = %s")
        args.append(selected_store)
    if query:
        clauses.append("(order_id LIKE %s OR seller_sku LIKE %s OR fnsku LIKE %s "
                       "OR asin LIKE %s OR fulfillment_center_id LIKE %s)")
        args.extend([f"%{query}%"] * 5)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""

    summary = frappe.db.sql(
        f"SELECT COUNT(*) AS records, COALESCE(SUM(quantity), 0) AS units, "
        "COUNT(DISTINCT NULLIF(order_id, '')) AS orders, "
        "COUNT(DISTINCT NULLIF(seller_sku, '')) AS skus, "
        "COALESCE(SUM(CASE WHEN attribution_status = '待分配' THEN 1 ELSE 0 END), 0) AS pending "
        f"FROM {TABLE}{where}", tuple(args), as_dict=True,
    )[0]
    page_size = min(max(cint(page_size), 1), 100)
    pages = max(1, (cint(summary.records) + page_size - 1) // page_size)
    page = min(max(cint(page), 1), pages)
    rows = frappe.db.sql(
        "SELECT name, amazon_store, attribution_status, marketplace_id, cost_center, "
        "order_id, seller_sku, fnsku, asin, quantity, shipment_at, fulfillment_center_id, "
        "currency, item_price_per_unit, shipping_price, gift_wrap_price, "
        "ship_city, ship_state, report_id, raw_json "
        f"FROM {TABLE}{where} ORDER BY shipment_at DESC, name DESC LIMIT %s OFFSET %s",
        (*args, page_size, (page - 1) * page_size), as_dict=True,
    )
    for row in rows:
        try:
            source = json.loads(row.raw_json or "{}")
        except (TypeError, ValueError):
            source = {}
        source_time = source.get("shipment-date") if isinstance(source, dict) else None
        row.report_shipment_at, row.report_timezone = _report_time(row.shipment_at, source_time)
        row.pop("raw_json", None)
    _attach_items(rows)
    products = frappe.db.sql(
        "SELECT seller_sku, MAX(asin) AS asin, COUNT(*) AS records, "
        "COALESCE(SUM(quantity), 0) AS units "
        f"FROM {TABLE}{where} GROUP BY seller_sku "
        "ORDER BY units DESC, records DESC LIMIT 10",
        tuple(args), as_dict=True,
    )
    _attach_items(products)
    return {
        "available_months": months, "selected_month": selected_month,
        "stores": stores, "selected_store": selected_store,
        "summary": summary, "products": products, "rows": rows,
        "pagination": {"page": page, "pages": pages, "total": cint(summary.records), "page_size": page_size},
    }
