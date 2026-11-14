"""Read-only analysis of one imported AWD monthly fee batch."""

from collections import defaultdict
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import cint


BATCH = "Amazon AWD Fee Batch"
DETAIL = "Amazon AWD Fee Detail"
PAGE_SIZE = 100
KINDS = {"all", "inbound_processing", "outbound_processing", "other_processing", "transportation", "storage"}
DETAIL_FIELDS = [
    "name", "source_type", "source_row", "charge_type", "transaction_date",
    "fee_type", "inbound_order_id", "replenishment_order_id", "shipment_id",
    "tracking_id", "billing_order_id", "asin", "fnsku", "msku", "carrier_type", "facility_id",
    "destination_code", "storage_region", "average_used_capacity", "billed_volume",
    "box_count", "box_volume", "volume_unit", "rate",
    "rate_unit", "rate_type", "calculated_amount", "promotion_amount",
    "tax_amount", "charged_amount", "currency",
]


def _decimal(value):
    return Decimal(str(value or 0))


def _money(value):
    return float(value.quantize(Decimal("0.01")))


def _kind(row):
    if row.source_type == "Storage":
        return "storage"
    if row.source_type == "Transportation":
        return "transportation"
    charge_type = (row.charge_type or "").casefold()
    if charge_type == "inbound":
        return "inbound_processing"
    if charge_type == "outbound":
        return "outbound_processing"
    return "other_processing"


def _group(key, label, **extra):
    return {
        "key": key, "label": label, **extra,
        "processing": Decimal("0"), "transportation": Decimal("0"),
        "total": Decimal("0"), "processing_boxes": Decimal("0"),
        "transportation_boxes": Decimal("0"), "rows": 0, "skus": set(),
    }


def _add(group, row):
    amount = _decimal(row.charged_amount)
    group["total"] += amount
    group["rows"] += 1
    group["skus"].add(row.msku or row.fnsku or row.asin or "")
    if row.source_type == "Transportation":
        group["transportation"] += amount
        group["transportation_boxes"] += _decimal(row.box_count)
    else:
        group["processing"] += amount
        group["processing_boxes"] += _decimal(row.box_count)


def _finish(groups):
    result = []
    for group in groups.values():
        group["sku_count"] = len(group["skus"] - {""})
        group["processing_boxes"] = float(group["processing_boxes"])
        group["transportation_boxes"] = float(group["transportation_boxes"])
        del group["skus"]
        for key in ("processing", "transportation", "total"):
            group[key] = _money(group[key])
        result.append(group)
    return sorted(result, key=lambda row: (-row["total"], row["label"]))


def _matches(row, search):
    if not search:
        return True
    return any(search in str(row.get(field) or "").casefold() for field in (
        "inbound_order_id", "replenishment_order_id", "shipment_id", "tracking_id",
        "billing_order_id", "asin", "fnsku", "msku", "fee_type", "charge_type",
        "facility_id", "storage_region",
    ))


