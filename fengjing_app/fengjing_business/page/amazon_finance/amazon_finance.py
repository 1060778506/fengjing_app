from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import json
import re
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import cint, flt, get_datetime, get_system_timezone, getdate

from fengjing_app.fengjing_business.doctype.amazon_financial_transaction.amazon_financial_transaction import (
    _上下文值, _费用分类, _金额,
)


DOCTYPE = "Amazon Financial Transaction"
MAPPING_DOCTYPE = "Fengjing - Product Corresponding Platform - Main Table"
REPORT_TIMEZONE = ZoneInfo("America/Los_Angeles")
AMOUNT_FIELDS = [
    "total_amount", "item_amount", "product_sales", "product_sales_tax",
    "shipping_credits", "shipping_credits_tax", "promotional_rebates",
    "promotional_rebates_tax", "marketplace_withheld_tax", "selling_fees",
    "fba_fees", "other_transaction_fees", "other_amount", "net_amount",
]

DETAIL_GROUPS = (
    ("order", "订单"),
    ("refund", "退款"),
    ("storage", "仓储费"),
    ("logistics", "入库与物流"),
    ("promotion", "广告与推广"),
    ("service", "订阅与服务"),
    ("funds", "资金划转"),
    ("adjustment", "调整与赔偿"),
    ("other", "其他"),
)


def _money(value):
    return flt(value or 0, 2)


def _awd_storage_order_id(order_id):
    """The finance API may append -AWD to the report's storage billing ID."""
    return re.sub(r"-AWD$", "", str(order_id or "").strip(), flags=re.IGNORECASE)


def _attach_awd_fee_details(page_rows):
    """Show imported AWD report lines beneath a matching finance charge."""
    fee_types = {
        "AWDProcessingFee": "Processing",
        "AWDTransportationFee": "Transportation",
        "STARStorageBilling": "Storage",
    }
    finance_rows = [
        row for row in page_rows
        if row.description in fee_types and row.amazon_order_id
        and row.currency_code == "USD"
    ]
    if not finance_rows:
        return
    batches = frappe.get_list(
        "Amazon AWD Fee Batch",
        filters={"status": "当前"},
        fields=["name", "period"],
        limit_page_length=0,
    )
    if not batches:
        return
    batch_names = [batch.name for batch in batches]
    order_ids = list({row.amazon_order_id for row in finance_rows})
    storage_order_ids = list({
        candidate
        for row in finance_rows if fee_types[row.description] == "Storage"
        for candidate in (row.amazon_order_id, _awd_storage_order_id(row.amazon_order_id))
    })
    report_rows = frappe.get_all(
        "Amazon AWD Fee Detail",
        filters={
            "parent": ["in", batch_names],
            "parenttype": "Amazon AWD Fee Batch",
            "source_type": ["in", ["Processing", "Transportation"]],
            "replenishment_order_id": ["in", order_ids],
        },
        fields=[
            "parent", "source_type", "source_row", "transaction_date",
            "replenishment_order_id", "billing_order_id", "shipment_id", "tracking_id",
            "msku", "fnsku", "asin", "box_count", "billed_volume", "volume_unit",
            "storage_region", "rate", "rate_unit",
            "charged_amount",
        ],
        order_by="parent asc, source_row asc",
        limit_page_length=0,
    )
    if storage_order_ids:
        report_rows.extend(frappe.get_all(
            "Amazon AWD Fee Detail",
            filters={
                "parent": ["in", batch_names],
                "parenttype": "Amazon AWD Fee Batch",
                "source_type": "Storage",
                "billing_order_id": ["in", storage_order_ids],
            },
            fields=[
                "parent", "source_type", "source_row", "transaction_date",
                "replenishment_order_id", "billing_order_id", "shipment_id", "tracking_id",
                "msku", "fnsku", "asin", "box_count", "billed_volume", "volume_unit",
                "storage_region", "rate", "rate_unit", "charged_amount",
            ],
            order_by="parent asc, source_row asc",
            limit_page_length=0,
        ))
    groups = defaultdict(list)
    for detail in report_rows:
        order_id = (_awd_storage_order_id(detail.billing_order_id)
                    if detail.source_type == "Storage" else detail.replenishment_order_id)
        groups[(detail.parent, order_id, detail.source_type)].append(detail)
    candidates = defaultdict(list)
    for key, details in groups.items():
        candidates[(key[1], key[2])].append((key, details))
    matched = {}
    for row in finance_rows:
        amount = Decimal(str(-(row.net_amount or 0))).quantize(Decimal("0.01"))
        possible = [
            (key, details) for key, details in candidates[
                (_awd_storage_order_id(row.amazon_order_id)
                 if fee_types[row.description] == "Storage" else row.amazon_order_id,
                 fee_types[row.description])
            ]
            if sum((Decimal(str(detail.charged_amount or 0)) for detail in details), Decimal(0)).quantize(
                Decimal("0.01")
            ) == amount
        ]
        if len(possible) == 1:
            matched[row.name] = possible[0]
    repeated = Counter(key for key, _details in matched.values())
    periods = {batch.name: batch.period for batch in batches}
    for row in finance_rows:
        match = matched.get(row.name)
        if not match or repeated[match[0]] != 1:
            continue
        key, details = match
        row.awd_fee_details = [
            {
                "batch": key[0],
                "period": periods.get(key[0]),
                "source_type": detail.source_type,
                "report_date": str(detail.transaction_date or ""),
                "source_row": detail.source_row,
                "shipment_id": detail.shipment_id,
                "tracking_id": detail.tracking_id,
                "billing_order_id": detail.billing_order_id,
                "msku": detail.msku,
                "fnsku": detail.fnsku,
                "asin": detail.asin,
                "box_count": detail.box_count,
                "billed_volume": detail.billed_volume,
                "volume_unit": detail.volume_unit,
                "storage_region": detail.storage_region,
                "rate": detail.rate,
                "rate_unit": detail.rate_unit,
                "amount": -_money(detail.charged_amount),
            }
            for detail in details
        ]


