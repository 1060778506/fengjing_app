import frappe
from frappe.utils import flt


# ============================================================
# 日记账外币辅助金额
# custom_借方外币 / custom_贷方外币仅作为录入辅助字段；
# 最终总账仍使用 ERPNext 原生借贷金额字段。
# ============================================================
def validate_journal_entry_foreign_amounts(doc, method=None):
    """保存日记账前，根据凭证中的唯一外币汇率换算本币科目金额。"""
    if isinstance(doc, dict):
        doc = frappe.get_doc(doc)

    accounts = doc.get("accounts") or []
    debit_foreign_total = sum(
        flt(row.get("custom_借方外币")) for row in accounts
    )
    credit_foreign_total = sum(
        flt(row.get("custom_贷方外币")) for row in accounts
    )
    doc.custom_借方外币合计 = flt(
        debit_foreign_total,
        doc.precision("custom_借方外币合计"),
    )
    doc.custom_贷方外币合计 = flt(
        credit_foreign_total,
        doc.precision("custom_贷方外币合计"),
    )

    rows_with_foreign_input = [
        row
        for row in accounts
        if flt(row.get("custom_借方外币")) or flt(row.get("custom_贷方外币"))
    ]
    if not rows_with_foreign_input:
        return

    company_currency = frappe.get_cached_value("Company", doc.company, "default_currency")
    rate_candidates = {}

    for row in accounts:
        account_currency = row.get("account_currency")
        if not account_currency and row.get("account"):
            account_currency = frappe.get_cached_value(
                "Account", row.account, "account_currency"
            )
            row.account_currency = account_currency

        exchange_rate = flt(row.get("exchange_rate"))
        if (
            account_currency
            and account_currency != company_currency
            and exchange_rate > 0
            and abs(exchange_rate - 1) > 1e-12
        ):
            key = (account_currency, round(exchange_rate, 12))
            rate_candidates[key] = exchange_rate

    if len(rate_candidates) > 1:
        rates = "、".join(
            f"{currency}：{rate:g}"
            for currency, rate in sorted(rate_candidates)
        )
        frappe.throw(
            f"检测到多个不同的外币汇率（{rates}），无法确定借方外币或贷方外币应使用哪个汇率。"
            "请清空辅助外币字段后手动填写，或保证凭证中只有一种外币汇率。",
            title="无法自动换算外币",
        )

    conversion_rate = (
        next(iter(rate_candidates.values())) if rate_candidates else 1
    )

    for row in rows_with_foreign_input:
        debit_foreign = flt(row.get("custom_借方外币"))
        credit_foreign = flt(row.get("custom_贷方外币"))

        if debit_foreign < 0 or credit_foreign < 0:
            frappe.throw(
                f"第 {row.idx} 行：借方外币和贷方外币不能填写负数，请通过借贷方向表示金额方向。"
            )

        if debit_foreign and credit_foreign:
            frappe.throw(
                f"第 {row.idx} 行：借方外币和贷方外币不能同时填写。"
            )

        account_currency = row.get("account_currency")
        if account_currency != company_currency:
            frappe.throw(
                f"第 {row.idx} 行：外币辅助字段只能用于公司本币（{company_currency}）科目。"
                "外币科目请直接填写ERPNext原生的借方或贷方账户币种金额。"
            )

        row.exchange_rate = 1
        if debit_foreign:
            precision = row.precision("debit_in_account_currency")
            row.debit_in_account_currency = flt(
                debit_foreign * conversion_rate, precision
            )
            row.credit_in_account_currency = 0
        elif credit_foreign:
            precision = row.precision("credit_in_account_currency")
            row.credit_in_account_currency = flt(
                credit_foreign * conversion_rate, precision
            )
            row.debit_in_account_currency = 0
