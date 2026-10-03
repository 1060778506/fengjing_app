(() => {
const STATEMENT_METHOD =
    "fengjing_app.fengjing_business.doctype.ozon_settlement_statement_configuration.ozon_settlement_statement_configuration";

frappe.ui.form.on("Ozon Settlement Statement Configuration", {
    sync_latest_now(frm) {
        return queue_action(frm, "start_latest_sync", {
            confirm: "确定立即同步该店铺最近的 Ozon 结算报告吗？",
            success: "最新结算报告同步任务已进入后台队列。",
        });
    },

    sync_history_now(frm) {
        return queue_action(frm, "start_history_sync", {
            require_history_range: true,
            confirm: "确定按照当前起止日期同步 Ozon 历史结算报告吗？再次执行已完成的任务会从头核对。",
            success: "历史结算报告同步任务已进入后台队列。",
        });
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

async function queue_action(frm, method, options) {
    if (
        options.require_history_range &&
        (!frm.doc.history_start_date || !frm.doc.history_end_date)
    ) {
        frappe.msgprint(__("请先填写历史同步开始日期和结束日期。"));
        return;
    }
    if (!(await confirm_action(options.confirm))) {
        return;
    }
    await save_if_needed(frm);
    await frappe.call({
        method: `${STATEMENT_METHOD}.${method}`,
        args: { name: frm.doc.name },
        freeze: true,
        freeze_message: __("正在提交后台任务..."),
    });
    frappe.show_alert({ message: __(options.success), indicator: "green" }, 7);
    await frm.reload_doc();
}
})();
