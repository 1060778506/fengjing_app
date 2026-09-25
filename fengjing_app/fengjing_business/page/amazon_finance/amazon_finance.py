from collections import defaultdict
from decimal import Decimal

import frappe
from frappe.utils import cint, flt, getdate


DOCTYPE = "Amazon Financial Transaction"
MAPPING_DOCTYPE = "Fengjing - Product Corresponding Platform - Main Table"
AMOUNT_FIELDS = [
    "total_amount", "item_amount", "product_sales", "product_sales_tax",
    "shipping_credits", "shipping_credits_tax", "promotional_rebates",
    "promotional_rebates_tax", "marketplace_withheld_tax", "selling_fees",
    "fba_fees", "other_transaction_fees", "other_amount", "net_amount",
]


def _money(value):
    return flt(value or 0, 2)


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
        filters["posted_date"] = ["between", [f.date_from, f"{f.date_to} 23:59:59"]]
    elif f.date_from:
        filters["posted_date"] = [">=", f.date_from]
    elif f.date_to:
        filters["posted_date"] = ["<=", f"{f.date_to} 23:59:59"]
    for key in ("store", "marketplace_id", "country", "currency_code", "transaction_type", "transaction_status"):
        if f.get(key):
            filters[key] = f.get(key)
    return filters


@frappe.whitelist()
def get_finance_dashboard_data(filters=None, page=1, page_size=50):
    f = _filters(filters)
    fields = [
        "name", "transaction_sku_key", "transaction_id", "amazon_order_id",
        "amazon_order_item_id", "sku", "asin", "store", "country", "marketplace_id",
        "transaction_type", "transaction_status", "description", "settlement_id",
        "financial_event_group_id", "fulfillment_channel", "product_name", "quantity",
        "corresponding_item", "corresponding_item_name", "posted_date", "release_date",
        "currency_code", "fetched_at", *AMOUNT_FIELDS,
    ]
    rows = frappe.get_all(
        DOCTYPE, filters=_build_db_filters(f), fields=fields,
        order_by="posted_date desc, modified desc", limit_page_length=100000,
    )
    truncated = len(rows) >= 100000
    mappings = _mapping_rows()
    item_ids = set()
    for row in rows:
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
    for row in rows:
        info = item_info.get(row.corresponding_item)
        row.item_name = (info.item_name if info else None) or row.corresponding_item_name or ""
        row.item_image = (info.image if info else None) or ""
        row.is_bound = 1 if row.corresponding_item else 0
        row.is_product = 1 if (row.sku or row.asin or row.amazon_order_item_id) else 0

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

    for row in rows:
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
        day = str(getdate(row.posted_date)) if row.posted_date else "未知日期"
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
        st = settlements.setdefault(skey, {"store": row.store, "settlement_id": row.settlement_id or "未结算", "currency": currency, "amount": 0.0, "count": 0, "last_date": row.posted_date})
        st["amount"] += amount; st["count"] += 1
        if row.posted_date and (not st["last_date"] or row.posted_date > st["last_date"]): st["last_date"] = row.posted_date

    product_list = []
    for p in products.values():
        p["amounts"] = dict(p["amounts"]); product_list.append(p)
    product_list.sort(key=lambda x: (x["quantity"], sum(abs(v) for v in x["amounts"].values())), reverse=True)
    settlement_list = sorted(settlements.values(), key=lambda x: str(x["last_date"] or ""), reverse=True)
    page, page_size = max(cint(page), 1), min(max(cint(page_size), 10), 200)
    start = (page - 1) * page_size
    page_rows = rows[start:start + page_size]
    options = {
        key: sorted({str(r.get(key)) for r in rows if r.get(key)})
        for key in ("store", "marketplace_id", "country", "currency_code", "transaction_type", "transaction_status")
    }
    return {
        "summary": {"row_count": len(rows), "transaction_count": len(unique_transactions), "sku_count": len(products), "product_count": product_count, "store_fee_count": store_fee_count, "bound_count": bound, "unbound_count": product_count - bound, "binding_rate": round(bound * 100 / product_count, 1) if product_count else 0, "currencies": dict(currencies), "truncated": truncated},
        "daily": {day: dict(values) for day, values in sorted(daily.items())},
        "fees": {c: dict(v) for c, v in fee_totals.items()},
        "types": {c: dict(v) for c, v in type_totals.items()},
        "stores": {c: dict(v) for c, v in store_totals.items()},
        "products": product_list[:500], "settlements": settlement_list[:200],
        "rows": page_rows, "options": options,
        "pagination": {"page": page, "page_size": page_size, "total": len(rows), "pages": max(1, (len(rows) + page_size - 1) // page_size)},
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
