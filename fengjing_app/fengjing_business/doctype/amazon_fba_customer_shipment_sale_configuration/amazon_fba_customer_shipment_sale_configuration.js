(() => {
const SALE_METHOD =
    "fengjing_app.fengjing_business.doctype.amazon_fba_customer_shipment_sale_configuration.amazon_fba_customer_shipment_sale_configuration";

frappe.ui.form.on("Amazon FBA Customer Shipment Sale Configuration", {
    sync_latest_now(frm) {
        return queue_shipment_action(frm, "start_incremental_sync", {}, {
            confirm: "确定立即同步该店铺的最新 FBA 已完成订单销售吗？",
            success: "最新销售同步任务已进入后台队列。",
        });
    },

    sync_history_now(frm) {
        return queue_shipment_action(frm, "start_history_sync", {}, {
            confirm: "确定从现在倒推18个月重新抓取历史销售记录吗？已完成的记录会核对更新。",
            success: "历史销售同步任务已进入后台队列。",
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
        frappe.msgprint(__("请先勾选倒推18个月抓取历史销售记录并保存。"));
        return;
    }

    if (!(await confirm_action(options.confirm))) {
        return;
    }

    await save_if_needed(frm);
    await frappe.call({
        method: `${SALE_METHOD}.${method}`,
        args: { name: frm.doc.name, ...args },
        freeze: true,
        freeze_message: __("正在提交后台任务..."),
    });
    frappe.show_alert({ message: __(options.success), indicator: "green" }, 7);
    await frm.reload_doc();
}

function queue_shipment_recheck(frm, days) {
    return queue_shipment_action(frm, "start_recheck_sync", { days }, {
        confirm: `确定立即核对该店铺最近 ${days} 天的 FBA 已完成订单销售吗？`,
        success: `最近 ${days} 天销售核对任务已进入后台队列。`,
    });
}
})();
