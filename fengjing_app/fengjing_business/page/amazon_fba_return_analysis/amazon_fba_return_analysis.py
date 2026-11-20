"""FBA customer return overview. Source marketplace labels are not trusted."""

from collections import Counter, defaultdict
from datetime import timedelta
import json

import frappe
from frappe.utils import cint, getdate

from fengjing_app.fengjing_business.page.amazon_finance.amazon_finance import (
    INVENTORY_LEDGER_DETAIL_DOCTYPE,
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


def _match_inventory_events(returns, events, sellers):
    """Return ledger candidates keyed by FBA return row, preserving ambiguous matches."""
    def scope(row):
        return sellers.get(row.amazon_store) or row.amazon_store, (row.seller_sku or "").strip().upper()

    def inventory_key(row, center, disposition):
        return (*scope(row), (center or "").strip().upper(), (disposition or "").strip().upper())

    receipts, removals, references = defaultdict(list), defaultdict(list), defaultdict(list)
    for event in events:
        event.day = getdate(event.event_at)
        event.reference = (event.reference_id or "").strip().upper()
        key = inventory_key(event, event.fulfillment_center, event.disposition)
        if event.event_type == "CustomerReturns" and event.quantity > 0:
            receipts[key].append(event)
        elif event.event_type == "VendorReturns" and event.quantity < 0:
            removals[key].append(event)
        else:
            continue
        if event.reference:
            references[(*scope(event), event.reference)].append(event)

    receipt_candidates, removal_candidates = {}, {}
    receipt_owners, removal_owners = Counter(), Counter()
    conditions = {}
    for row in returns:
        key = inventory_key(row, row.fulfillment_center_id, row.detailed_disposition)
        identifiers = {(value or "").strip().upper() for value in (row.order_id, row.license_plate_number)} - {""}
        by_reference = [event for ref in identifiers for event in references.get((*scope(row), ref), ())]
        day = getdate(row.report_day)

        def fits_item(event):
            return not (row.fnsku and event.fnsku) or row.fnsku.upper() == event.fnsku.upper()

        exact_receipts = [e for e in by_reference if e.event_type == "CustomerReturns" and fits_item(e)]
        nearby = [e for e in receipts[key] if not e.reference and fits_item(e)
                  and key[2] and key[3] and e.quantity == cint(row.quantity) and abs((e.day - day).days) <= 1]
        if nearby and not exact_receipts:
            distance = min(abs((event.day - day).days) for event in nearby)
            nearby = [event for event in nearby if abs((event.day - day).days) == distance]
        arrivals = exact_receipts or nearby
        receipt_candidates[row.name] = arrivals
        receipt_owners.update(event.name for event in arrivals)

        exact_removals = [e for e in by_reference if e.event_type == "VendorReturns" and fits_item(e)]
        possible = {}
        ambiguous_days = set()
        if not exact_removals and key[2] and key[3]:
            for arrival in arrivals:
                # A later receipt starts a new candidate window. Day-only
                # ledger dates leave the shared boundary ambiguous.
                next_day = min((e.day for e in receipts[key] if e.day > arrival.day and fits_item(e)), default=None)
                for event in removals[key]:
                    if (not event.reference and fits_item(event) and -event.quantity == cint(row.quantity)
                            and arrival.day <= event.day and (next_day is None or event.day <= next_day)):
                        possible[event.name] = event
                        if event.day in (arrival.day, next_day):
                            ambiguous_days.add(event.name)
        removals_for_row = exact_removals or list(possible.values())
        removal_candidates[row.name] = removals_for_row
        removal_owners.update(event.name for event in removals_for_row)
        conditions[row.name] = (exact_receipts, exact_removals, ambiguous_days)

    result = {}
    for row in returns:
        arrivals = receipt_candidates[row.name]
        departures = removal_candidates[row.name]
        exact_receipts, exact_removals, ambiguous_days = conditions[row.name]
        arrival_unique = len(arrivals) == 1 and receipt_owners[arrivals[0].name] == 1
        timeline = []
        for candidates, owners, exact in (
                (arrivals, receipt_owners, exact_receipts), (departures, removal_owners, exact_removals)):
            for event in candidates:
                if exact and owners[event.name] == 1:
                    match = "reference"
                elif (len(candidates) == 1 and owners[event.name] == 1
                      and arrival_unique and event.name not in ambiguous_days):
                    match = "conditions"
                else:
                    match = "review"
                timeline.append({
                    "name": event.name, "date": event.day.isoformat(), "event_type": event.event_type,
                    "fulfillment_center": event.fulfillment_center, "disposition": event.disposition,
                    "quantity": event.quantity, "reference_id": event.reference_id, "match": match,
                    "cross_month": event.day.isoformat()[:7] != row.report_month,
                })
        result[row.name] = sorted(timeline, key=lambda event: (event["date"], -event["quantity"], event["name"]))
    return result


def _inventory_events_for_returns(visible, all_rows):
    if not visible:
        return {}
    skus = {row.seller_sku for row in visible if row.seller_sku and row.report_day}
    if not skus:
        return {}
    sellers = {row.name: row.seller_id or row.name for row in frappe.get_all(
        "Amazon Store Configuration", fields=["name", "seller_id"], limit_page_length=0,
    )}
    visible_sellers = {sellers.get(row.amazon_store, row.amazon_store) for row in visible}
    stores = {store for store, seller in sellers.items() if seller in visible_sellers}
    stores.update(row.amazon_store for row in visible if row.amazon_store)
    relevant = [row for row in all_rows if row.seller_sku in skus and row.report_day
                and sellers.get(row.amazon_store, row.amazon_store) in visible_sellers]
    if not relevant or not stores:
        return {}
    events = frappe.get_all(
        INVENTORY_LEDGER_DETAIL_DOCTYPE,
        filters={"amazon_store": ["in", sorted(stores)], "seller_sku": ["in", sorted(skus)],
                 "event_type": ["in", ["CustomerReturns", "VendorReturns"]],
                 "event_at": [">=", getdate(min(row.report_day for row in relevant)) - timedelta(days=1)]},
        fields=["name", "amazon_store", "seller_sku", "fnsku", "event_at", "event_type",
                "fulfillment_center", "disposition", "reference_id", "quantity"],
        limit_page_length=0,
    )
    return _match_inventory_events(relevant, events, sellers)


def _in_date_range(day, start_day, end_day):
    return (not start_day or day >= start_day) and (not end_day or day <= end_day)


def _returns_by_inventory_month(returns, inventory_events, start_day, end_day):
    matched_results = {"reference", "conditions"}
    rows = []
    for row in returns:
        events = inventory_events.get(row.name, ())
        receipts = [event for event in events if event["event_type"] == "CustomerReturns" and event["match"] in matched_results]
        receipt_months = {event["date"][:7] for event in receipts} or {row.report_month}
        unmatched = max(0, cint(row.quantity) - sum(event["quantity"] for event in receipts))
        events_by_month = defaultdict(list)
        for event in events:
            if _in_date_range(event["date"], start_day, end_day):
                events_by_month[event["date"][:7]].append(event)
        if unmatched and _in_date_range(row.report_day, start_day, end_day):
            events_by_month.setdefault(row.report_month, [])
        else:
            unmatched = 0

        for month, month_events in events_by_month.items():
            matched = [event for event in month_events if event["match"] in matched_results]
            month_receipts = [event for event in matched if event["event_type"] == "CustomerReturns"]
            received = sum(event["quantity"] for event in month_receipts)
            removed = -sum(event["quantity"] for event in matched if event["event_type"] == "VendorReturns")
            unmatched_in_month = unmatched if month == row.report_month else 0
            if not (received or removed or unmatched_in_month):
                continue
            rows.append(frappe._dict(
                row, inventory_month=month, received_units=received, removed_units=removed,
                received_sellable_units=sum(event["quantity"] for event in month_receipts if event["disposition"] == "SELLABLE"),
                unmatched_receipt_units=unmatched_in_month, inventory_events=month_events,
                cross_month_return=bool(removed and any(receipt_month < month for receipt_month in receipt_months)),
                inventory_day=max((event["date"] for event in matched), default=row.report_day),
            ))
    return sorted(rows, key=lambda row: (row.inventory_day, row.report_date, row.name), reverse=True)


@frappe.whitelist()
def get_finance_return_data(date_from=None, date_to=None, search=None, page=1, page_size=20, date_basis="return"):
    """List FBA returns by report date or by the month of their inventory events."""
    start_day = str(getdate(date_from)) if date_from else ""
    end_day = str(getdate(date_to)) if date_to else ""
    if start_day and end_day and start_day > end_day:
        frappe.throw("退货开始日期不能晚于结束日期")
    all_rows, truncated = _unique_returns()
    query = str(search or "").strip().lower()
    rows = [row for row in all_rows if not query or query in " ".join(str(row.get(key) or "") for key in (
        "order_id", "seller_sku", "asin", "fnsku", "product_name",
    )).lower()]
    by_inventory = date_basis in ("inventory", "removal")
    if by_inventory:
        inventory_events = _inventory_events_for_returns(rows, all_rows)
        rows = _returns_by_inventory_month(rows, inventory_events, start_day, end_day)
    else:
        rows = [row for row in rows if _in_date_range(row.report_day, start_day, end_day)]
    page_size = min(max(cint(page_size), 1), 100)
    pages = max(1, (len(rows) + page_size - 1) // page_size)
    page = min(max(cint(page), 1), pages)
    visible = rows[(page - 1) * page_size:page * page_size]
    _item_info(visible)
    order_months = _original_order_months(visible)
    if not by_inventory:
        inventory_events = _inventory_events_for_returns(visible, all_rows)
    original_returns = {row.name: row for row in rows}.values()
    return {
        "date_basis": "inventory" if by_inventory else "return",
        "summary": {
            "records": len(rows),
            "units": sum(cint(row.quantity) for row in original_returns),
            "received_units": sum(cint(row.get("received_units")) for row in rows),
            "received_sellable_units": sum(cint(row.get("received_sellable_units")) for row in rows),
            "removed_units": sum(cint(row.get("removed_units")) for row in rows),
            "unmatched_receipt_units": sum(cint(row.get("unmatched_receipt_units")) for row in rows),
            "orders": len({row.order_id for row in rows if row.order_id}),
            "sellable": sum(cint(row.quantity) for row in original_returns if row.category == "sellable"),
            "unsellable": sum(cint(row.quantity) for row in original_returns if row.category == "unsellable"),
            "unknown": sum(cint(row.quantity) for row in original_returns if row.category == "unknown"),
            "liquidation": sum(cint(row.quantity) for row in original_returns if row.status == "IMMEDIATE_LIQUIDATION"),
            "returned_to_inventory": sum(cint(row.quantity) for row in original_returns if row.status == "Unit returned to inventory"),
        },
        "rows": [{**{key: row.get(key) for key in (
            "name", "order_id", "seller_sku", "asin", "fnsku", "product_name",
            "quantity", "fulfillment_center_id", "detailed_disposition",
            "reason", "status", "item_id", "item_name", "item_image",
            "report_date", "report_timezone", "inventory_month", "received_units", "removed_units",
            "unmatched_receipt_units", "cross_month_return",
        )}, "order_month": order_months.get(row.order_id, ""),
            "inventory_events": row.inventory_events if by_inventory else inventory_events.get(row.name, [])} for row in visible],
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