def _report_boundary(value, following_day=False):
    """Convert a Pacific calendar date boundary to the stored site timezone."""
    day = getdate(value) + timedelta(days=1 if following_day else 0)
    pacific = datetime.combine(day, time.min, REPORT_TIMEZONE)
    return pacific.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)


def _report_time(stored_value, raw_value=None):
    """Prefer Amazon's original timestamp for historical and future records."""
    instant = None
    if raw_value:
        try:
            instant = datetime.fromisoformat(str(raw_value).strip().replace("Z", "+00:00"))
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=timezone.utc)
        except ValueError:
            instant = None
    if instant is None and stored_value:
        instant = get_datetime(stored_value).replace(tzinfo=ZoneInfo(get_system_timezone()))
    if instant is None:
        return None, None
    local = instant.astimezone(REPORT_TIMEZONE)
    return local.replace(tzinfo=None), local.tzname()


def _transaction_group(row):
    """Group Amazon's detailed event names into stable, accounting-friendly tabs."""
    transaction_type = str(row.get("transaction_type") or "")
    description = str(row.get("description") or "")
    value = f"{transaction_type} {description}"

    if transaction_type == "Shipment" and description == "Order Payment":
        return "order"
    if transaction_type in {"Refund", "RefundEvent", "Chargeback", "ChargebackEvent"}:
        return "refund"
    if re.search(r"StorageBilling|StorageFee|LongTermStorage|STARStorage", value, re.I):
        return "storage"
    if re.search(
        r"Inbound|Transportation|ShipmentInjection|AWDProcessing|RemovalShipment|FBADisposal",
        value,
        re.I,
    ):
        return "logistics"
    if re.search(r"ProductAds|Advertising|Coupon|Promotion", value, re.I):
        return "promotion"
    if transaction_type in {"Transfer", "DebtRecovery"} or re.search(
        r"Disbursement|DebtPayment|Reserve", value, re.I
    ):
        return "funds"
    if transaction_type in {"Adjustment", "FBAInventoryReimbursement", "Retrocharge"} or re.search(
        r"Adjustment|Reimbursement|Clawback|WAREHOUSE_LOST|WAREHOUSE_DAMAGE|MISSING_FROM_INBOUND",
        value,
        re.I,
    ):
        return "adjustment"
    if transaction_type == "ServiceFee" or re.search(r"Subscription|ServiceFee", value, re.I):
        return "service"
    return "other"


