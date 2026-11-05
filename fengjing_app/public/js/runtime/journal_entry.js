// ERPNext 日记账凭证的外币辅助金额与合计。

// ============================================================
// 日记账外币辅助金额实时换算
// - 借方外币：换算后写入原生借方账户币种金额
// - 贷方外币：换算后写入原生贷方账户币种金额
// - 没有非 1 汇率时按 1:1 处理
// ============================================================
(function () {
    "use strict";

    const RATE_EPSILON = 1e-12;
    const RECALC_DELAYS = [0, 350, 1200];

    function getCompanyCurrency(frm) {
        const company = frappe.get_doc(":Company", frm.doc.company);
        return company?.default_currency || frappe.boot?.sysdefaults?.currency || "";
    }

    function getRateCandidates(frm) {
        const companyCurrency = getCompanyCurrency(frm);
        const candidates = new Map();

        (frm.doc.accounts || []).forEach(row => {
            const rate = flt(row.exchange_rate);
            const currency = String(row.account_currency || "").trim();

            if (
                currency &&
                companyCurrency &&
                currency !== companyCurrency &&
                rate > 0 &&
                Math.abs(rate - 1) > RATE_EPSILON
            ) {
                const key = `${currency}|${rate.toFixed(12)}`;
                candidates.set(key, { currency, rate });
            }
        });

        return Array.from(candidates.values());
    }

    function showWarningOnce(frm, key, message) {
        if (frm.__fengjingForeignAmountWarning === key) return;
        frm.__fengjingForeignAmountWarning = key;
        frappe.show_alert({ message: __(message), indicator: "orange" }, 7);
    }

    function clearWarning(frm) {
        frm.__fengjingForeignAmountWarning = "";
    }

    async function setNativeAmounts(row, debit, credit) {
        await frappe.model.set_value(row.doctype, row.name, {
            debit_in_account_currency: debit,
            credit_in_account_currency: credit
        });
    }

    async function updateForeignTotals(frm) {
        if (frm.doc.docstatus !== 0) return;

        const totals = (frm.doc.accounts || []).reduce(
            (result, row) => {
                result.debit += flt(row.custom_借方外币);
                result.credit += flt(row.custom_贷方外币);
                return result;
            },
            { debit: 0, credit: 0 }
        );
        const debitTotal = flt(
            totals.debit,
            precision("custom_借方外币合计", frm.doc)
        );
        const creditTotal = flt(
            totals.credit,
            precision("custom_贷方外币合计", frm.doc)
        );
        const values = {};

        if (flt(frm.doc.custom_借方外币合计) !== debitTotal) {
            values.custom_借方外币合计 = debitTotal;
        }
        if (flt(frm.doc.custom_贷方外币合计) !== creditTotal) {
            values.custom_贷方外币合计 = creditTotal;
        }
        if (Object.keys(values).length) {
            await frm.set_value(values);
        }
    }

    async function recalculateRow(frm, cdt, cdn, options = {}) {
        if (frm.doc.docstatus !== 0) return;

        const row = locals[cdt]?.[cdn];
        if (!row) return;

        await updateForeignTotals(frm);

        const debitForeign = flt(row.custom_借方外币);
        const creditForeign = flt(row.custom_贷方外币);

        if (!debitForeign && !creditForeign) {
            if (options.clearWhenEmpty) {
                await setNativeAmounts(row, 0, 0);
                frm.cscript.update_totals(frm.doc);
            }
            return;
        }

        if (debitForeign < 0 || creditForeign < 0) {
            showWarningOnce(
                frm,
                `negative-${row.name}`,
                `第 ${row.idx} 行：借方外币和贷方外币不能填写负数。`
            );
            return;
        }

        if (debitForeign && creditForeign) {
            showWarningOnce(
                frm,
                `both-${row.name}`,
                `第 ${row.idx} 行：借方外币和贷方外币不能同时填写。`
            );
            return;
        }

        const companyCurrency = getCompanyCurrency(frm);
        if (
            row.account_currency &&
            companyCurrency &&
            row.account_currency !== companyCurrency
        ) {
            showWarningOnce(
                frm,
                `foreign-account-${row.name}`,
                `第 ${row.idx} 行：外币辅助字段只能用于 ${companyCurrency} 科目。`
            );
            return;
        }

        const candidates = getRateCandidates(frm);
        if (candidates.length > 1) {
            const rateText = candidates
                .map(candidate => `${candidate.currency}：${candidate.rate}`)
                .join("、");
            showWarningOnce(
                frm,
                `multiple-${rateText}`,
                `检测到多个不同的外币汇率（${rateText}），已停止自动换算。`
            );
            return;
        }

        clearWarning(frm);
        const conversionRate = candidates.length ? candidates[0].rate : 1;

        if (debitForeign) {
            const amount = flt(
                debitForeign * conversionRate,
                precision("debit_in_account_currency", row)
            );
            await setNativeAmounts(row, amount, 0);
        } else {
            const amount = flt(
                creditForeign * conversionRate,
                precision("credit_in_account_currency", row)
            );
            await setNativeAmounts(row, 0, amount);
        }

        frm.cscript.update_totals(frm.doc);
    }

    async function recalculateAll(frm) {
        if (frm.doc.docstatus !== 0) return;

        await updateForeignTotals(frm);

        for (const row of frm.doc.accounts || []) {
            if (flt(row.custom_借方外币) || flt(row.custom_贷方外币)) {
                await recalculateRow(frm, row.doctype, row.name);
            }
        }
    }

    function scheduleRecalculation(frm) {
        frm.__fengjingForeignAmountTimers = frm.__fengjingForeignAmountTimers || [];
        frm.__fengjingForeignAmountTimers.forEach(timer => clearTimeout(timer));
        frm.__fengjingForeignAmountTimers = RECALC_DELAYS.map(delay =>
            setTimeout(() => recalculateAll(frm), delay)
        );
    }

    async function splitRemarks(frm, cdt, cdn) {
        if (frm.doc.docstatus !== 0) {
            frappe.show_alert({ message: __("已提交的日记账凭证不能切分摘要。"), indicator: "orange" }, 6);
            return;
        }

        const currentRow = locals[cdt]?.[cdn];
        if (!currentRow) return;

        const remarks = String(currentRow.user_remark || "")
            .split(/[;；]/)
            .map(value => value.trim())
            .filter(Boolean);

        if (remarks.length < 2) {
            frappe.show_alert({ message: __("摘要中没有可切分的分号内容。"), indicator: "orange" }, 6);
            return;
        }

        const accounts = frm.doc.accounts || [];
        const startIndex = accounts.findIndex(row => row.name === cdn);
        if (startIndex < 0) return;

        while ((frm.doc.accounts || []).length < startIndex + remarks.length) {
            frm.add_child("accounts");
        }

        const targetRows = (frm.doc.accounts || []).slice(startIndex, startIndex + remarks.length);
        for (let index = 0; index < targetRows.length; index += 1) {
            const row = targetRows[index];
            await frappe.model.set_value(row.doctype, row.name, "user_remark", remarks[index]);
        }

        frm.refresh_field("accounts");
        frm.dirty();
        frappe.show_alert({ message: __(`已按顺序切分 ${remarks.length} 条摘要。`), indicator: "green" }, 6);
    }

    frappe.ui.form.on("Journal Entry", {
        refresh(frm) {
            scheduleRecalculation(frm);
        },
        posting_date(frm) {
            scheduleRecalculation(frm);
        },
        multi_currency(frm) {
            scheduleRecalculation(frm);
        },
        validate(frm) {
            return recalculateAll(frm);
        }
    });

    frappe.ui.form.on("Journal Entry Account", {
        custom_切分摘要(frm, cdt, cdn) {
            return splitRemarks(frm, cdt, cdn);
        },
        custom_借方外币(frm, cdt, cdn) {
            return recalculateRow(frm, cdt, cdn, { clearWhenEmpty: true });
        },
        custom_贷方外币(frm, cdt, cdn) {
            return recalculateRow(frm, cdt, cdn, { clearWhenEmpty: true });
        },
        exchange_rate(frm) {
            scheduleRecalculation(frm);
        },
        account(frm) {
            scheduleRecalculation(frm);
        },
        account_currency(frm) {
            scheduleRecalculation(frm);
        },
        debit_in_account_currency(frm) {
            scheduleRecalculation(frm);
        },
        credit_in_account_currency(frm) {
            scheduleRecalculation(frm);
        },
        accounts_remove(frm) {
            scheduleRecalculation(frm);
        },
        accounts_add(frm) {
            scheduleRecalculation(frm);
        }
    });
})();
