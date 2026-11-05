(() => {
const LEDGER_METHOD =
	"fengjing_app.fengjing_business.doctype.amazon_fba_inventory_ledger_configuration.amazon_fba_inventory_ledger_configuration";

frappe.ui.form.on("Amazon FBA Inventory Ledger Configuration", {
	recheck_7_now(frm) {
		return queue_ledger_recheck(frm, 7);
	},

	recheck_14_now(frm) {
		return queue_ledger_recheck(frm, 14);
	},

	recheck_30_now(frm) {
		return queue_ledger_recheck(frm, 30);
	},

	recheck_90_now(frm) {
		return queue_ledger_recheck(frm, 90);
	},

	recheck_180_now(frm) {
		return queue_ledger_recheck(frm, 180);
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

async function queue_ledger_recheck(frm, days) {
	if (!(await confirm_action(`确定立即核对该店铺最近 ${days} 天的 FBA 库存分类账吗？`))) {
		return;
	}

	await save_if_needed(frm);
	await frappe.call({
		method: `${LEDGER_METHOD}.start_recheck_sync`,
		args: { name: frm.doc.name, days },
		freeze: true,
		freeze_message: __("正在提交后台任务..."),
	});
	frappe.show_alert(
		{ message: __(`最近 ${days} 天库存分类账核对任务已进入后台队列。`), indicator: "green" },
		7
	);
	await frm.reload_doc();
}
})();
