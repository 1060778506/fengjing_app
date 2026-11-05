// ERPNext 物料移动中的通用物料套件解析功能。

// ============================================================
// 物料套件移动
// 1. 解析 Product Bundle 中的真实库存物料
// 2. 自动生成 Stock Entry Detail 明细
// 3. 重新解析时只替换程序生成的行
// ============================================================
(function () {
    "use strict";

    const PARSE_METHOD =
        "fengjing_app.fengjing_business.doctype.analysis_material_movement.analysis_material_movement.parse_product_bundles";

    function getBundleRows(frm) {
        return (frm.doc.custom_物料套件移动 || []).map(row => ({
            套件: row.套件 || "",
            数量: row.数量
        }));
    }

    function removePreviouslyGeneratedRows(frm) {
        const retainedRows = (frm.doc.items || []).filter(row => {
            const isGenerated = Number(row.custom_是否程序生成 || 0) === 1;
            const isBlank = !String(row.item_code || "").trim();

            // 重新解析时删除旧的程序生成行，同时清理
            // ERPNext 新单据中自动出现的空白物料行。
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

        // 先写物料编号，让 ERPNext 原生逻辑带出单位和物料资料。
        await frappe.model.set_value(row.doctype, row.name, "item_code", data.item_code);

        const values = {
            qty: data.qty,
            custom_是否程序生成: 1
        };

        if (frm.doc.from_warehouse) {
            values.s_warehouse = frm.doc.from_warehouse;
        }
        if (frm.doc.to_warehouse) {
            values.t_warehouse = frm.doc.to_warehouse;
        }

        await frappe.model.set_value(row.doctype, row.name, values);
    }

    async function parseBundles(frm) {
        if (frm.doc.docstatus !== 0) {
            frappe.msgprint(__("只有草稿状态的物料移动才能解析套件。"));
            return;
        }

        const bundleRows = getBundleRows(frm);
        if (!bundleRows.length) {
            frappe.msgprint(__("请先在“套件物料解析”中添加物料套件。"));
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
            frappe.throw(__("未解析到可移动的库存物料。"));
        }

        removePreviouslyGeneratedRows(frm);

        for (const itemRow of itemRows) {
            await addGeneratedItem(frm, itemRow);
        }

        frm.refresh_field("items");
        frm.dirty();

        // 与原生“更新物料成本价和可用数量”按钮使用同一个
        // Stock Entry 文档方法，确保数量、估值价和仓库可用量统一刷新。
        await frm.call({
            method: "get_stock_and_rate",
            doc: frm.doc,
            freeze: true,
            freeze_message: __("正在更新物料成本价和可用数量……")
        });
        frm.refresh_fields();

        frappe.show_alert({
            message: __(
                "解析完成：{0} 行套件，生成 {1} 条物料明细。",
                [result.bundle_row_count || 0, result.item_row_count || itemRows.length]
            ),
            indicator: "green"
        }, 7);
    }

    frappe.ui.form.on("Stock Entry", {
        async custom_解析套件(frm) {
            try {
                await parseBundles(frm);
            } catch (error) {
                console.error("物料套件解析失败：", error);
            }
        }
    });
})();
