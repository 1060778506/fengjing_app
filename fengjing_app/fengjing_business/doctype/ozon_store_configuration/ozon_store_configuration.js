(() => {
const STORE_METHOD =
    "fengjing_app.fengjing_business.doctype.ozon_store_configuration.ozon_store_configuration";

frappe.ui.form.on("Ozon Store Configuration", {
    test_api_connection(frm) {
        return test_api_connection(frm);
    },
});

async function save_if_needed(frm) {
    if (frm.is_new() || frm.is_dirty()) {
        await frm.save();
    }
}

async function test_api_connection(frm) {
    await save_if_needed(frm);
    let response;
    try {
        response = await frappe.call({
            method: `${STORE_METHOD}.test_ozon_store_api`,
            args: { name: frm.doc.name },
            freeze: true,
            freeze_message: __("正在测试 Ozon API 连接..."),
        });
    } finally {
        await frm.reload_doc();
    }

    const data = response?.message || {};
    const rows = (data.results || []).map((row) =>
        `${frappe.utils.escape_html(row.name)}：${frappe.utils.escape_html(row.message)}`
    );
    frappe.msgprint({
        title: data.status === "success" ? __("测试成功") : __("测试结果"),
        message: rows.join("<br>") || __(data.message || "Ozon API 测试已完成。"),
        indicator: data.status === "success" ? "green" : data.status === "partial" ? "orange" : "red",
    });
}
})();
