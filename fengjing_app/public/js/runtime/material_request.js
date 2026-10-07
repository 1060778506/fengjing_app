// ERPNext 物料需求中的通用物料套件解析功能。

(function () {
    "use strict";

    const PARSE_METHOD =
        "fengjing_app.fengjing_business.doctype.analysis_material_movement.analysis_material_movement.parse_product_bundles_for_material_request";

    function getBundleRows(frm) {
        return (frm.doc.custom_物料套件移动 || []).map(row => ({
            套件: row.套件 || "",
            数量: row.数量,
            temu包裹号: row.temu包裹号 || ""
        }));
    }

    function removePreviouslyGeneratedRows(frm) {
        const retainedRows = (frm.doc.items || []).filter(row => {
            const isGenerated = Number(row.custom_是否程序生成 || 0) === 1;
            const isBlank = !String(row.item_code || "").trim();

            return !isGenerated && !isBlank;
        });

        frm.doc.items = retainedRows;
        retainedRows.forEach((row, index) => {
            row.idx = index + 1;
        });
        frm.refresh_field("items");
    }

    async function addGeneratedItem(frm, data) {
        const row = frm.add_child("items");

        // 先写入物料编码，交给 ERPNext 原生逻辑带出单位和物料资料。
        await frappe.model.set_value(row.doctype, row.name, "item_code", data.item_code);

        const values = {
            qty: data.qty,
            custom_temu包裹号: data.temu_package_no || "",
            custom_是否程序生成: 1
        };

        // 已填写时直接带入；未填写时允许先解析，之后再由用户统一设置。
        if (frm.doc.schedule_date) {
            values.schedule_date = frm.doc.schedule_date;
        }
        if (frm.doc.set_from_warehouse) {
            values.from_warehouse = frm.doc.set_from_warehouse;
        }
        if (frm.doc.set_warehouse) {
            values.warehouse = frm.doc.set_warehouse;
        }

        await frappe.model.set_value(row.doctype, row.name, values);
    }

    async function parseBundles(frm) {
        if (frm.doc.docstatus !== 0) {
            frappe.msgprint(__("只有草稿状态的物料需求才能解析套件。"));
            return;
        }

        if (frm.doc.material_request_type !== "Material Transfer") {
            frappe.msgprint(__("请先把物料需求类型设置为“物料转移”。"));
            return;
        }

        const bundleRows = getBundleRows(frm);
        if (!bundleRows.length) {
            frappe.msgprint(__("请先在“物料套件移动”中添加物料套件。"));
            return;
        }

        const response = await frappe.call({
            method: PARSE_METHOD,
            args: {
                bundle_rows: bundleRows
            },
            freeze: true,
            freeze_message: __("正在解析物料套件……")
        });

        const result = response.message || {};
        const itemRows = result.rows || [];
        if (!itemRows.length) {
            frappe.throw(__("未解析到可申请移动的库存物料。"));
        }

        removePreviouslyGeneratedRows(frm);

        for (const itemRow of itemRows) {
            await addGeneratedItem(frm, itemRow);
        }

        frm.refresh_field("items");
        frm.dirty();

        frappe.show_alert({
            message: __(
                "解析完成：{0} 行套件，生成 {1} 条物料需求明细。",
                [result.bundle_row_count || 0, result.item_row_count || itemRows.length]
            ),
            indicator: "green"
        }, 7);
    }

    frappe.ui.form.on("Material Request", {
        async custom_解析套件(frm) {
            try {
                await parseBundles(frm);
            } catch (error) {
                console.error("物料需求套件解析失败：", error);
            }
        }
    });
})();
