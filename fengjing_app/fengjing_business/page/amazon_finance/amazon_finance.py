from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import re
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import cint, flt, get_datetime, get_system_timezone, getdate

from fengjing_app.fengjing_business.doctype.amazon_financial_configuration.amazon_financial_configuration import (
    DISBURSEMENT_FIELD, _disbursement_amount, read_disbursement_group,
)
from fengjing_app.fengjing_business.doctype.amazon_financial_transaction.amazon_financial_transaction import (
    _上下文值, _费用分类, _金额,
)


DOCTYPE = "Amazon Financial Transaction"
FULFILLED_SHIPMENT_DOCTYPE = "Amazon FBA Fulfilled Shipment"
CUSTOMER_SALE_DOCTYPE = "Amazon FBA Customer Shipment Sale"
INVENTORY_LEDGER_DETAIL_DOCTYPE = "Amazon FBA Inventory Ledger Detail"
MAPPING_DOCTYPE = "Fengjing - Product Corresponding Platform - Main Table"
REPORT_TIMEZONE = ZoneInfo("America/Los_Angeles")
DISBURSEMENT_TIMEZONE = ZoneInfo("Asia/Shanghai")
FILTER_OPTION_FIELDS = (
    "store", "marketplace_id", "country", "currency_code",
    "transaction_type", "transaction_status",
)
AMOUNT_FIELDS = [
    "total_amount", "item_amount", "product_sales", "product_sales_tax",
    "shipping_credits", "shipping_credits_tax", "promotional_rebates",
    "promotional_rebates_tax", "marketplace_withheld_tax", "selling_fees",
    "fba_fees", "other_transaction_fees", "other_amount", "net_amount",
]
ORDER_FEE_LABELS = {
    "Commission": "佣金",
    "FBAPerUnitFulfillmentFee": "FBA 履约费",
    "ShippingChargeback": "运费扣回",
    "GiftwrapChargeback": "礼品包装费扣回",
    "selling_fees": "销售费（未拆分）",
    "fba_fees": "履约费（未拆分）",
    "other_transaction_fees": "其他费用（未拆分）",
    "AmazonFees": "平台费（未拆分）",
}
ORDER_TOTAL_FIELDS = ("net", "sales", "shipping")
REFUND_FEE_LABELS = {
    **ORDER_FEE_LABELS,
    "Commission": "退回销售佣金",
    "RefundCommission": "退款管理费",
    "shipping_credits": "退款运费调整",
    "promotional_rebates": "退款促销调整",
    "tax_adjustment": "退款税额调整",
    "other_adjustment": "其他退款调整",
}
REFUND_FEE_ACCOUNTS = {
    "Commission": "560104_fj",
    "RefundCommission": "560104_fj",
    "FBAPerUnitFulfillmentFee": "540104_fj",
    "ShippingChargeback": "5001_fj",
    "shipping_credits": "5001_fj",
}

DETAIL_GROUPS = (
    ("order", "订单"),
    ("refund", "退款"),
    ("logistics", "仓储费 / 入库与物流"),
    ("removal", "移除货件"),
    ("promotion", "广告与推广"),
    ("service", "订阅与服务"),
    ("funds", "资金划转"),
    ("adjustment", "调整与赔偿"),
    ("other", "其他"),
)
LOGISTICS_ACCOUNT = "540103_fj - 海运费用【主营业务成本】 - 丰境"
STORAGE_ACCOUNT = "560105_fj - 平台仓储费【销售费用】 - 丰境"
STORAGE_DESCRIPTIONS = {"FBAStorageBilling", "STARStorageBilling"}
DEFAULT_COST_CENTER = "亚马逊-美国-Neighbor's house-A1VOX5X8GAU17J - 丰境"
JOURNAL_DETAIL_GROUPS = {"removal", "service", "adjustment"}
JOURNAL_ACCOUNT_NUMBERS = {"101204_fj", "5051_fj", "5301_fj", "560104_fj", "560108_fj"}


def _money(value):
    return flt(value or 0, 2)


def _saved_item_amounts(value):
    try:
        data = json.loads(value or "{}") if isinstance(value, str) else value
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    result = {}
    for item, amount in data.items():
        try:
            number = Decimal(str(amount))
        except (TypeError, ValueError, ArithmeticError):
            continue
        if isinstance(item, str) and number.is_finite():
            result[item] = float(abs(number))
    return result


def _awd_storage_order_id(order_id):
    """The finance API may append -AWD to the report's storage billing ID."""
    return re.sub(r"-AWD$", "", str(order_id or "").strip(), flags=re.IGNORECASE)


def _awd_child_key(detail):
    identity = [detail.get(field) or "" for field in (
        "source_type", "shipment_id", "tracking_id", "billing_order_id",
        "msku", "fnsku", "asin", "storage_region",
    )]
    return "awd:" + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:24]


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
                "manual_key": _awd_child_key(detail),
                "manual_items": row.manual_child_items.get(_awd_child_key(detail), []),
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


def _report_time(stored_value, raw_value=None, time_zone=REPORT_TIMEZONE):
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
    local = instant.astimezone(time_zone)
    return local.replace(tzinfo=None), local.tzname()


def _transaction_group(row):
    """Group Amazon's detailed event names into stable, accounting-friendly tabs."""
    transaction_type = str(row.get("transaction_type") or "")
    description = str(row.get("description") or "")
    value = f"{transaction_type} {description}"

    if transaction_type == "Shipment" and description == "Order Payment":
        return "order"
    if transaction_type == "RemovalShipment":
        return "removal"
    if transaction_type in {"Refund", "RefundEvent"}:
        return "refund" if row.get("amazon_order_id") else "adjustment"
    if transaction_type in {"Chargeback", "ChargebackEvent"}:
        return "adjustment"
    if re.search(r"StorageBilling|StorageFee|LongTermStorage|STARStorage", value, re.I):
        return "logistics"
    if re.search(
        r"Inbound|Transportation|ShipmentInjection|AWDProcessing|FBADisposal",
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
        fields=["name", "店铺", "站点id", "平台asin", "平台sku", "平台fnsku", "物料id", "物料名称"],
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
        mf = (m.平台fnsku or "").upper()
        score = 0
        if sku and mf == sku:
            score = 5
        elif asin and sku and ma == asin and ms == sku:
            score = 4
        elif sku and ms == sku:
            score = 3
        elif asin and ma == asin:
            score = 2
        if score:
            candidates.append((score, m))
    return max(candidates, key=lambda x: x[0])[1] if candidates else None


def _selected_pending_month(f):
    if f.period != "pending" or not f.pending_month:
        return None
    month = str(f.pending_month)
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month) or month.startswith("0000-"):
        frappe.throw("请选择有效的原交易月份")
    if month > datetime.now(REPORT_TIMEZONE).strftime("%Y-%m"):
        frappe.throw("请选择当前月份或之前的月份")
    return month


def _build_db_filters(f):
    if f.period == "pending":
        return {"transaction_status": ["in", ["DEFERRED", "PENDING"]]}
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
    return filters