def _filters(raw=None):
    if isinstance(raw, str):
        raw = frappe.parse_json(raw)
    return frappe._dict(raw or {})


def _mapping_rows():
    return frappe.get_all(
        MAPPING_DOCTYPE,
        filters={"启用": 1},
        fields=["name", "店铺", "站点id", "平台asin", "平台sku", "物料id", "物料名称"],
        limit_page_length=0,
    )


def _resolve_mapping(row, mappings):
    store, site = row.get("store") or "", row.get("marketplace_id") or ""
    asin, sku = (row.get("asin") or "").upper(), (row.get("sku") or "").upper()
    candidates = []
    for m in mappings:
        if m.店铺 != store or (m.站点id or "") != site:
            continue
        ma, ms = (m.平台asin or "").upper(), (m.平台sku or "").upper()
        score = 0
        if asin and sku and ma == asin and ms == sku:
            score = 4
        elif sku and ms == sku:
            score = 3
        elif asin and ma == asin:
            score = 2
        if score:
            candidates.append((score, m))
    return max(candidates, key=lambda x: x[0])[1] if candidates else None


def _build_db_filters(f):
    filters = {}
    if f.date_from and f.date_to:
        filters["posted_date"] = ["between", [
            _report_boundary(f.date_from),
            _report_boundary(f.date_to, following_day=True) - timedelta(microseconds=1),
        ]]
    elif f.date_from:
        filters["posted_date"] = [">=", _report_boundary(f.date_from)]
    elif f.date_to:
        filters["posted_date"] = ["<=", _report_boundary(f.date_to, following_day=True) - timedelta(microseconds=1)]
    for key in ("store", "marketplace_id", "country", "currency_code", "transaction_type", "transaction_status"):
        if f.get(key):
            filters[key] = f.get(key)
    return filters


