(() => {
    function splitDescription(value) {
        const documentFragment = new DOMParser().parseFromString(String(value || ""), "text/html");
        documentFragment.querySelectorAll("br").forEach(node => node.replaceWith("\n"));
        const text = (documentFragment.body.textContent || "").replace(/[\u00a0\u200b]/g, " ");
        const hasSemicolons = /[;；]/.test(text);
        const parts = text.split(hasSemicolons ? /[;；]/ : /\r?\n/).map(part => part.trim());
        if (hasSemicolons && /[;；]\s*$/.test(text)) parts.pop();
        return parts.map(part => part === "-" || part === "—" ? "" : part);
    }

    async function distributeDescriptions(frm, cdt, cdn, tableField = "items") {
        const tableLabel = tableField === "taxes" ? "销售税费" : "物料明细";
        if (frm.doc.docstatus !== 0) {
            frappe.show_alert({ message: __("已提交的销售发票不能向下拆分说明。"), indicator: "orange" }, 6);
            return;
        }

        const source = locals[cdt]?.[cdn];
        const rows = frm.doc[tableField] || [];
        const startIndex = rows.findIndex(row => row.name === cdn);
        if (!source || startIndex < 0) return;

        const parts = splitDescription(source.description);
        if (parts.length < 2) {
            frappe.show_alert({ message: __("本行说明中没有可向下拆分的分号或换行内容。"), indicator: "orange" }, 6);
            return;
        }

        while (rows.length < startIndex + parts.length) frm.add_child(tableField);
        for (const [offset, part] of parts.entries()) {
            const row = rows[startIndex + offset];
            await frappe.model.set_value(row.doctype, row.name, "description", part);
        }
        frm.refresh_field(tableField);
        frm.dirty();
        frappe.show_alert({ message: __("已从第 {0} 行向下分配 {1} 条{2}说明。", [startIndex + 1, parts.length, tableLabel]), indicator: "green" }, 6);
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
