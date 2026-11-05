(() => {
const FINANCIAL_METHOD =
    "fengjing_app.fengjing_business.doctype.ozon_financial_configuration.ozon_financial_configuration";

frappe.ui.form.on("Ozon Financial Configuration", {
    sync_latest_now(frm) {
        return queue_action(frm, "start_incremental_sync", {}, {
            confirm: "确定立即同步该店铺的最新 Ozon 财务数据吗？",
            success: "最新财务同步任务已进入后台队列。",
        });
    },

    sync_history_now(frm) {
        return queue_action(frm, "start_history_sync", {}, {
            require_history_range: true,
            confirm: "确定按照当前起止日期执行 Ozon 历史财务同步吗？再次执行已完成的任务会从头核对。",
            success: "历史财务同步任务已进入后台队列。",
        });
    },

    recheck_7_now(frm) {
        return queue_recheck(frm, 7);
    },

    recheck_14_now(frm) {
        return queue_recheck(frm, 14);
    },

    recheck_30_now(frm) {
        return queue_recheck(frm, 30);
    },

    recheck_90_now(frm) {
        return queue_recheck(frm, 90);
    },

    recheck_180_now(frm) {
        return queue_recheck(frm, 180);
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

async function queue_action(frm, method, args, options) {
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
        method: `${FINANCIAL_METHOD}.${method}`,
        args: { name: frm.doc.name, ...args },
        freeze: true,
        freeze_message: __("正在提交后台任务..."),
    });
    frappe.show_alert({ message: __(options.success), indicator: "green" }, 7);
    await frm.reload_doc();
}

function queue_recheck(frm, days) {
    return queue_action(frm, "start_recheck_sync", { days }, {
        confirm: `确定立即核对该店铺最近 ${days} 天的 Ozon 财务数据吗？`,
        success: `最近 ${days} 天财务核对任务已进入后台队列。`,
    });
}
})();