def _transaction_links(row):
    """Read Amazon's explicit deferred/release IDs from stored API data."""
    try:
        raw = json.loads(row.get("raw_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return {
        item.get("relatedIdentifierName"): str(item.get("relatedIdentifierValue") or "").strip()
        for item in (raw.get("relatedIdentifiers") or [])
        if isinstance(item, dict)
        and item.get("relatedIdentifierName") in {"DEFERRED_TRANSACTION_ID", "RELEASE_TRANSACTION_ID"}
    }


def _transaction_key(row, transaction_id=None):
    return (row.store or "", row.marketplace_id or "", transaction_id or row.transaction_id or "")


def _matching_item(candidates, row):
    """Keep multiple SKUs in one Amazon transaction from pairing with the wrong item."""
    if not candidates:
        return None
    sku = row.sku or ""
    if sku:
        matches = [candidate for candidate in candidates if (candidate.sku or "") == sku]
        if len(matches) == 1:
            return matches[0]
    item_id = row.amazon_order_item_id or ""
    if item_id:
        matches = [candidate for candidate in candidates if (candidate.amazon_order_item_id or "") == item_id]
        if len(matches) == 1:
            return matches[0]
    return candidates[0] if len(candidates) == 1 else None


def _raw_item_fee_total(nodes):
    """Read fee parents without adding their nested adjustments twice."""
    total = 0.0
    for node in nodes:
        kind = str(node.get("breakdownType") or "").lower()
        children = node.get("breakdowns") or []
        if "fee" in kind:
            total += _金额(node.get("breakdownAmount") or {})
        elif children:
            total += _raw_item_fee_total(children)
    return total


def _item_display(row):
    """Reconstruct Amazon's item lines for one stored transaction/SKU row."""
    if not row.sku:
        return [], None
    try:
        raw = json.loads(row.get("raw_json") or "{}")
    except (TypeError, ValueError):
        return [], None
    source_items = raw.get("items") or []
    if not isinstance(source_items, list):
        return [], None
    sku_items = []
    skus = set()
    for item in source_items:
        if not isinstance(item, dict):
            continue
        sku = str(_上下文值(item.get("contexts") or [], "sku") or "").strip()
        skus.add(sku)
        if sku == row.sku:
            sku_items.append(item)
    # A transaction spanning several SKUs is stored once per SKU; the table
    # should show this SKU's share rather than repeat the transaction total.
    display_amount = _money(row.item_amount) if len(skus) > 1 and row.item_amount is not None else None
    if len(sku_items) < 2:
        return [], display_amount
    expected = Decimal(str(row.item_amount or 0))
    actual = sum((Decimal(str(_金额(item.get("totalAmount") or {}))) for item in sku_items), Decimal(0))
    if abs(expected - actual) > Decimal("0.01"):
        return [], display_amount
    lines = []
    for index, item in enumerate(sku_items, 1):
        amounts = _费用分类(item.get("breakdowns") or [])
        # AmazonFees > FBAPerUnitFulfillmentFee > Base/Promo keeps the fee
        # category on a parent; the imported aggregate uses leaf values.
        contexts = item.get("contexts") or []
        lines.append({
            "line_no": index,
            "line_count": len(sku_items),
            "description": item.get("description") or row.product_name,
            "asin": _上下文值(contexts, "asin") or row.asin,
            "sku": row.sku,
            "quantity": cint(_上下文值(contexts, "quantityShipped")),
            "net_amount": _money(_金额(item.get("totalAmount") or {})),
            "display_fees": _money(_raw_item_fee_total(item.get("breakdowns") or [])),
            **{key: _money(value) for key, value in amounts.items()},
        })
    row.display_fees = _money(sum(line["display_fees"] for line in lines))
    return lines, display_amount


def _release_relationships(selected_rows, fields):
    """Resolve saved and future API transactions without changing their source records.

    A linked release can fall outside the selected month. Fetch its counterpart
    to show the release under the original transaction's month only.
    """
    selected_ids = {row.name for row in selected_rows}
    links = {row.name: _transaction_links(row) for row in selected_rows}
    present_ids = {_transaction_key(row) for row in selected_rows if row.transaction_id}
    missing_ids = {
        linked_id
        for row in selected_rows
        for linked_id in (links[row.name].get("DEFERRED_TRANSACTION_ID"), links[row.name].get("RELEASE_TRANSACTION_ID"))
        if linked_id and _transaction_key(row, linked_id) not in present_ids
    }
    related_rows = []
    missing_ids = sorted(missing_ids)
    for offset in range(0, len(missing_ids), 200):
        related_rows.extend(frappe.get_all(
            DOCTYPE,
            filters={"transaction_id": ["in", missing_ids[offset:offset + 200]]},
            fields=fields,
            limit_page_length=0,
        ))
    all_rows = selected_rows + [row for row in related_rows if row.name not in selected_ids]
    for row in related_rows:
        links[row.name] = _transaction_links(row)

    by_transaction = defaultdict(list)
    by_name = {row.name: row for row in all_rows}
    for row in all_rows:
        if row.transaction_id:
            by_transaction[_transaction_key(row)].append(row)

    parent_for_child = {}
    for row in all_rows:
        deferred_id = links[row.name].get("DEFERRED_TRANSACTION_ID")
        if not deferred_id:
            continue
        parent = _matching_item(by_transaction.get(_transaction_key(row, deferred_id)), row)
        if parent and parent.name != row.name and parent.transaction_type == row.transaction_type:
            parent_for_child[row.name] = parent
    # Some historical responses only have the link on the deferred transaction.
    for row in all_rows:
        release_id = links[row.name].get("RELEASE_TRANSACTION_ID")
        if not release_id:
            continue
        child = _matching_item(by_transaction.get(_transaction_key(row, release_id)), row)
        if child and child.name != row.name and child.transaction_type == row.transaction_type:
            parent_for_child.setdefault(child.name, row)

    children = defaultdict(list)
    for child_name, parent in parent_for_child.items():
        child = by_name.get(child_name)
        if child:
            children[parent.name].append(child)
    for group in children.values():
        group.sort(key=lambda row: str(row.posted_date or ""))
    return all_rows, parent_for_child, children


def _available_months():
    """Show months using the same Pacific calendar as the downloaded report."""
    today = datetime.now(REPORT_TIMEZONE).date()
    earliest = frappe.db.sql(
        f"""SELECT MIN(posted_date) FROM `tab{DOCTYPE}`
            WHERE posted_date BETWEEN %s AND %s""",
        (_report_boundary(f"{today.year}-01-01"),
         _report_boundary(f"{today.year + 1}-01-01") - timedelta(microseconds=1)),
    )[0][0]
    first_report_date = _report_time(earliest)[0] if earliest else None
    first_month = min(first_report_date.month, today.month) if first_report_date else today.month
    return [f"{today.year}-{month:02d}" for month in range(first_month, today.month + 1)]


def _summarize_detail_rows(rows):
    """Summarize every filtered detail row, including rows outside the current page."""
    grouped = {}
    currencies = {}
    amount_fields = ("net", "sales", "fees")

    for row in rows:
        currency = row.currency_code or "未知"
        key = (currency, row.transaction_type or "未分类", row.description or "")
        group = grouped.setdefault(key, {"currency": currency, "transaction_type": key[1],
                                         "description": key[2], "count": 0, **{field: Decimal(0) for field in amount_fields}})
        total = currencies.setdefault(currency, {"currency": currency, "count": 0,
                                                  **{field: Decimal(0) for field in amount_fields}})
        amounts = {
            "net": Decimal(str(_money(row.net_amount))),
            "sales": Decimal(str(_money(row.product_sales))),
            "fees": sum((Decimal(str(_money(row.get(field)))) for field in
                         ("selling_fees", "fba_fees", "other_transaction_fees")), Decimal(0)),
        }
        for result in (group, total):
            result["count"] += 1
            for field in amount_fields:
                result[field] += amounts[field]

    def as_json(result):
        return {key: float(value) if isinstance(value, Decimal) else value
                for key, value in result.items()}

    return {
        "count": len(rows),
        "currencies": [as_json(currencies[key]) for key in sorted(currencies)],
        "rows": [as_json(grouped[key]) for key in sorted(
            grouped, key=lambda key: (key[0], -abs(grouped[key]["net"]), key[1], key[2]))],
    }


@frappe.whitelist()
def get_finance_dashboard_data(filters=None, page=1, page_size=300):
    f = _filters(filters)
    fields = [
        "name", "transaction_sku_key", "transaction_id", "amazon_order_id",
        "amazon_order_item_id", "sku", "asin", "store", "country", "marketplace_id",
        "transaction_type", "transaction_status", "description", "settlement_id",
        "financial_event_group_id", "fulfillment_channel", "product_name", "quantity",
        "corresponding_item", "corresponding_item_name", "posted_date", "release_date",
        "currency_code", "fetched_at", "raw_json", *AMOUNT_FIELDS,
    ]
    rows = frappe.get_all(
        DOCTYPE, filters=_build_db_filters(f), fields=fields,
        order_by="posted_date desc, modified desc", limit_page_length=100000,
    )
    truncated = len(rows) >= 100000
    all_rows, parent_for_child, release_children = _release_relationships(rows, fields)
    for row in all_rows:
        try:
            source = json.loads(row.get("raw_json") or "{}")
        except (TypeError, ValueError):
            source = {}
        row.report_posted_date, row.report_timezone = _report_time(
            row.posted_date, source.get("postedDate")
        )
        row.report_release_date, _ = _report_time(row.release_date)
    mappings = _mapping_rows()
    item_ids = set()
    for row in all_rows:
        if not row.corresponding_item:
            mapping = _resolve_mapping(row, mappings)
            if mapping:
                row.corresponding_item = mapping.物料id
                row.corresponding_item_name = mapping.物料名称
        if row.corresponding_item:
            item_ids.add(row.corresponding_item)

    item_info = {}
    if item_ids:
        for item in frappe.get_all("Item", filters={"name": ["in", list(item_ids)]}, fields=["name", "item_name", "image"]):
            item_info[item.name] = item
    for row in all_rows:
        info = item_info.get(row.corresponding_item)
        row.item_name = (info.item_name if info else None) or row.corresponding_item_name or ""
        row.item_image = (info.image if info else None) or ""
        row.is_bound = 1 if row.corresponding_item else 0
        row.is_product = 1 if (row.sku or row.asin or row.amazon_order_item_id) else 0
        row.detail_group = _transaction_group(row)

    search = (f.search or "").strip().lower()
    binding = f.binding or ""
    scope = f.scope or ""
    item_filter = f.corresponding_item or ""
    if search or binding or scope or item_filter:
        filtered = []
        for row in rows:
            haystack = " ".join(str(row.get(k) or "") for k in (
                "transaction_id", "amazon_order_id", "sku", "asin", "product_name",
                "corresponding_item", "item_name", "description", "settlement_id",
            )).lower()
            if search and search not in haystack:
                continue
            if binding == "bound" and not row.is_bound:
                continue
            if binding == "unbound" and (row.is_bound or not row.is_product):
                continue
            if scope == "product" and not row.is_product:
                continue
            if scope == "store" and row.is_product:
                continue
            if item_filter and row.corresponding_item != item_filter:
                continue
            filtered.append(row)
        rows = filtered

    dashboard_rows = rows
    economic_rows = [row for row in dashboard_rows if row.name not in parent_for_child]
    group_counts = defaultdict(int)
    line_counts = {}
    for row in economic_rows:
        row.item_lines, row.display_net_amount = _item_display(row)
        line_counts[row.name] = max(1, len(row.item_lines))
        group_counts[row.detail_group] += line_counts[row.name]
    report_row_count = sum(line_counts.values())

    detail_group = f.detail_group or ""
    selected_detail_rows = [row for row in dashboard_rows if not detail_group or row.detail_group == detail_group]
    detail_rows = [row for row in selected_detail_rows if row.name not in parent_for_child]

    currencies = defaultdict(lambda: {"inflow": 0.0, "outflow": 0.0, "net": 0.0, "sales": 0.0, "fees": 0.0})
    daily = defaultdict(lambda: defaultdict(lambda: {"net": 0.0, "sales": 0.0, "fees": 0.0, "tax": 0.0, "promotion": 0.0, "count": 0}))
    fee_totals = defaultdict(lambda: defaultdict(float))
    type_totals = defaultdict(lambda: defaultdict(lambda: {"amount": 0.0, "count": 0}))
    store_totals = defaultdict(lambda: defaultdict(lambda: {"amount": 0.0, "count": 0, "items": 0}))
    products = {}
    settlements = {}
    unique_transactions = set()
    bound = 0
    product_count = 0
    store_fee_count = 0

    for row in economic_rows:
        currency = row.currency_code or "未知"
        amount = _money(row.net_amount if row.net_amount is not None else row.total_amount)
        key = (row.store, row.marketplace_id, row.transaction_id)
        if key not in unique_transactions:
            unique_transactions.add(key)
            currencies[currency]["net"] += amount
            currencies[currency]["inflow" if amount >= 0 else "outflow"] += abs(amount)
            currencies[currency]["sales"] += _money(row.product_sales)
            currencies[currency]["fees"] += abs(_money(row.selling_fees) + _money(row.fba_fees) + _money(row.other_transaction_fees))
        if row.is_product:
            product_count += 1
            if row.is_bound:
                bound += 1
        else:
            store_fee_count += 1
        day = str(getdate(row.report_posted_date)) if row.report_posted_date else "未知日期"
        d = daily[day][currency]
        d["net"] += amount
        d["sales"] += _money(row.product_sales)
        d["fees"] += _money(row.selling_fees) + _money(row.fba_fees) + _money(row.other_transaction_fees)
        d["tax"] += _money(row.product_sales_tax) + _money(row.shipping_credits_tax) + _money(row.marketplace_withheld_tax)
        d["promotion"] += _money(row.promotional_rebates) + _money(row.promotional_rebates_tax)
        d["count"] += 1
        for label, field in (("销售费用", "selling_fees"), ("FBA费用", "fba_fees"), ("其他交易费用", "other_transaction_fees"), ("平台代扣税", "marketplace_withheld_tax"), ("促销折扣", "promotional_rebates"), ("销售税", "product_sales_tax")):
            fee_totals[currency][label] += _money(row.get(field))
        t = type_totals[currency][row.transaction_type or "未分类"]
        t["amount"] += amount; t["count"] += 1
        s = store_totals[currency][row.store or "未归属店铺"]
        s["amount"] += amount; s["count"] += 1; s["items"] += cint(row.quantity)
        if row.is_product:
            pkey = (row.store or "", row.marketplace_id or "", row.asin or "", row.sku or "")
            p = products.setdefault(pkey, {"store": row.store, "marketplace_id": row.marketplace_id, "asin": row.asin, "sku": row.sku, "product_name": row.product_name, "item": row.corresponding_item, "item_name": row.item_name, "image": row.item_image, "quantity": 0, "amounts": defaultdict(float), "transactions": 0})
            p["quantity"] += cint(row.quantity); p["amounts"][currency] += amount; p["transactions"] += 1
        skey = (row.store or "", row.settlement_id or "未结算", currency)
        st = settlements.setdefault(skey, {"store": row.store, "settlement_id": row.settlement_id or "未结算", "currency": currency, "amount": 0.0, "count": 0, "last_date": row.report_posted_date})
        st["amount"] += amount; st["count"] += 1
        if row.report_posted_date and (not st["last_date"] or row.report_posted_date > st["last_date"]): st["last_date"] = row.report_posted_date

    product_list = []
    for p in products.values():
        p["amounts"] = dict(p["amounts"]); product_list.append(p)
    product_list.sort(key=lambda x: (x["quantity"], sum(abs(v) for v in x["amounts"].values())), reverse=True)
    settlement_list = sorted(settlements.values(), key=lambda x: str(x["last_date"] or ""), reverse=True)
    page, page_size = max(cint(page), 1), min(max(cint(page_size), 10), 500)
    selected_names = {row.name for row in selected_detail_rows}
    # Date filters select the original transaction, never a release from another month.
    display_rows = sorted(detail_rows, key=lambda row: str(row.posted_date or ""), reverse=True)
    start = (page - 1) * page_size
    page_rows = display_rows[start:start + page_size]
    for row in page_rows:
        row.related_releases = release_children.get(row.name, [])
        for child in row.related_releases:
            child.context_only = child.name not in selected_names
    _attach_awd_fee_details(page_rows)
    # The complete Amazon response is retained in its DocType; do not send it to the browser.
    for row in all_rows:
        row.pop("raw_json", None)
    options = {
        key: sorted({str(r.get(key)) for r in dashboard_rows if r.get(key)})
        for key in ("store", "marketplace_id", "country", "currency_code", "transaction_type", "transaction_status")
    }
    return {
        "summary": {"row_count": len(economic_rows), "report_row_count": report_row_count, "transaction_count": len(unique_transactions), "release_count": sum(len(release_children.get(row.name, [])) for row in economic_rows), "sku_count": len(products), "product_count": product_count, "store_fee_count": store_fee_count, "bound_count": bound, "unbound_count": product_count - bound, "binding_rate": round(bound * 100 / product_count, 1) if product_count else 0, "currencies": dict(currencies), "truncated": truncated},
        "daily": {day: dict(values) for day, values in sorted(daily.items())},
        "fees": {c: dict(v) for c, v in fee_totals.items()},
        "types": {c: dict(v) for c, v in type_totals.items()},
        "stores": {c: dict(v) for c, v in store_totals.items()},
        "products": product_list[:500], "settlements": settlement_list[:200],
        "rows": page_rows, "options": options, "available_months": _available_months(),
        "detail_summary": _summarize_detail_rows(detail_rows),
        "detail_groups": [
            {"key": "", "label": "全部", "count": report_row_count},
            *[
                {"key": key, "label": label, "count": group_counts[key]}
                for key, label in DETAIL_GROUPS
            ],
        ],
        "pagination": {"page": page, "page_size": page_size, "total": len(display_rows), "display_total": sum(line_counts[row.name] for row in display_rows), "raw_total": len(selected_detail_rows), "pages": max(1, (len(display_rows) + page_size - 1) // page_size)},
    }


@frappe.whitelist()
def bind_finance_item(store, marketplace_id, item, asin=None, sku=None):
    frappe.only_for("System Manager")
    if not item or not frappe.db.exists("Item", item):
        frappe.throw("请选择有效的ERPNext物料")
    asin, sku = (asin or "").strip().upper(), (sku or "").strip().upper()
    if not asin and not sku:
        frappe.throw("ASIN和SKU至少需要一个")
    filters = {"店铺": store, "站点id": marketplace_id}
    if asin: filters["平台asin"] = asin
    if sku: filters["平台sku"] = sku
    name = frappe.db.get_value(MAPPING_DOCTYPE, filters, "name")
    if name:
        doc = frappe.get_doc(MAPPING_DOCTYPE, name)
        doc.物料id = item; doc.启用 = 1; doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({"doctype": MAPPING_DOCTYPE, "启用": 1, "店铺": store, "站点id": marketplace_id, "平台asin": asin, "平台sku": sku, "物料id": item}).insert(ignore_permissions=True)
    clauses, values = ["store=%s", "marketplace_id=%s"], [store, marketplace_id]
    match, match_values = [], []
    if asin: match.append("UPPER(IFNULL(asin,''))=%s"); match_values.append(asin)
    if sku: match.append("UPPER(IFNULL(sku,''))=%s"); match_values.append(sku)
    frappe.db.sql(f"UPDATE `tab{DOCTYPE}` SET corresponding_item=%s WHERE {' AND '.join(clauses)} AND ({' OR '.join(match)})", [item, *values, *match_values])
    frappe.db.commit()
    return {"mapping": doc.name, "item": item}


@frappe.whitelist()
def refresh_finance_item_bindings():
    frappe.only_for("System Manager")
    mappings = _mapping_rows()
    rows = frappe.get_all(DOCTYPE, fields=["name", "store", "marketplace_id", "asin", "sku", "corresponding_item"], limit_page_length=0)
    updated = 0
    for row in rows:
        mapping = _resolve_mapping(row, mappings)
        target = mapping.物料id if mapping else None
        if target and target != row.corresponding_item:
            frappe.db.set_value(DOCTYPE, row.name, "corresponding_item", target, update_modified=False)
            updated += 1
    frappe.db.commit()
    return {"updated": updated, "total": len(rows)}
