"""Read-only view of Amazon-Fulfilled Shipments report rows."""

from datetime import datetime

import frappe
from frappe.utils import cint


DOCTYPE = "Amazon FBA Fulfilled Shipment"
TABLE = "`tabAmazon FBA Fulfilled Shipment`"
MAPPING_DOCTYPE = "Fengjing - Product Corresponding Platform - Main Table"


def _month_window(month):
    start = datetime.strptime(month, "%Y-%m")
    end = datetime(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _attach_items(rows):
    skus = {row.seller_sku for row in rows if row.seller_sku}
    if not skus:
        return
    mappings = frappe.get_all(
        MAPPING_DOCTYPE,
        filters={"启用": 1, "平台sku": ["in", list(skus)]},
        fields=["平台sku", "物料id"], limit_page_length=0,
    )
    by_sku = {}
    for mapping in mappings:
        if mapping.物料id:
            by_sku.setdefault(mapping.平台sku, set()).add(mapping.物料id)
    item_ids = {next(iter(items)) for items in by_sku.values() if len(items) == 1}
    items = {item.name: item for item in frappe.get_all(
        "Item", filters={"name": ["in", list(item_ids)]},
        fields=["name", "item_name", "image"], limit_page_length=0,
    )} if item_ids else {}
    for row in rows:
        candidates = by_sku.get(row.seller_sku, set())
        item = items.get(next(iter(candidates))) if len(candidates) == 1 else None
        row.item_id = item.name if item else ""
        row.item_name = item.item_name if item else ""
        row.item_image = item.image if item else ""


@frappe.whitelist()
def get_analysis(month=None, store=None, search=None, page=1, page_size=30):
    if not frappe.has_permission(DOCTYPE, "read"):
        frappe.throw("没有查看亚马逊FBA已配送货件的权限", frappe.PermissionError)

    months = [row.month for row in frappe.db.sql(
        f"SELECT DISTINCT LEFT(shipment_at, 7) AS month FROM {TABLE} "
        "WHERE shipment_at IS NOT NULL ORDER BY month DESC", as_dict=True,
    )]
    stores = [row.amazon_store for row in frappe.db.sql(
        f"SELECT DISTINCT amazon_store FROM {TABLE} WHERE amazon_store != '' "
        "ORDER BY amazon_store", as_dict=True,
    )]
    requested_month = str(month or "")
    selected_month = requested_month if requested_month == "all" or requested_month in months else (months[0] if months else "all")
    selected_store = str(store or "") if store in stores else ""
    query = str(search or "").strip()[:120]

    clauses, args = [], []
    if selected_month != "all":
        start, end = _month_window(selected_month)
        clauses.append("shipment_at >= %s AND shipment_at < %s")
        args.extend([start, end])
    if selected_store:
        clauses.append("amazon_store = %s")
        args.append(selected_store)
    if query:
        clauses.append("(order_id LIKE %s OR merchant_order_id LIKE %s "
                       "OR seller_sku LIKE %s OR product_name LIKE %s "
                       "OR shipment_id LIKE %s OR fulfillment_center_id LIKE %s)")
        args.extend([f"%{query}%"] * 6)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    # The same seller-level report can be returned for several marketplace
    # requests. Keep every source document, but count a shipment item once.
    source = (
        f"(SELECT MIN(name) AS name, COUNT(*) AS source_count, "
        f"GROUP_CONCAT(DISTINCT amazon_store SEPARATOR ', ') AS source_stores "
        f"FROM {TABLE}{where} "
        "GROUP BY CASE WHEN shipment_id != '' AND shipment_item_id != '' "
        "THEN CONCAT(shipment_id, CHAR(31), shipment_item_id) ELSE name END) source"
    )
    unique_from = f"{source} JOIN {TABLE} row_data ON row_data.name = source.name"

    summary = frappe.db.sql(
        f"SELECT COUNT(*) AS records, COALESCE(SUM(source.source_count), 0) AS raw_records, "
        "COALESCE(SUM(row_data.quantity_shipped), 0) AS units, "
        "COUNT(DISTINCT NULLIF(row_data.order_id, '')) AS orders, "
        "COUNT(DISTINCT NULLIF(row_data.shipment_id, '')) AS shipments, "
        f"COUNT(DISTINCT NULLIF(row_data.seller_sku, '')) AS skus FROM {unique_from}",
        tuple(args), as_dict=True,
    )[0]
    page_size = min(max(cint(page_size), 1), 100)
    pages = max(1, (cint(summary.records) + page_size - 1) // page_size)
    page = min(max(cint(page), 1), pages)
    rows = frappe.db.sql(
        f"SELECT row_data.name, row_data.amazon_store, row_data.marketplace_id, "
        "row_data.cost_center, row_data.order_id, row_data.merchant_order_id, "
        "row_data.shipment_id, row_data.shipment_item_id, row_data.seller_sku, "
        "row_data.product_name, row_data.quantity_shipped, row_data.fulfillment_center_id, "
        "row_data.purchase_at, row_data.shipment_at, row_data.reporting_at, "
        "row_data.carrier, row_data.tracking_number, source.source_count, source.source_stores "
        f"FROM {unique_from} ORDER BY row_data.shipment_at DESC, row_data.name DESC LIMIT %s OFFSET %s",
        (*args, page_size, (page - 1) * page_size), as_dict=True,
    )
    _attach_items(rows)
    products = frappe.db.sql(
        f"SELECT row_data.seller_sku, MAX(row_data.product_name) AS product_name, "
        "COUNT(*) AS records, COALESCE(SUM(row_data.quantity_shipped), 0) AS units "
        f"FROM {unique_from} GROUP BY row_data.seller_sku "
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
