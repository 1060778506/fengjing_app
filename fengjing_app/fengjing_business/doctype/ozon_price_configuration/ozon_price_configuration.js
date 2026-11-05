(() => {
const PRICE_METHOD =
    "fengjing_app.fengjing_business.doctype.ozon_price_configuration.ozon_price_configuration";

frappe.ui.form.on("Ozon Price Configuration", {
    record_now(frm) {
        return record_prices(frm);
    },
});

function confirm_action(message) {
    return new Promise((resolve) => {
        frappe.confirm(__(message), () => resolve(true), () => resolve(false));
    });
}

async function save_if_needed(frm) {
    if (frm.is_new() || frm.is_dirty()) {
        await frm.save();
    }
}

async function record_prices(frm) {
    if (!(await confirm_action("确定立即记录该店铺的 Ozon 商品价格吗？"))) {
        return;
    }
    await save_if_needed(frm);
    await frappe.call({
        method: `${PRICE_METHOD}.start_price_recording`,
        args: { name: frm.doc.name },
        freeze: true,
        freeze_message: __("正在提交后台任务..."),
    });
    frappe.show_alert({ message: __("价格记录任务已进入后台队列。"), indicator: "green" }, 7);
    await frm.reload_doc();
}
})();