@frappe.whitelist()
def get_analysis(batch_name=None, kind="all", search=None, page=1):
    kind = str(kind or "all")
    if kind not in KINDS:
        frappe.throw(_("未知的费用筛选类型。"))
    page = max(1, cint(page))
    search = str(search or "").strip().casefold()
    if len(search) > 100:
        frappe.throw(_("搜索词最多 100 个字符。"))

    batches = frappe.get_list(
        BATCH,
        fields=["name", "period", "seller_customer_id", "status", "processing_total",
                "transportation_total", "storage_total", "total_amount", "row_count", "creation"],
        order_by="period desc, creation desc",
        limit_page_length=200,
    )
    batches = sorted(
        batches,
        key=lambda row: (row.period or "", row.status == "当前", str(row.creation or "")),
        reverse=True,
    )
    if not batches:
        return {"batches": [], "batch": None, "summary": None, "orders": [],
                "shipments": [], "skus": [], "storage": [], "details": [], "page": 1, "pages": 0}
    visible = {row.name for row in batches}
    if not batch_name:
        batch_name = next((row.name for row in batches if row.status == "当前"), batches[0].name)
    if batch_name not in visible:
        frappe.throw(_("批次不存在或无权查看。"), frappe.PermissionError)
    batch = frappe.get_doc(BATCH, batch_name)
    batch.check_permission("read")

    rows = frappe.get_all(
        DETAIL,
        filters={"parent": batch.name, "parenttype": BATCH, "parentfield": "details"},
        fields=DETAIL_FIELDS,
        order_by="idx asc",
        limit_page_length=50000,
    )
    kind_totals = defaultdict(lambda: Decimal("0"))
    for row in rows:
        kind_totals[_kind(row)] += _decimal(row.charged_amount)
    filtered = [row for row in rows if (kind == "all" or _kind(row) == kind) and _matches(row, search)]

    orders, shipments, skus, storage_groups = {}, {}, {}, {}
    total = Decimal("0")
    processing = Decimal("0")
    transportation = Decimal("0")
    storage = Decimal("0")
    for row in filtered:
        amount = _decimal(row.charged_amount)
        total += amount
        if row.source_type == "Storage":
            storage += amount
            storage_key = (row.billing_order_id or "", row.asin or "", row.fnsku or "",
                           row.msku or "", row.storage_region or "", row.rate, row.rate_unit or "")
            if storage_key not in storage_groups:
                storage_groups[storage_key] = {
                    "billing_order_id": row.billing_order_id or "",
                    "inbound_order_id": row.inbound_order_id or "",
                    "asin": row.asin or "", "fnsku": row.fnsku or "",
                    "msku": row.msku or "", "storage_region": row.storage_region or "",
                    "volume_unit": row.volume_unit or "", "rate": row.rate,
                    "rate_unit": row.rate_unit or "",
                    "average_used_capacity": Decimal(0), "billed_volume": Decimal(0),
                    "total": Decimal(0), "rows": 0,
                }
            group = storage_groups[storage_key]
            group["average_used_capacity"] += _decimal(row.average_used_capacity)
            group["billed_volume"] += _decimal(row.billed_volume)
            group["total"] += amount
            group["rows"] += 1
            continue
        if row.source_type == "Transportation":
            transportation += amount
        else:
            processing += amount
        order_type = "AWD入库" if _kind(row) == "inbound_processing" else "AWD→FBA"
        order_id = (row.inbound_order_id if order_type == "AWD入库"
                    else row.replenishment_order_id) or "未提供"
        order_key = f"{order_type}:{order_id}"
        if order_key not in orders:
            orders[order_key] = _group(order_key, order_id, order_type=order_type)
        _add(orders[order_key], row)

        shipment_id = row.shipment_id or row.tracking_id or "未提供"
        shipment_key = f"{order_key}:{shipment_id}"
        if shipment_key not in shipments:
            shipments[shipment_key] = _group(
                shipment_key, row.tracking_id or shipment_id,
                shipment_id=row.shipment_id or "", tracking_id=row.tracking_id or "",
                order_id=order_id, order_type=order_type,
            )
        _add(shipments[shipment_key], row)

        sku_key = (row.msku or "", row.fnsku or "", row.asin or "")
        if sku_key not in skus:
            skus[sku_key] = _group(
                "|".join(sku_key), row.msku or row.fnsku or row.asin or "未提供",
                msku=row.msku or "", fnsku=row.fnsku or "", asin=row.asin or "",
            )
        _add(skus[sku_key], row)

    filtered.sort(key=lambda row: (str(row.transaction_date or ""), row.source_type or "", row.source_row or 0))
    pages = (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE
    page = min(page, max(pages, 1))
    detail_page = filtered[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    storage_rows = []
    for group in storage_groups.values():
        group["average_used_capacity"] = float(group["average_used_capacity"])
        group["billed_volume"] = float(group["billed_volume"])
        group["total"] = _money(group["total"])
        storage_rows.append(group)
    storage_rows.sort(key=lambda row: (-row["total"], row["asin"], row["msku"]))
    return {
        "batches": batches,
        "batch": {
            "name": batch.name, "period": batch.period, "seller_customer_id": batch.seller_customer_id,
            "status": batch.status, "processing_file": batch.processing_file,
            "transportation_file": batch.transportation_file, "storage_file": batch.storage_file,
            "processing_total": batch.processing_total, "transportation_total": batch.transportation_total,
            "storage_total": batch.storage_total,
            "total_amount": batch.total_amount, "row_count": batch.row_count,
        },
        "kind_totals": {key: _money(value) for key, value in kind_totals.items()},
        "summary": {
            "total": _money(total), "processing": _money(processing),
            "transportation": _money(transportation), "storage": _money(storage),
            "row_count": len(filtered), "order_count": len(orders),
            "shipment_count": len(shipments), "sku_count": len(skus),
            "storage_count": len(storage_rows),
            "matches_batch_total": kind == "all" and not search
            and total == _decimal(batch.total_amount),
        },
        "orders": _finish(orders),
        "shipments": _finish(shipments),
        "skus": _finish(skus),
        "storage": storage_rows,
        "details": [
            {**row, "transaction_date": str(row.transaction_date or ""),
             "kind": _kind(row)}
            for row in detail_page
        ],
        "page": page,
        "pages": pages,
    }
