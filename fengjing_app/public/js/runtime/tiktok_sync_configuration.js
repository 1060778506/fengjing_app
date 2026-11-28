(() => {
	if (frappe.tiktokSyncConfigurationHandlersInstalled) return;
	frappe.tiktokSyncConfigurationHandlersInstalled = true;

	const kinds = {
		"Order": "orders",
		"Aftersales": "aftersales",
		"Statement": "statements",
		"Statement Transaction": "statement_transactions",
		"Payout": "payouts",
		"Shop Performance": "shop_performance",
	};

	for (const [report, kind] of Object.entries(kinds)) {
		async function start(frm, action) {
			if (frm.is_new() || frm.is_dirty()) {
				frappe.msgprint("请先保存配置。");
				return;
			}
			await frappe.xcall("fengjing_app.fengjing_business.doctype.tiktok_order_configuration.tiktok_sync.request_sync", {
				kind,
				configuration: frm.doc.name,
				action,
			});
			frappe.show_alert({ message: "同步任务已加入队列", indicator: "green" });
			await frm.reload_doc();
		}

		frappe.ui.form.on("TikTok " + report + " Sync Configuration", {
			refresh(frm) {
				const available = !frm.is_new() && !["排队中", "运行中", "待续跑"].includes(frm.doc.task_status);
				frm.toggle_display(["sync_latest", "sync_history"], available);
				frm.toggle_display("sync_retry", available && frm.doc.task_status === "失败");
				if (available) {
					for (const days of [7, 14, 30, 180]) {
						frm.add_custom_button(`核对 ${days} 天`, () => {
							frappe.confirm(`重新核对最近 ${days} 个完整店铺日的数据？`, () => start(frm, `recheck_${days}`));
						}, "手动核对");
					}
				}
			},
			sync_latest: frm => start(frm, "incremental"),
			sync_history: frm => start(frm, "history"),
			sync_retry: frm => start(frm, "retry"),
		});
	}
})();
