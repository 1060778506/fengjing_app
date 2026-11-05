(() => {
    function splitDescription(value) {
        const documentFragment = new DOMParser().parseFromString(String(value || ""), "text/html");
        return (documentFragment.body.textContent || "")
            .replace(/[\u00a0\u200b]/g, " ")
            .split(/[;；]/)
            .map(part => part.trim())
            .filter(Boolean);
    }

    async function distributeDescriptions(frm, cdt, cdn) {
        if (frm.doc.docstatus !== 0) {
            frappe.show_alert({ message: __("已提交的销售发票不能拆分说明。"), indicator: "orange" }, 6);
            return;
        }

        const source = locals[cdt]?.[cdn];
        const items = frm.doc.items || [];
        if (!source || !items.some(item => item.name === cdn)) return;

        const parts = splitDescription(source.description);
        if (parts.length < 2) {
            frappe.show_alert({ message: __("本行说明中没有可切分的分号内容。"), indicator: "orange" }, 6);
            return;
        }
        if (parts.length !== items.length) {
            frappe.msgprint({
                title: __("说明数量不一致"),
                message: __("切分得到 {0} 条说明，当前销售发票有 {1} 条物料明细。未修改任何内容。", [parts.length, items.length]),
                indicator: "orange",
            });
            return;
        }

        for (const [index, item] of items.entries()) {
            await frappe.model.set_value(item.doctype, item.name, "description", parts[index]);
        }
        frm.refresh_field("items");
        frm.dirty();
        frappe.show_alert({ message: __("已按物料行顺序切分 {0} 条说明。", [parts.length]), indicator: "green" }, 6);
    }

    frappe.ui.form.on("Sales Invoice Item", {
        custom_拆分说明(frm, cdt, cdn) {
            return distributeDescriptions(frm, cdt, cdn);
        },
    });
})();
