(() => {
const ORDER_METHOD =
    "fengjing_app.fengjing_business.doctype.amazon_order_configuration.amazon_order_configuration";

frappe.ui.form.on("Amazon Order Configuration", {
    sync_latest_now(frm) {
        return queue_order_action(frm, "start_incremental_sync", {}, {
            confirm: "确定立即同步该店铺的最新 Amazon 订单吗？",
            success: "最新订单同步任务已进入后台队列。",
        });
    },

    sync_history_now(frm) {
        return queue_order_action(frm, "start_history_sync", {}, {
            require_history_range: true,
            confirm: "确定按照当前起止时间执行 Amazon 历史订单同步吗？再次执行已完成的历史任务会从头核对整个时间范围。",
            success: "历史订单同步任务已进入后台队列。",
        });
    },

    recheck_7_now(frm) {
        return queue_order_recheck(frm, 7);
    },

    recheck_14_now(frm) {
        return queue_order_recheck(frm, 14);
    },

    recheck_30_now(frm) {
        return queue_order_recheck(frm, 30);
    },

    recheck_90_now(frm) {
        return queue_order_recheck(frm, 90);
    },

    recheck_180_now(frm) {
        return queue_order_recheck(frm, 180);
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

async function queue_order_action(frm, method, args, options) {
    if (
        options.require_history_range &&
        (!frm.doc.history_start_datetime || !frm.doc.history_end_datetime)
    ) {
        frappe.msgprint(__("请先填写历史同步开始时间和结束时间。"));
        return;
    }

    if (!(await confirm_action(options.confirm))) {
        return;
    }

    await save_if_needed(frm);
    await frappe.call({
        method: `${ORDER_METHOD}.${method}`,
        args: { name: frm.doc.name, ...args },
        freeze: true,
        freeze_message: __("正在提交后台任务..."),
    });
    frappe.show_alert({ message: __(options.success), indicator: "green" }, 7);
    await frm.reload_doc();
}

function queue_order_recheck(frm, days) {
    return queue_order_action(frm, "start_recheck_sync", { days }, {
        confirm: `确定立即核对该店铺最近 ${days} 天的 Amazon 订单吗？`,
        success: `最近 ${days} 天订单核对任务已进入后台队列。`,
    });
}
})();
