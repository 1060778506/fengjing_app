(() => {
    function splitDescription(value, preserveEmpty = false) {
        const documentFragment = new DOMParser().parseFromString(String(value || ""), "text/html");
        const parts = (documentFragment.body.textContent || "")
            .replace(/[\u00a0\u200b]/g, " ")
            .split(/[;；]/)
            .map(part => part.trim());
        return preserveEmpty ? parts.map(part => part === "-" || part === "—" ? "" : part) : parts.filter(Boolean);
    }

    async function distributeDescriptions(frm, cdt, cdn, tableField = "items") {
        const tableLabel = tableField === "taxes" ? "销售税费" : "物料明细";
        if (frm.doc.docstatus !== 0) {
            frappe.show_alert({ message: __("已提交的销售发票不能拆分说明。"), indicator: "orange" }, 6);
            return;
        }

        const source = locals[cdt]?.[cdn];
        const rows = frm.doc[tableField] || [];
        if (!source || !rows.some(row => row.name === cdn)) return;

        const parts = splitDescription(source.description, tableField === "taxes");
        if (parts.length < 2) {
            frappe.show_alert({ message: __("本行说明中没有可切分的分号内容。"), indicator: "orange" }, 6);
            return;
        }
        if (parts.length !== rows.length) {
            frappe.msgprint({
                title: __("说明数量不一致"),
                message: __("切分得到 {0} 条说明，当前销售发票有 {1} 条{2}。未修改任何内容。", [parts.length, rows.length, tableLabel]),
                indicator: "orange",
            });
            return;
        }

        for (const [index, row] of rows.entries()) {
            await frappe.model.set_value(row.doctype, row.name, "description", parts[index]);
        }
        frm.refresh_field(tableField);
        frm.dirty();
        frappe.show_alert({ message: __("已按{0}行顺序切分 {1} 条说明。", [tableLabel, parts.length]), indicator: "green" }, 6);
    }

    frappe.ui.form.on("Sales Invoice Item", {
        custom_拆分说明(frm, cdt, cdn) {
            return distributeDescriptions(frm, cdt, cdn);
        },
    });

    frappe.ui.form.on("Sales Taxes and Charges", {
        custom_拆分说明(frm, cdt, cdn) {
            return distributeDescriptions(frm, cdt, cdn, "taxes");
        },
    });
})();