def _transaction_links(row):
    """Read Amazon's explicit deferred/release IDs from stored API data."""
    try:
        raw = json.loads(row.get("raw_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return _release_identifiers(raw.get("relatedIdentifiers")) if isinstance(raw, dict) else {}


def _release_identifiers(identifiers):
    return {
        item.get("relatedIdentifierName"): str(item.get("relatedIdentifierValue") or "").strip()
        for item in identifiers or []
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


def _raw_item_fee_categories(nodes):
    """Classify AmazonFees children before their Base leaves lose fee context."""
    categories = {"selling_fees": 0.0, "fba_fees": 0.0, "other_transaction_fees": 0.0}
    found = False

    def visit(entries):
        nonlocal found
        for node in entries or []:
            if not isinstance(node, dict):
                continue
            kind = str(node.get("breakdownType") or "").lower()
            children = node.get("breakdowns") or []
            if kind == "amazonfees" and children:
                visit(children)
                continue
            if kind == "amazonfees" or "fee" in kind or kind in {
                "commission", "refundcommission", "shippingchargeback", "giftwrapchargeback",
            }:
                found = True
                if any(part in kind for part in ("fba", "fulfillment", "storage")):
                    field = "fba_fees"
                elif any(part in kind for part in ("commission", "referral", "selling")):
                    field = "selling_fees"
                else:
                    field = "other_transaction_fees"
                categories[field] += _金额(node.get("breakdownAmount") or {})
            elif children:
                visit(children)

    visit(nodes)
    return {key: _money(value) for key, value in categories.items()} if found else None


def _liquidation_breakdown(nodes):
    fields = {
        "RecommerceLiquidation": "gross_recovery",
        "LiquidationProcessingFee": "processing_fee",
        "LiquidationReferralFee": "referral_fee",
    }
    amounts = defaultdict(float)

    def visit(entries):
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            return
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            field = fields.get(entry.get("breakdownType"))
            amount = entry.get("breakdownAmount")
            if field and isinstance(amount, dict) and ("currencyAmount" in amount or "amount" in amount):
                amounts[field] += _金额(amount)
            else:
                visit(entry.get("breakdowns"))

    visit(nodes)
    return {field: _money(amount) for field, amount in amounts.items()}


def _attach_liquidation_breakdown(row):
    try:
        source = json.loads(row.get("raw_json") or "{}")
    except (TypeError, ValueError):
        return
    if not isinstance(source, dict):
        return
    items = [item for item in (source.get("items") or []) if isinstance(item, dict)]
    sku_items = [item for item in items if str(_上下文值(item.get("contexts") or [], "sku") or "").strip() == row.sku]
    item_amounts = [_liquidation_breakdown(item.get("breakdowns")) for item in sku_items]
    common_fields = set.intersection(*(set(parts) for parts in item_amounts)) if item_amounts else set()
    row.liquidation = {field: _money(sum(parts[field] for parts in item_amounts)) for field in common_fields}
    # Amazon can report fee components only at transaction level. Use those totals
    # when this row represents the entire transaction.
    if not row.sku or len(items) <= 1 or len(sku_items) == len(items):
        row.liquidation.update(_liquidation_breakdown(source.get("breakdowns")))
    for line, parts in zip(row.item_lines, item_amounts):
        line.liquidation = parts


def _journal_counterparts(row, net):
    if row.detail_group == "removal" and row.description == "RecommerceAfterMarketplaceShipmentItem":
        liquidation = row.get("liquidation") or {}
        if {"gross_recovery", "processing_fee", "referral_fee"} <= liquidation.keys():
            gross, processing, commission = (Decimal(str(liquidation[key])) for key in (
                "gross_recovery", "processing_fee", "referral_fee",
            ))
            if gross + processing + commission == net:
                return [("清算回收收入", "5051_fj", -gross), ("清算处理费", "560108_fj", -processing),
                        ("清算佣金", "560108_fj", -commission)]
        return [("清算净回收", "5051_fj", -net)]
    if row.detail_group == "service":
        label = "店铺订阅费" if row.description == "Subscription" else row.description or "平台服务费"
        return [(label, "560104_fj", -net)]
    if row.transaction_type == "FBAInventoryReimbursement":
        label = "FBA仓库损坏赔偿" if row.description == "WAREHOUSE_DAMAGE" else row.description or "FBA库存赔偿"
        return [(label, "5301_fj", -net)]
    return [(row.description or row.transaction_type or "平台调整", "", -net)]


def _platform_journal_summary(rows, mappings):
    stores = sorted({row.store for row in rows if row.store})
    companies = {row.name: row.company for row in frappe.get_all(
        "Cost Center", filters={"name": ["in", stores], "is_group": 0}, fields=["name", "company"],
        limit_page_length=0,
    )} if stores else {}
    accounts = {(row.company, row.account_number): row for row in frappe.get_all(
        "Account", filters={"company": ["in", sorted(set(companies.values()))], "is_group": 0, "disabled": 0,
                            "account_number": ["in", sorted(JOURNAL_ACCOUNT_NUMBERS)]},
        fields=["name", "account_number", "account_currency", "company"], limit_page_length=0,
    )} if companies else {}
    currencies = {row.name: row.default_currency for row in frappe.get_all(
        "Company", filters={"name": ["in", sorted(set(companies.values()))]},
        fields=["name", "default_currency"], limit_page_length=0,
    )} if companies else {}
    groups = {}
    for row in rows:
        company, currency = companies.get(row.store, ""), row.currency_code or "未知"
        net = Decimal(str(_money(row.net_amount)))
        if row.detail_group == "removal":
            _attach_liquidation_breakdown(row)
        counterparts = _journal_counterparts(row, net)
        if not net and not any(amount for _, _, amount in counterparts):
            continue
        group = groups.setdefault((company, currency), {"company": company, "currency": currency,
                                  "platform_net": Decimal(0), "rows": []})
        group["platform_net"] += net
        bank_number = "101204_fj" if row.country == "United States" and currency == "USD" else ""
        bank_label = counterparts[0][0] + "_平台余额"
        mapping = _resolve_mapping(row, mappings)
        item = (mapping.物料id if mapping else None) or row.corresponding_item or ""
        date = str(row.report_posted_date or "")[:10]
        reference = row.amazon_order_id or row.settlement_id or row.release_transaction_id or row.transaction_id
        for label, account_number, amount, platform in [
            (bank_label, bank_number, net, True),
            *((label, number, amount, False) for label, number, amount in counterparts),
        ]:
            if not amount:
                continue
            account = accounts.get((company, account_number))
            use_foreign_input = not platform and currency != currencies.get(company)
            # The foreign helper columns accept amounts for company-currency accounts only.
            if account and account.account_currency != (currency if platform else currencies.get(company)):
                account = None
            debit, credit = float(max(amount, 0)), float(max(-amount, 0))
            description = "_".join(part for part in (label, date, reference) if part)
            group["rows"].append({
                "cost_center": row.store if company else "", "item": "" if platform else item,
                "account": account.name if account else "", "description": description,
                "debit": 0 if use_foreign_input else debit, "credit": 0 if use_foreign_input else credit,
                "foreign_debit": debit if use_foreign_input else 0,
                "foreign_credit": credit if use_foreign_input else 0,
            })
    return [{**group, "platform_net": float(group["platform_net"])} for _, group in sorted(groups.items())]


def _funds_summary(rows):
    names = {row.name for row in rows if row.description == "Disbursement" and row.transaction_type == "Transfer"}
    stored = {}
    if names and frappe.db.has_column(DOCTYPE, DISBURSEMENT_FIELD):
        stored = {entry.name: entry.get(DISBURSEMENT_FIELD) for entry in _read_order_summary_rows(
            DOCTYPE, ["name", DISBURSEMENT_FIELD], {}, "name", names,
        )}
    original_totals = defaultdict(Decimal)
    totals = defaultdict(Decimal)
    unmatched = 0
    summary_rows = []
    for row in rows:
        payout = read_disbursement_group(row, stored.get(row.name)) if row.name in names else None
        converted = _disbursement_amount(payout.get("ConvertedTotal")) if payout else None
        sent_at, _ = _report_time(None, payout.get("FundTransferDate"), DISBURSEMENT_TIMEZONE) if payout else (None, None)
        row.disbursement = {
            "date": sent_at.date().isoformat() if sent_at else "",
            "currency": converted[0] if converted else "",
            "amount": float(converted[1]) if converted else None,
        }
        if converted:
            totals[converted[0]] += converted[1]
        if row.name in names:
            original_totals[row.currency_code or "未知"] += Decimal(str(_money(row.net_amount)))
            if not sent_at or not converted:
                unmatched += 1
        summary_rows.append({
            "store": row.store, "description": row.description or row.transaction_type,
            "amount": row.net_amount, "currency": row.currency_code, "settlement_id": row.settlement_id,
            "disbursement_date": row.disbursement["date"], "disbursement_currency": row.disbursement["currency"],
            "disbursement_amount": row.disbursement["amount"],
        })
    return {"rows": summary_rows, "unmatched": unmatched,
            "original_totals": [{"currency": currency, "amount": float(amount)} for currency, amount in sorted(original_totals.items())],
            "totals": [{"currency": currency, "amount": float(amount)} for currency, amount in sorted(totals.items())]}


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
    fee_categories = {"selling_fees": 0.0, "fba_fees": 0.0, "other_transaction_fees": 0.0}
    has_fee_breakdown = False
    for item in sku_items:
        parsed = _raw_item_fee_categories(item.get("breakdowns") or [])
        if parsed is not None:
            has_fee_breakdown = True
            for field, amount in parsed.items():
                fee_categories[field] += amount
    if has_fee_breakdown:
        row.display_fee_categories = {field: _money(amount) for field, amount in fee_categories.items()}
        row.display_fees = _money(sum(row.display_fee_categories.values()))
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
            **(_raw_item_fee_categories(item.get("breakdowns") or []) or {}),
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


def _reverse_release_rows(rows, fields, pending):
    """Find counterpart records whose backlink may be outside the selected period."""
    key = "DEFERRED_TRANSACTION_ID" if pending else "RELEASE_TRANSACTION_ID"
    targets = {_transaction_key(row) for row in rows if row.transaction_id and (
        pending or (str(row.transaction_status or "").upper() == "RELEASED"
                    and not _transaction_links(row).get("DEFERRED_TRANSACTION_ID"))
    )}
    if not targets:
        return []
    present = {row.name for row in rows}
    references = frappe.db.sql(
        """SELECT name, store, marketplace_id,
                  JSON_EXTRACT(raw_json, '$.relatedIdentifiers') AS identifiers
             FROM `tabAmazon Financial Transaction`
            WHERE raw_json LIKE %s AND JSON_VALID(raw_json)""",
        (f'%"{key}"%',), as_dict=True,
    )
    matches = []
    for reference in references:
        if reference.name not in present:
            linked_id = _release_identifiers(json.loads(reference.identifiers or "[]")).get(key)
            if linked_id and _transaction_key(reference, linked_id) in targets:
                matches.append(reference.name)
    return list(_read_order_summary_rows(DOCTYPE, fields, {}, "name", matches))


def _finance_period_rows(rows, parents, children, pending):
    """A release carries the settled amounts. A deferred transaction carries its open balance."""
    selected = []
    for source in rows:
        status = str(source.transaction_status or "").upper()
        links = _transaction_links(source)
        if pending:
            released = any(str(child.transaction_status or "").upper() == "RELEASED" for child in children.get(source.name, ()))
            if not released and not links.get("RELEASE_TRANSACTION_ID"):
                source.report_maturity_date, _ = _report_time(source.release_date)
                selected.append(source)
            continue
        if status in {"PENDING", "DEFERRED", "DEFERRED_RELEASED"}:
            continue
        original = parents.get(source.name)
        if original or links.get("DEFERRED_TRANSACTION_ID"):
            row = frappe._dict(source, transaction_status="DEFERRED_RELEASED")
            row.release_transaction_id = source.transaction_id
            if original:
                row.transaction_id = original.transaction_id
                row.original_transaction_date = original.report_posted_date
                row.related_releases = [source]
                if (row.detail_group == "logistics" and not (row.manual_items or row.manual_child_items)
                        and (row.currency_code, row.net_amount) == (original.currency_code, original.net_amount)):
                    for name in ("manual_items", "manual_item_ids", "manual_item_amounts", "manual_child_items",
                                 "manual_child_item_ids", "manual_child_item_amounts"):
                        row[name] = original.get(name)
            selected.append(row)
        else:
            selected.append(source)
    return selected


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


def _summarize_detail_rows(rows, detail_group="", mappings=None, cost_centers=None):
    """Summarize every filtered detail row, including rows outside the current page."""
    grouped = {}
    currencies = {}
    row_mismatches = []
    amount_fields = ("net", "sales", "fees")
    item_sku_candidates = defaultdict(set)
    if detail_group == "logistics":
        for mapping in mappings or []:
            if mapping.get("平台sku") and mapping.get("物料id"):
                item_sku_candidates[(mapping.get("店铺") or "", mapping.get("站点id") or "",
                                     mapping.物料id)].add(mapping.平台sku)
    item_skus = {key: next(iter(values)) for key, values in item_sku_candidates.items()
                 if len(values) == 1}

    for row in rows:
        currency = row.currency_code or "未知"
        logistics = detail_group == "logistics"
        net = row.get("display_net_amount") if detail_group == "refund" else None
        amounts = {
            "net": Decimal(str(_money(row.net_amount if net is None else net))),
            "sales": Decimal(str(_money(row.product_sales))),
            "fees": Decimal(str(_money(row.display_fees))) if row.get("display_fees") is not None else
                    sum((Decimal(str(_money(row.get(field)))) for field in
                         ("selling_fees", "fba_fees", "other_transaction_fees")), Decimal(0)),
        }
        total = currencies.setdefault(currency, {"currency": currency, "count": 0,
                                                  **{field: Decimal(0) for field in amount_fields}})
        total["count"] += 1
        for field in amount_fields:
            total[field] += amounts[field]

        awd_children = (row.get("awd_fee_details") or []) if logistics else []
        if awd_children:
            parts = [(
                child.get("msku") or row.sku or "",
                {"net": Decimal(str(_money(child["amount"]))),
                 "sales": Decimal(0), "fees": Decimal(0)},
                child.get("manual_items") or [],
                ((child.get("shipment_id") or child.get("tracking_id")
                  or f"{child.get('batch')}:{child.get('source_row')}")
                 if child.get("source_type") in ("Processing", "Transportation") else ""),
                child.get("tracking_id") or child.get("shipment_id") or "",
            ) for child in awd_children]
        else:
            item_children = (row.get("item_lines") or []) if logistics else []
            attached = ([item for child in item_children for item in child.get("manual_items", [])]
                        if item_children else row.get("manual_items") or [])
            inbound_shipment = (row.amazon_order_id or "") if logistics and row.description == "AGSGlobalInboundTransportation" else ""
            parts = [(row.sku or "", amounts, attached, inbound_shipment, inbound_shipment)]

        allocated_parts = []
        for source_sku, part_amounts, attached, shipment_key, shipment_label in parts:
            part_net = part_amounts["net"]
            if not logistics or not attached:
                allocated_parts.append((source_sku, "", part_amounts, attached,
                                        shipment_key, shipment_label))
                continue
            sign = Decimal(-1) if part_net < 0 else Decimal(1)
            allocated = Decimal(0)
            for item in attached:
                if item.get("amount") is None:
                    continue
                item_amount = Decimal(str(item["amount"]))
                signed_amount = sign * item_amount
                allocated += signed_amount
                item_sku = item_skus.get((row.store or "", row.marketplace_id or "", item["name"])) or source_sku
                allocated_parts.append((item_sku, item["name"],
                                        {"net": signed_amount, "sales": Decimal(0), "fees": Decimal(0)},
                                        [item], shipment_key, shipment_label))
            residual = part_net - allocated
            if residual or not allocated_parts:
                allocated_parts.append((source_sku, "", {"net": residual,
                                       "sales": part_amounts["sales"], "fees": part_amounts["fees"]},
                                        [], shipment_key, shipment_label))

        allocated_net = sum((part[2]["net"] for part in allocated_parts), Decimal(0))
        if allocated_net.quantize(Decimal("0.01")) != amounts["net"].quantize(Decimal("0.01")):
            row_mismatches.append({
                "transaction": row.transaction_id or row.name,
                "source_net": float(amounts["net"]), "summary_net": float(allocated_net),
            })

        for sku, item_id, part_amounts, attached, shipment_key, shipment_label in allocated_parts:
            key = ((row.store or "", row.marketplace_id or "") if logistics else ()) + (
                currency, row.transaction_type or "未分类", row.description or "", sku, item_id,
                shipment_key)
            group = grouped.setdefault(key, {
                "currency": currency, "transaction_type": row.transaction_type or "未分类",
                "description": row.description or "", "sku": sku, "item_id": item_id, "count": 0,
                "shipment_id": shipment_key, "shipment_label": shipment_label,
                "store": row.store or "", "marketplace_id": row.marketplace_id or "",
                "cost_center": ((row.store if row.store in (cost_centers or []) else DEFAULT_COST_CENTER)
                                if logistics else ""),
                "account": (STORAGE_ACCOUNT if row.description in STORAGE_DESCRIPTIONS
                            else LOGISTICS_ACCOUNT) if logistics else "",
                "manual_items": [],
                **{field: Decimal(0) for field in amount_fields},
            })
            group["count"] += 1
            for field in amount_fields:
                group[field] += part_amounts[field]
            known = {item["name"]: item for item in group["manual_items"]}
            for item in attached:
                if item["name"] in known:
                    if item.get("amount") is not None:
                        previous = known[item["name"]]
                        previous["amount"] = float(
                            Decimal(str(previous.get("amount") or 0)) + Decimal(str(item["amount"]))
                        )
                else:
                    copy = dict(item)
                    group["manual_items"].append(copy)
                    known[item["name"]] = copy

    def as_json(result):
        return {key: float(value) if isinstance(value, Decimal) else value
                for key, value in result.items()}

    # Compare the original transaction total with the rows users will copy into a journal entry.
    grouped_nets = defaultdict(lambda: Decimal(0))
    for group in grouped.values():
        grouped_nets[group["currency"]] += group["net"]
    reconciliation = []
    for currency in sorted(set(currencies) | set(grouped_nets)):
        source_net = currencies.get(currency, {}).get("net", Decimal(0)).quantize(Decimal("0.01"))
        summary_net = grouped_nets[currency].quantize(Decimal("0.01"))
        reconciliation.append({
            "currency": currency, "source_net": float(source_net),
            "summary_net": float(summary_net),
            "difference": float(summary_net - source_net),
            "matched": source_net == summary_net,
        })

    return {
        "count": len(rows),
        "currencies": [as_json(currencies[key]) for key in sorted(currencies)],
        "reconciliation": reconciliation,
        "row_mismatch_count": len(row_mismatches),
        "row_mismatches": row_mismatches[:10],
        "rows": [as_json(grouped[key]) for key in sorted(
            grouped, key=lambda key: (key[0], -abs(grouped[key]["net"]), *key[1:]))],
    }


def _attach_order_report_dates(rows, doctype, date_field):
    """Use the Amazon order number and deduplicate dates across report copies."""
    order_ids = {row.amazon_order_id for row in rows if row.amazon_order_id}
    dates_by_order = defaultdict(set)
    for shipment in _read_order_summary_rows(
        doctype, ["order_id", "shipment_at", "raw_json"],
        {"shipment_at": ["is", "set"]}, "order_id", order_ids,
    ):
        try:
            raw = json.loads(shipment.raw_json or "{}")
        except (TypeError, ValueError):
            raw = {}
        source_timestamp = raw.get("shipment-date") if isinstance(raw, dict) else None
        pacific_time, _ = _report_time(shipment.shipment_at, source_timestamp)
        if pacific_time:
            dates_by_order[shipment.order_id].add(pacific_time.date().isoformat())

    for row in rows:
        row[date_field] = sorted(dates_by_order.get(row.amazon_order_id, ()))


def _read_order_summary_rows(doctype, fields, filters, key, values):
    values = sorted(values)
    for offset in range(0, len(values), 500):
        yield from frappe.get_all(
            doctype, fields=fields, limit_page_length=0,
            filters={**filters, key: ["in", values[offset:offset + 500]]},
        )


def _order_summary_shipments(report, filters, key, values, store_scopes):
    fulfilled = report == "fulfilled"
    doctype = FULFILLED_SHIPMENT_DOCTYPE if fulfilled else CUSTOMER_SALE_DOCTYPE
    fields = ["name", "amazon_store", "order_id", "seller_sku", "fulfillment_center_id",
              "shipment_at", "raw_json"]
    fields += (["shipment_id", "shipment_item_id", "amazon_order_item_id", "quantity_shipped"]
               if fulfilled else ["seller_id", "quantity"])
    # The fulfilled report can return the same shipment for several requests.
    # Preserve repeated lines within one request when the source has no line ID.
    copies = defaultdict(lambda: defaultdict(list))
    for row in _read_order_summary_rows(doctype, fields, filters, key, values):
        scope = store_scopes.get(row.amazon_store)
        if not fulfilled and not scope:
            scope = ("seller", row.seller_id)
        if not scope or not row.order_id or not row.seller_sku:
            continue
        try:
            raw = json.loads(row.raw_json or "{}")
        except (TypeError, ValueError):
            raw = {}
        source_date = raw.get("shipment-date") if isinstance(raw, dict) else None
        local, _ = _report_time(row.shipment_at, source_date)
        if not local:
            continue
        sku = row.seller_sku.strip()
        center = (row.fulfillment_center_id or "").strip().upper()
        line = {
            "key": (scope, sku, local.date().isoformat(), center),
            "order_id": row.order_id,
            "time": local.strftime("%H:%M:%S"),
            "quantity": cint(row.quantity_shipped if fulfilled else row.quantity),
        }
        if not fulfilled:
            yield line
        elif row.shipment_id and row.shipment_item_id:
            copies[(scope, row.shipment_id, row.shipment_item_id)][row.amazon_store].append(line)
        else:
            identity = (scope, row.order_id, sku, center, local, row.shipment_id,
                        row.amazon_order_item_id, line["quantity"])
            copies[identity][row.amazon_store].append(line)
    for by_store in copies.values():
        representative = max(sorted(by_store), key=lambda store: len(by_store[store]))
        yield from by_store[representative]


def _named_order_fees(nodes, inside_amazon_fees=False):
    """Read net amounts per fee type without counting Base, Tax and Promo twice."""
    fees = defaultdict(Decimal)
    if isinstance(nodes, dict):
        nodes = [nodes]
    for node in nodes or ():
        if not isinstance(node, dict):
            continue
        name = str(node.get("breakdownType") or "").strip()
        kind = re.sub(r"[^a-z0-9]", "", name.lower())
        children = node.get("breakdowns") or []
        amount = node.get("breakdownAmount")
        if kind == "amazonfees" and children:
            split = _named_order_fees(children, inside_amazon_fees=True)
            if isinstance(amount, dict) and amount:
                remainder = Decimal(str(_金额(amount))) - sum(split.values())
                if remainder:
                    split["AmazonFees"] += remainder
        elif kind == "amazonfees" or (name and inside_amazon_fees) or "fee" in kind or kind in {
                "commission", "refundcommission", "shippingchargeback", "giftwrapchargeback"}:
            value = Decimal(str(_金额(amount))) if amount else sum(
                (Decimal(str(_金额(leaf.get("breakdownAmount"))))
                 for leaf in _fee_leaf_nodes(children)), Decimal(0))
            split = {name: value}
        else:
            split = _named_order_fees(children, inside_amazon_fees)
        for fee, value in split.items():
            fees[fee] += value
    return fees


def _fee_leaf_nodes(nodes):
    if isinstance(nodes, dict):
        nodes = [nodes]
    for node in nodes or ():
        if not isinstance(node, dict):
            continue
        if node.get("breakdowns"):
            yield from _fee_leaf_nodes(node["breakdowns"])
        else:
            yield node


def _order_summary_amounts(currency, row):
    net = row.display_net_amount if row.get("display_net_amount") is not None else row.net_amount
    try:
        raw = json.loads(row.get("raw_json") or "{}")
    except (TypeError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    items = [item for item in raw.get("items") or [] if isinstance(item, dict)]
    sku_items = [
        item for item in items if str(_上下文值(item.get("contexts") or [], "sku") or "").strip()
        == (row.sku or "")
    ]
    fees = defaultdict(Decimal)
    for item in sku_items:
        for name, value in _named_order_fees(item.get("breakdowns")).items():
            fees[name] += value
    # A transaction-wide amount can only be used if it belongs to this SKU.
    if not fees and len(sku_items) == len(items):
        fees.update(_named_order_fees(raw.get("breakdowns")))
    if not fees:
        categories = row.get("display_fee_categories") or row
        fees = {field: categories.get(field) for field in
                ("selling_fees", "fba_fees", "other_transaction_fees")}
    return {"currency": currency, "net": _money(net), "sales": _money(row.product_sales),
            "shipping": _money(row.shipping_credits), "quantity": cint(row.quantity),
            "fees": {name: _money(amount) for name, amount in fees.items() if _money(amount)}}


def _order_summary_totals(rows):
    totals = defaultdict(lambda: {**dict.fromkeys(ORDER_TOTAL_FIELDS, Decimal(0)),
                                  "quantity": 0, "fees": defaultdict(Decimal)})
    for row in rows:
        total = totals[row["currency"]]
        total["quantity"] += row["quantity"]
        for field in ORDER_TOTAL_FIELDS:
            total[field] += Decimal(str(row[field]))
        for fee, amount in row["fees"].items():
            total["fees"][fee] += Decimal(str(amount))
    return [{"currency": currency, "quantity": values["quantity"],
             **{field: float(values[field]) for field in ORDER_TOTAL_FIELDS},
             "fees": {fee: float(amount) for fee, amount in values["fees"].items()}}
            for currency, values in sorted(totals.items())]


def _order_sku_price_lines(amounts):
    """Split into at most two cent prices whose extended amounts match the recorded sale."""
    quantity = amounts["quantity"]
    if quantity <= 0:
        return [{**amounts, "unit_price": None}]
    sales = Decimal(str(amounts["sales"]))
    cent = Decimal("0.01")
    lower_price, higher_quantity = divmod(int(sales / cent), quantity)
    if not higher_quantity:
        return [{**amounts, "unit_price": float(lower_price * cent)}]

    first, second = sorted(
        [(quantity - higher_quantity, lower_price), (higher_quantity, lower_price + 1)],
        key=lambda part: -part[0],
    )
    first_quantity, first_price = first
    second_quantity, second_price = second

    def first_share(amount):
        return (Decimal(str(amount)) * first_quantity / quantity).quantize(cent, rounding=ROUND_HALF_UP)

    fees = {key: Decimal(str(value)) for key, value in amounts["fees"].items()}
    first_fees = {key: first_share(value) for key, value in fees.items()}
    second_fees = {key: value - first_fees[key] for key, value in fees.items()}
    shipping = Decimal(str(amounts["shipping"]))
    first_shipping = first_share(shipping)
    net = Decimal(str(amounts["net"]))
    other = net - sales - shipping - sum(fees.values())
    first_sale = first_quantity * first_price * cent
    first_net = first_sale + first_shipping + sum(first_fees.values()) + first_share(other)
    return [
        {"currency": amounts["currency"], "quantity": qty, "unit_price": float(price * cent),
         "sales": float(qty * price * cent), "shipping": float(shipping_share), "net": float(net_share),
         "fees": {key: float(value) for key, value in fee_shares.items()}, "adjustment": adjustment}
        for qty, price, shipping_share, net_share, fee_shares, adjustment in (
            (first_quantity, first_price, first_shipping, first_net, first_fees, False),
            (second_quantity, second_price, shipping - first_shipping, net - first_net, second_fees, True),
        )
    ]


def _refund_summary(rows):
    sales_rows, fee_rows = {}, {}
    totals = defaultdict(lambda: {"sales": Decimal(0), "fees": Decimal(0), "net": Decimal(0)})
    centers = {row.store for row in rows if row.store}
    companies = {row.name: row.company for row in frappe.get_all(
        "Cost Center", filters={"name": ["in", sorted(centers)]},
        fields=["name", "company"], limit_page_length=0,
    )} if centers else {}
    accounts = {(row.company, row.account_number): row.name for row in frappe.get_all(
        "Account", filters={"company": ["in", sorted(set(companies.values()))],
                            "account_number": ["in", sorted(set(REFUND_FEE_ACCOUNTS.values()))],
                            "is_group": 0, "disabled": 0},
        fields=["name", "account_number", "company"], limit_page_length=0,
    )} if companies else {}

    for row in rows:
        currency = row.currency_code or "未知"
        amounts = {
            field: Decimal(str(_money(row.get(field))))
            for field in ("product_sales", "shipping_credits", "promotional_rebates", "product_sales_tax",
                          "shipping_credits_tax", "promotional_rebates_tax", "marketplace_withheld_tax")
        }
        sales = amounts["product_sales"]
        net = Decimal(str(_money(row.net_amount if row.display_net_amount is None else row.display_net_amount)))
        base = {
            "cost_center": row.store or "", "sku": row.sku or "", "item_id": row.corresponding_item or "",
            "item_name": row.item_name, "item_image": row.item_image, "product_name": row.product_name,
            "currency": currency,
        }
        key = (base["cost_center"], base["sku"], base["item_id"], currency)
        quantity = abs(cint(row.quantity))
        if quantity or sales:
            direction = 1 if sales > 0 else -1
            # Group equal rates after splitting each source line, so different
            # rates for the same SKU survive aggregation.
            price_lines = _order_sku_price_lines({
                "quantity": quantity, "sales": float(abs(sales)), "net": float(abs(sales)),
                "currency": currency, "fees": {}, "shipping": 0,
            })
            for line in price_lines:
                price = line["unit_price"]
                sale = sales_rows.setdefault((*key, direction, price), {
                    **base, "unit_price": price, "quantity": 0 if quantity else None, "amount": Decimal(0),
                })
                if quantity:
                    sale["quantity"] += direction * line["quantity"]
                sale["amount"] += direction * Decimal(str(line["sales"]))

        components = {code: Decimal(str(value)) for code, value in row.refund_fee_details.items()}
        components.update(shipping_credits=amounts["shipping_credits"], promotional_rebates=amounts["promotional_rebates"])
        components["tax_adjustment"] = sum(amounts[field] for field in (
            "product_sales_tax", "shipping_credits_tax", "promotional_rebates_tax", "marketplace_withheld_tax",
        ))
        components["other_adjustment"] = net - sales - sum(components.values())
        for code, value in components.items():
            if not value:
                continue
            fee = fee_rows.setdefault((*key, code), {
                **base, "fee_type": REFUND_FEE_LABELS.get(code, code),
                "account": accounts.get((companies.get(row.store), REFUND_FEE_ACCOUNTS.get(code)), ""),
                "amount": Decimal(0),
            })
            fee["amount"] += value

        totals[currency]["sales"] += sales
        totals[currency]["fees"] += net - sales
        totals[currency]["net"] += net

    return {
        "count": len(rows),
        "sales_rows": [{**row, "amount": float(row["amount"])} for row in sorted(
            sales_rows.values(), key=lambda row: (
                row["cost_center"], row["sku"], row["item_id"], row["currency"], row["unit_price"] or 0,
            ),
        )],
        "fee_rows": [{**row, "amount": float(row["amount"])} for _, row in sorted(fee_rows.items()) if row["amount"]],
        "totals": [{"currency": currency, **{key: float(value) for key, value in total.items()}}
                   for currency, total in sorted(totals.items())],
    }


def _order_reconciliation(rows, filters, truncated, order_count):
    stores = frappe.get_all("Amazon Store Configuration", limit_page_length=0,
                            fields=["name", "seller_id", "cost_center", "marketplace_id"])
    store_scopes = {row.name: ("seller", row.seller_id) if row.seller_id else ("store", row.name)
                    for row in stores}
    finance_scopes = {(row.cost_center, row.marketplace_id): store_scopes[row.name] for row in stores}
    products, orders = {}, {}
    finance_lines = []
    sku_summary = {}
    for row in rows:
        scope = finance_scopes.get((row.store, row.marketplace_id),
                                   ("finance", row.store or "", row.marketplace_id or ""))
        sku = (row.sku or "").strip()
        product = products.setdefault((scope, sku), {
            "sku": sku, "product_name": row.product_name, "items": {}, "stores": set(),
            "orders": [], "days": [], "quantity": 0,
        })
        item_id = row.corresponding_item or ""
        item = {"name": item_id, "label": row.item_name, "image": row.item_image}
        if item_id:
            product["items"][item_id] = item
        if row.store:
            product["stores"].add(row.store)
        order_key = (scope, sku, row.amazon_order_id or row.name)
        if order_key not in orders:
            orders[order_key] = {"id": row.amazon_order_id, "dates": set(), "amounts": [],
                                 "quantity": 0, "days": set(), "transaction_count": 0}
            product["orders"].append(orders[order_key])
        order = orders[order_key]
        if row.report_posted_date:
            order["dates"].add(row.report_posted_date.date().isoformat())
        quantity = cint(row.quantity)
        order["quantity"] += quantity
        order["transaction_count"] += 1
        product["quantity"] += quantity
        amounts = _order_summary_amounts(row.currency_code or "未知", row)
        order["amounts"].append(amounts)
        finance_lines.append(amounts)

        summary = sku_summary.setdefault(sku, {
            "sku": sku, "quantity": 0, "product_name": "", "items": {}, "item_amounts": defaultdict(list),
        })
        summary["quantity"] += quantity
        summary["product_name"] = summary["product_name"] or row.product_name
        if item_id:
            summary["items"][item_id] = item
        group_key = (row.store or "", item_id, amounts["currency"])
        summary["item_amounts"][group_key].append(amounts)

    selected_orders = {key for key, order in orders.items() if order["id"] and key[1]}
    active_scopes = {key[0] for key in selected_orders}
    source_stores = {name for name, scope in store_scopes.items() if scope in active_scopes}
    sellers = {scope[1] for scope in active_scopes if scope[0] == "seller"}
    report_filters = {}
    if source_stores:
        report_filters["fulfilled"] = {"amazon_store": ["in", sorted(source_stores)]}
    if sellers:
        report_filters["completed"] = {"seller_id": ["in", sorted(sellers)]}
    order_ids = {key[2] for key in selected_orders}
    centers = {}
    for report, source_filter in report_filters.items():
        for line in _order_summary_shipments(
                report, source_filter, "order_id", order_ids, store_scopes):
            scope, sku, day, center = line["key"]
            order_key = (scope, sku, line["order_id"])
            if order_key not in selected_orders:
                continue
            orders[order_key]["days"].add(day)
            group = centers.setdefault(line["key"], {
                "center": center, "orders": {}, "selected_fulfilled": 0, "selected_completed": 0,
                "fulfilled": None, "completed": None, "ledger": None, "countries": set(),
            })
            order = group["orders"].setdefault(line["order_id"], {"id": line["order_id"],
                                                                 "fulfilled": [], "completed": []})
            order[report].append({"time": line["time"], "quantity": line["quantity"]})
            group[f"selected_{report}"] += line["quantity"]

    if centers:
        days = sorted({key[2] for key in centers})
        skus = {key[1] for key in centers}
        # Read the full day for each source, even if the financial filter selects one order.
        shipment_range = ["between", [_report_boundary(days[0]) - timedelta(days=1),
                                      _report_boundary(days[-1], following_day=True) + timedelta(days=1)]]
        for report, source_filter in report_filters.items():
            for line in _order_summary_shipments(
                    report, {**source_filter, "shipment_at": shipment_range}, "seller_sku", skus, store_scopes):
                group = centers.get(line["key"])
                if group is not None:
                    group[report] = (group[report] or 0) + line["quantity"]

        ledger_filters = {"amazon_store": ["in", sorted(source_stores)], "event_type": "Shipments",
                          "event_at": ["between", [days[0], f"{days[-1]} 23:59:59.999999"]]}
        for row in _read_order_summary_rows(
                INVENTORY_LEDGER_DETAIL_DOCTYPE,
                ["amazon_store", "seller_sku", "event_at", "fulfillment_center", "country", "quantity"],
                ledger_filters, "seller_sku", skus):
            key = (store_scopes[row.amazon_store], (row.seller_sku or "").strip(),
                   getdate(row.event_at).isoformat(), (row.fulfillment_center or "").strip().upper())
            group = centers.get(key)
            if group is not None:
                group["ledger"] = (group["ledger"] or 0) - cint(row.quantity)
                if row.country:
                    group["countries"].add(row.country)

    statuses = Counter()
    product_days = defaultdict(lambda: defaultdict(list))
    for (scope, sku, day, center), group in sorted(centers.items()):
        counts = [group[field] for field in ("ledger", "fulfilled", "completed") if group[field] is not None]
        if not center:
            status = "missing"
        elif len(set(counts)) > 1:
            status = "difference"
        elif len(counts) == 3:
            status = "matched"
        else:
            status = "missing"
        statuses[status] += 1
        group["status"] = status
        group["orders"] = [group["orders"][key] for key in sorted(group["orders"])]
        group["countries"] = sorted(group["countries"])
        product_days[(scope, sku)][day].append(group)

    for key, product in products.items():
        product["items"] = list(product["items"].values())
        product["stores"] = sorted(product["stores"])
        product["totals"] = _order_summary_totals(
            amount for order in product["orders"] for amount in order["amounts"])
        product["order_count"] = sum(bool(order["id"]) for order in product["orders"])
        for order in product["orders"]:
            order["amounts"] = _order_summary_totals(order["amounts"])
            order["dates"] = sorted(order["dates"])
            order["days"] = sorted(order["days"])
        product["days"] = [{
            "date": day, "centers": entries,
            "outside_finance_period": bool((filters.date_from and day < str(filters.date_from))
                                           or (filters.date_to and day > str(filters.date_to))),
        } for day, entries in sorted(product_days[key].items(), reverse=True)]

    for summary in sku_summary.values():
        summary["items"] = list(summary["items"].values())
        summary["price_lines"] = []
        for (cost_center, item_id, _currency), amounts in sorted(summary.pop("item_amounts").items()):
            total, = _order_summary_totals(amounts)
            summary["price_lines"].extend(
                {**line, "item_id": item_id, "cost_center": cost_center}
                for line in _order_sku_price_lines(total)
            )
    fee_names = {fee for line in finance_lines for fee in line["fees"]}
    fee_columns = [fee for fee in ORDER_FEE_LABELS if fee in fee_names]
    fee_columns.extend(sorted(fee_names.difference(ORDER_FEE_LABELS)))
    return {
        "products": sorted(products.values(), key=lambda item: (-item["quantity"], item["sku"])),
        "sku_summary": sorted(sku_summary.values(), key=lambda item: (-item["quantity"], item["sku"])),
        "order_count": order_count,
        "finance_rows": len(rows), "totals": _order_summary_totals(finance_lines),
        "fee_columns": [{"key": fee, "label": ORDER_FEE_LABELS.get(fee, fee)} for fee in fee_columns],
        "center_count": len(centers), "matched_count": statuses["matched"],
        "difference_count": statuses["difference"], "missing_count": statuses["missing"],
        "truncated": truncated,
    }


@frappe.whitelist()
def get_finance_dashboard_data(filters=None, page=1, page_size=300, order_reconciliation=0):
    f = _filters(filters)
    pending = f.period == "pending"
    pending_month = _selected_pending_month(f)
    detail_group = "logistics" if f.detail_group == "storage" else (f.detail_group or "")
    fields = [
        "name", "transaction_sku_key", "transaction_id", "amazon_order_id",
        "amazon_order_item_id", "sku", "asin", "store", "country", "marketplace_id",
        "transaction_type", "transaction_status", "description", "settlement_id",
        "financial_event_group_id", "fulfillment_channel", "product_name", "quantity",
        "corresponding_item", "corresponding_item_name", "manual_item_ids_json",
        "manual_item_amounts_json", "manual_child_items_json",
        "manual_child_item_amounts_json", "posted_date", "release_date",
        "currency_code", "fetched_at", "raw_json", *AMOUNT_FIELDS,
    ]
    db_filters = _build_db_filters(f)
    rows = frappe.get_all(
        DOCTYPE, filters=db_filters, fields=fields,
        order_by="posted_date desc, modified desc", limit_page_length=100000,
    )
    truncated = len(rows) >= 100000
    reverse_links = _reverse_release_rows(rows, fields, pending)
    all_rows, parent_for_child, release_children = _release_relationships(rows + reverse_links, fields)
    for row in all_rows:
        try:
            source = json.loads(row.get("raw_json") or "{}")
        except (TypeError, ValueError):
            source = {}
        posted_at = source.get("postedDate") if isinstance(source, dict) else None
        row.report_posted_date, row.report_timezone = _report_time(row.posted_date, posted_at)
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
    manual_ids = set()
    for row in all_rows:
        row.manual_item_ids = []
        row.manual_child_item_ids = {}
        row.manual_item_amounts = _saved_item_amounts(row.manual_item_amounts_json)
        try:
            child_amounts = json.loads(row.manual_child_item_amounts_json or "{}")
        except (TypeError, ValueError):
            child_amounts = {}
        row.manual_child_item_amounts = {
            key: _saved_item_amounts(value) for key, value in child_amounts.items()
            if isinstance(key, str)
        } if isinstance(child_amounts, dict) else {}
        if row.detail_group != "logistics":
            continue
        try:
            selected = json.loads(row.manual_item_ids_json or "[]")
        except (TypeError, ValueError):
            selected = []
        if isinstance(selected, list):
            row.manual_item_ids = list(dict.fromkeys(item for item in selected if isinstance(item, str)))
            manual_ids.update(row.manual_item_ids)
        try:
            selected_children = json.loads(row.manual_child_items_json or "{}")
        except (TypeError, ValueError):
            selected_children = {}
        if isinstance(selected_children, dict):
            for key, selected in selected_children.items():
                if isinstance(key, str) and isinstance(selected, list):
                    row.manual_child_item_ids[key] = list(dict.fromkeys(
                        item for item in selected if isinstance(item, str)))
                    manual_ids.update(row.manual_child_item_ids[key])
    manual_info = {item.name: item for item in frappe.get_all(
        "Item", filters={"name": ["in", list(manual_ids)]},
        fields=["name", "item_name", "image"], limit_page_length=0,
    )} if manual_ids else {}
    def selected_items(selected, amounts):
        return [
            {"name": item, "item_name": manual_info[item].item_name,
             "image": manual_info[item].image, "amount": amounts.get(item)}
            for item in selected if item in manual_info
        ]
    for row in all_rows:
        row.manual_items = selected_items(row.manual_item_ids, row.manual_item_amounts)
        row.manual_child_items = {
            key: selected_items(selected, row.manual_child_item_amounts.get(key, {}))
            for key, selected in row.manual_child_item_ids.items()
        }

    search = (f.search or "").strip().lower()
    binding = f.binding or ""
    scope = f.scope or ""
    item_filter = f.corresponding_item or ""
    def matches_row(row, original=None):
        if any(f.get(key) and str(row.get(key) or "") != str(f[key]) for key in FILTER_OPTION_FIELDS):
            return False
        if search:
            haystack = " ".join(
                str(record.get(key) or "") for record in (row, original) if record
                for key in ("transaction_id", "amazon_order_id", "sku", "asin", "product_name",
                            "corresponding_item", "item_name", "description", "settlement_id")
            ).lower()
            if search not in haystack:
                return False
        if binding == "bound" and not row.is_bound:
            return False
        if binding == "unbound" and (row.is_bound or not row.is_product):
            return False
        if scope == "product" and not row.is_product:
            return False
        if scope == "store" and row.is_product:
            return False
        return not item_filter or row.corresponding_item == item_filter

    report_start = str(getdate(f.date_from)) if f.date_from and not pending else ""
    report_end = str(getdate(f.date_to)) if f.date_to and not pending else ""

    def in_selected_dates(timestamp):
        day = str(timestamp or "")[:10]
        return bool(day) and (not report_start or day >= report_start) and (not report_end or day <= report_end)

    period_rows = _finance_period_rows(rows, parent_for_child, release_children, pending)
    pending_months = []
    if pending:
        # Derive month buttons before applying the selected month or other filters.
        pending_months = sorted({row.report_posted_date.strftime("%Y-%m") for row in period_rows
                                 if row.report_posted_date})
        option_rows = [row for row in period_rows if not pending_month
                       or str(row.report_posted_date or "")[:7] == pending_month]
    else:
        option_rows = [row for row in period_rows if in_selected_dates(row.report_posted_date)]
    economic_rows = [row for row in option_rows if matches_row(row, parent_for_child.get(row.name))]
    _attach_order_report_dates(economic_rows, FULFILLED_SHIPMENT_DOCTYPE, "shipment_dates")
    shipment_months = sorted({
        day[:7]
        for row in economic_rows if not detail_group or row.detail_group == detail_group
        for day in row.shipment_dates
    })
    groups_before_shipment_filter = set()
    if f.shipment_month:
        groups_before_shipment_filter = {row.detail_group for row in economic_rows}
        economic_rows = [
            row for row in economic_rows
            if any(day[:7] == f.shipment_month for day in row.shipment_dates)
        ]
    order_rows = [row for row in economic_rows if row.detail_group == "order"]
    order_count = len({
        (row.store, row.marketplace_id, row.amazon_order_id)
        for row in order_rows if row.amazon_order_id
    })

    group_counts = defaultdict(int)
    report_row_count = 0
    for row in economic_rows:
        row.finance_outside_period = bool(row.original_transaction_date and not in_selected_dates(row.original_transaction_date))
        row.item_lines, row.display_net_amount = _item_display(row)
        if detail_group == row.detail_group == "refund":
            row.refund_fee_details = _order_summary_amounts(row.currency_code, row)["fees"]
            row.display_fees = _money(sum(row.refund_fee_details.values()))
        if row.detail_group == "logistics":
            for line in row.item_lines:
                line.manual_key = f"item:{line.line_no}"
                line.manual_items = row.manual_child_items.get(line.manual_key, [])
        line_count = max(1, len(row.item_lines))
        report_row_count += line_count
        group_counts[row.detail_group] += line_count
    group_counts["order"] = order_count
    group_counts["refund"] = sum(row.detail_group == "refund" for row in economic_rows)
    if detail_group == "order" and cint(order_reconciliation) and not pending:
        return _order_reconciliation(order_rows, f, truncated, order_count)

    detail_rows = [row for row in economic_rows if not detail_group or row.detail_group == detail_group]
    if detail_group == "logistics":
        _attach_awd_fee_details(detail_rows)

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
        key = _transaction_key(row, row.release_transaction_id) if row.transaction_id or row.release_transaction_id else (row.store, row.marketplace_id, row.name)
        if key not in unique_transactions:
            unique_transactions.add(key)
            currencies[currency]["net"] += amount
            currencies[currency]["inflow" if amount >= 0 else "outflow"] += abs(amount)
            currencies[currency]["sales"] += _money(row.product_sales)
            fee_amount = (row.display_fees if row.get("display_fees") is not None else
                          _money(row.selling_fees) + _money(row.fba_fees) + _money(row.other_transaction_fees))
            currencies[currency]["fees"] += abs(_money(fee_amount))
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
        d["fees"] += _money(row.display_fees) if row.get("display_fees") is not None else (
            _money(row.selling_fees) + _money(row.fba_fees) + _money(row.other_transaction_fees))
        d["tax"] += _money(row.product_sales_tax) + _money(row.shipping_credits_tax) + _money(row.marketplace_withheld_tax)
        d["promotion"] += _money(row.promotional_rebates) + _money(row.promotional_rebates_tax)
        d["count"] += 1
        for label, field in (("销售费用", "selling_fees"), ("FBA费用", "fba_fees"), ("其他交易费用", "other_transaction_fees"), ("平台代扣税", "marketplace_withheld_tax"), ("促销折扣", "promotional_rebates"), ("销售税", "product_sales_tax")):
            fee_totals[currency][label] += _money(
                (row.get("display_fee_categories") or {}).get(field, row.get(field)))
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
    display_rows = sorted(detail_rows, key=lambda row: (str(row.report_posted_date or ""), row.name), reverse=True)
    start = (page - 1) * page_size
    page_rows = display_rows[start:start + page_size]
    if detail_group == "order":
        _attach_order_report_dates(page_rows, CUSTOMER_SALE_DOCTYPE, "completed_dates")
    if detail_group == "removal" and pending:
        for row in page_rows:
            _attach_liquidation_breakdown(row)
    if detail_group != "logistics":
        _attach_awd_fee_details(page_rows)
    journal_summary = _platform_journal_summary(detail_rows, mappings) if detail_group in JOURNAL_DETAIL_GROUPS and not pending else []
    funds_summary = _funds_summary(display_rows) if detail_group == "funds" else None
    # The complete Amazon response is retained in its DocType; do not send it to the browser.
    for row in [*all_rows, *page_rows]:
        row.pop("raw_json", None)
    options = {
        key: sorted({str(r.get(key)) for r in option_rows if r.get(key)})
        for key in FILTER_OPTION_FIELDS
    }
    detail_groups = []
    retained_groups = () if pending else ("order", "refund")
    if detail_group and (pending_month or f.shipment_month):
        retained_groups = (*retained_groups, detail_group)
    if report_row_count or groups_before_shipment_filter or detail_group in retained_groups:
        detail_groups.append({"key": "", "label": "全部", "count": report_row_count})
    detail_groups.extend(
        {"key": key, "label": label, "count": group_counts[key]}
        for key, label in DETAIL_GROUPS
        if group_counts[key] > 0 or key in groups_before_shipment_filter
        or (key in retained_groups and detail_group == key)
    )
    cost_center_options = [center.name for center in frappe.get_all(
        "Cost Center", filters={"is_group": 0}, fields=["name"], order_by="name asc",
        limit_page_length=0,
    )] if detail_group == "logistics" else []
    account_options = [account.name for account in frappe.get_all(
        "Account", filters={"company": frappe.get_cached_value("Cost Center", DEFAULT_COST_CENTER, "company"),
                            "is_group": 0, "root_type": "Expense"},
        fields=["name"], order_by="name asc", limit_page_length=0,
    )] if detail_group == "logistics" else []
    return {
        "period": "pending" if pending else "released",
        "summary": {"row_count": len(economic_rows), "report_row_count": report_row_count, "transaction_count": len(unique_transactions), "release_count": sum(len(row.get("related_releases") or []) for row in economic_rows), "sku_count": len(products), "product_count": product_count, "store_fee_count": store_fee_count, "bound_count": bound, "unbound_count": product_count - bound, "binding_rate": round(bound * 100 / product_count, 1) if product_count else 0, "currencies": dict(currencies), "truncated": truncated},
        "daily": {day: dict(values) for day, values in sorted(daily.items())},
        "fees": {c: dict(v) for c, v in fee_totals.items()},
        "types": {c: dict(v) for c, v in type_totals.items()},
        "stores": {c: dict(v) for c, v in store_totals.items()},
        "products": product_list[:500], "settlements": settlement_list[:200],
        "rows": page_rows, "options": options, "available_months": _available_months(),
        "pending_months": pending_months,
        "shipment_months": shipment_months,
        "detail_summary": _summarize_detail_rows(detail_rows, detail_group, mappings, cost_center_options),
        "journal_summary": journal_summary,
        "funds_summary": funds_summary,
        "refund_summary": _refund_summary(detail_rows) if detail_group == "refund" and not pending else None,
        "cost_center_options": cost_center_options,
        "account_options": account_options,
        "detail_groups": detail_groups,
        "order_list_totals": {
            "orders": group_counts["order"],
            "units": sum(cint(row.quantity) for row in order_rows),
        } if detail_group == "order" else None,
        "pagination": {"page": page, "page_size": page_size, "total": len(display_rows), "display_total": len(display_rows) if detail_group == "refund" else sum(max(1, len(row.item_lines)) for row in display_rows), "raw_total": len(detail_rows), "pages": max(1, (len(display_rows) + page_size - 1) // page_size)},
    }


@frappe.whitelist()
def bind_finance_item(store, marketplace_id, item, asin=None, sku=None, transaction_type=None):
    frappe.only_for("System Manager")
    if not item or not frappe.db.exists("Item", item):
        frappe.throw("请选择有效的ERPNext物料")
    asin, sku = (asin or "").strip().upper(), (sku or "").strip().upper()
    is_fnsku = transaction_type == "RemovalShipment" and bool(sku)
    if not asin and not sku:
        frappe.throw("ASIN、SKU和FNSKU至少需要一个")
    filters = {"店铺": store, "站点id": marketplace_id}
    if asin and not is_fnsku: filters["平台asin"] = asin
    if sku: filters["平台fnsku" if is_fnsku else "平台sku"] = sku
    name = frappe.db.get_value(MAPPING_DOCTYPE, filters, "name")
    if name:
        doc = frappe.get_doc(MAPPING_DOCTYPE, name)
        doc.物料id = item; doc.启用 = 1; doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({"doctype": MAPPING_DOCTYPE, "启用": 1, "店铺": store, "站点id": marketplace_id, "平台asin": asin, "平台fnsku": sku if is_fnsku else "", "平台sku": "" if is_fnsku else sku, "物料id": item}).insert(ignore_permissions=True)
    clauses, values = ["store=%s", "marketplace_id=%s"], [store, marketplace_id]
    if is_fnsku:
        clauses.append("transaction_type=%s")
        values.append("RemovalShipment")
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


@frappe.whitelist()
def search_logistics_items(query=""):
    frappe.only_for("System Manager")
    query = str(query or "").strip()[:100]
    filters = {"disabled": 0}
    or_filters = [["Item", "name", "like", f"%{query}%"], ["Item", "item_name", "like", f"%{query}%"]] if query else None
    return frappe.get_all(
        "Item", filters=filters, or_filters=or_filters,
        fields=["name", "item_name", "image"], order_by="item_name asc",
        limit_page_length=40,
    )


@frappe.whitelist()
def save_logistics_items(transaction_name, item_ids, item_amounts=None, child_key=None):
    frappe.only_for("System Manager")
    if isinstance(item_ids, str):
        try:
            item_ids = json.loads(item_ids)
        except ValueError:
            frappe.throw("物料选择格式无效")
    if not isinstance(item_ids, list) or len(item_ids) > 50:
        frappe.throw("每条交易最多关联 50 个物料")
    item_ids = list(dict.fromkeys(str(item or "").strip() for item in item_ids))
    if any(not item for item in item_ids):
        frappe.throw("物料编号不能为空")
    if isinstance(item_amounts, str):
        try:
            item_amounts = json.loads(item_amounts)
        except ValueError:
            frappe.throw("物料金额格式无效")
    if not isinstance(item_amounts, dict) or set(item_amounts) != set(item_ids):
        frappe.throw("请为每个已选物料填写金额")
    amounts = {}
    for item in item_ids:
        try:
            amount = Decimal(str(item_amounts[item]))
            if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal("0.01")):
                raise ValueError
        except (TypeError, ValueError, ArithmeticError):
            frappe.throw("物料金额必须是正数或零，且最多两位小数")
        amounts[item] = amount
    row = frappe.get_doc(DOCTYPE, transaction_name)
    if _transaction_group(row) != "logistics":
        frappe.throw("只有仓储费 / 入库与物流交易可以选择关联物料")
    items = frappe.get_all(
        "Item", filters={"name": ["in", item_ids], "disabled": 0},
        fields=["name", "item_name", "image"], limit_page_length=0,
    ) if item_ids else []
    by_name = {item.name: item for item in items}
    if len(by_name) != len(item_ids):
        frappe.throw("选择的物料不存在或已停用")
    row.manual_child_items = {}
    _attach_awd_fee_details([row])
    item_lines, display_net = _item_display(row)
    targets = {_awd_child_key(detail): Decimal(str(_money(detail["amount"])))
               for detail in row.get("awd_fee_details") or []}
    targets.update({f"item:{line.line_no}": Decimal(str(_money(line.net_amount)))
                    for line in item_lines})
    if child_key:
        if child_key not in targets:
            frappe.throw("费用明细已变化，请刷新页面后重新选择物料")
        target = targets[child_key]
    else:
        if targets:
            frappe.throw("这笔费用有子级，请在子级分配物料和金额")
        target = Decimal(str(_money(display_net if display_net is not None else row.net_amount)))
    target = abs(target)
    if sum(amounts.values(), Decimal(0)) != target:
        frappe.throw(f"物料金额合计必须等于本条净额的绝对值 {target:.2f} {row.currency_code or ''}")
    stored_amounts = {item: float(amounts[item]) for item in item_ids}
    result = {"manual_item_ids": item_ids, "manual_item_amounts": stored_amounts,
              "manual_items": [{"name": item, "item_name": by_name[item].item_name,
                                "image": by_name[item].image, "amount": stored_amounts[item]}
                               for item in item_ids]}
    if child_key:
        try:
            selections = json.loads(row.manual_child_items_json or "{}")
        except (TypeError, ValueError):
            selections = {}
        if not isinstance(selections, dict):
            selections = {}
        if item_ids:
            selections[child_key] = item_ids
        else:
            selections.pop(child_key, None)
        try:
            saved_amounts = json.loads(row.manual_child_item_amounts_json or "{}")
        except (TypeError, ValueError):
            saved_amounts = {}
        if not isinstance(saved_amounts, dict):
            saved_amounts = {}
        if item_ids:
            saved_amounts[child_key] = stored_amounts
        else:
            saved_amounts.pop(child_key, None)
        frappe.db.set_value(DOCTYPE, transaction_name, {
            "manual_child_items_json": json.dumps(selections, ensure_ascii=False),
            "manual_child_item_amounts_json": json.dumps(saved_amounts, ensure_ascii=False),
        }, update_modified=False)
        return {"child_key": child_key, **result}
    frappe.db.set_value(DOCTYPE, transaction_name, {
        "manual_item_ids_json": json.dumps(item_ids, ensure_ascii=False),
        "manual_item_amounts_json": json.dumps(stored_amounts, ensure_ascii=False),
    }, update_modified=False)
    return result
