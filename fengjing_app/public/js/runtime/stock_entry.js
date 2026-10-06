// ERPNext 物料移动中的 TEMU 物料套件功能。

// ============================================================
// TEMU 物料套件移动
// 1. 解析 Product Bundle 中的真实库存物料
// 2. 自动生成 Stock Entry Detail 明细
// 3. 重新解析时只替换程序生成的行
// ============================================================
(function () {
    "use strict";

    const PARSE_METHOD =
        "fengjing_app.fengjing_business.doctype.temu_material_movement.temu_material_movement.parse_temu_product_bundles";

    function getBundleRows(frm) {
        return (frm.doc.custom_temu_物料套件移动 || []).map(row => ({
            temu包裹号: String(row.temu包裹号 || "").trim(),
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
            custom_temu包裹号: data.temu_package_no,
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
            frappe.msgprint(__("请先在“TEMU 物料套件移动”中添加包裹和物料套件。"));
            return;
        }

        const response = await frappe.call({
            method: PARSE_METHOD,
            args: {
                bundle_rows: bundleRows
            },
            freeze: true,
            freeze_message: __("正在解析 TEMU 物料套件……")
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
                "解析完成：{0} 个包裹，生成 {1} 条物料明细。",
                [result.package_count || 0, result.item_row_count || itemRows.length]
            ),
            indicator: "green"
        }, 7);
    }

    frappe.ui.form.on("Stock Entry", {
        async custom_解析套件(frm) {
            try {
                await parseBundles(frm);
            } catch (error) {
                console.error("TEMU 物料套件解析失败：", error);
            }
        },

        validate(frm) {
            // “套件名称”当前是 Product Bundle Link，在发送到后端前
            // 统一保存有效套件编号，避免 fetch_from 描述造成无效链接。
            (frm.doc.custom_temu_物料套件移动 || []).forEach(row => {
                row.套件名称 = row.套件 || "";
            });
        }
    });

    frappe.ui.form.on("Temu Material movement", {
        套件(frm, cdt, cdn) {
            const row = locals[cdt][cdn];
            // 当前“套件名称”是 Product Bundle Link，必须保存有效编号。
            frappe.model.set_value(cdt, cdn, "套件名称", row.套件 || "");
        }
    });
})();
