(() => {
const SHIPMENT_METHOD =
    "fengjing_app.fengjing_business.doctype.amazon_fba_fulfilled_shipment_configuration.amazon_fba_fulfilled_shipment_configuration";

frappe.ui.form.on("Amazon FBA Fulfilled Shipment Configuration", {
    sync_latest_now(frm) {
        return queue_shipment_action(frm, "start_incremental_sync", {}, {
            confirm: "确定立即同步该店铺的最新 FBA 已配送货件吗？",
            success: "最新发货同步任务已进入后台队列。",
        });
    },

    sync_history_now(frm) {
        return queue_shipment_action(frm, "start_history_sync", {}, {
            confirm: "确定从现在倒推18个月重新抓取历史发货记录吗？已完成的记录会核对更新。",
            success: "历史发货同步任务已进入后台队列。",
        });
    },

    recheck_7_now(frm) {
        return queue_shipment_recheck(frm, 7);
    },

    recheck_14_now(frm) {
        return queue_shipment_recheck(frm, 14);
    },

    recheck_30_now(frm) {
        return queue_shipment_recheck(frm, 30);
    },

    recheck_90_now(frm) {
        return queue_shipment_recheck(frm, 90);
    },

    recheck_180_now(frm) {
        return queue_shipment_recheck(frm, 180);
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

async function queue_shipment_action(frm, method, args, options) {
    if (method === "start_history_sync" && !frm.doc.history_18_months) {
        frappe.msgprint(__("请先勾选倒推18个月抓取历史发货记录并保存。"));
        return;
    }

    if (!(await confirm_action(options.confirm))) {
        return;
    }

    await save_if_needed(frm);
    await frappe.call({
        method: `${SHIPMENT_METHOD}.${method}`,
        args: { name: frm.doc.name, ...args },
        freeze: true,
        freeze_message: __("正在提交后台任务..."),
    });
    frappe.show_alert({ message: __(options.success), indicator: "green" }, 7);
    await frm.reload_doc();
}

function queue_shipment_recheck(frm, days) {
    return queue_shipment_action(frm, "start_recheck_sync", { days }, {
        confirm: `确定立即核对该店铺最近 ${days} 天的 FBA 已配送货件吗？`,
        success: `最近 ${days} 天发货核对任务已进入后台队列。`,
    });
}
})();
