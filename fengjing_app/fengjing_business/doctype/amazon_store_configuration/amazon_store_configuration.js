(() => {
frappe.ui.form.on("Amazon Store Configuration", {
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
    try {
        await frappe.call({
            method: "fengjing_app.fengjing_business.doctype.amazon_store_configuration.amazon_store_configuration.test_amazon_store_api",
            args: { name: frm.doc.name },
            freeze: true,
            freeze_message: __("正在测试 Amazon API 连接..."),
        });
    } finally {
        await frm.reload_doc();
    }

    frappe.msgprint({
        title: __("测试成功"),
        message: __("Amazon API 连接可用。"),
        indicator: "green",
    });
}
})();
