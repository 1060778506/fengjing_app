// Copyright (c) 2026, Fengjing and contributors
// For license information, please see license.txt

frappe.ui.form.on("Amazon Advertising Site Configuration", {
	refresh(frm) {
		if (frm.is_new()) return;
		const call = (method, freezeMessage) => frappe.call({
			method: `fengjing_app.fengjing_business.doctype.amazon_advertising_site_configuration.amazon_advertising_site_configuration.${method}`,
			args: { name: frm.doc.name },
			freeze: true,
			freeze_message: freezeMessage,
			callback(r) {
				frappe.show_alert({ message: (r.message || {}).message || __("任务已提交。"), indicator: "green" });
				frm.reload_doc();
			},
		});
		frm.add_custom_button(__("测试广告Profile"), () => call("test_advertising_access", __("正在测试广告Profile…")), __("广告同步"));
		frm.add_custom_button(__("同步广告结构"), () => call("start_structure_sync", __("正在提交广告结构任务…")), __("广告同步"));
		frm.add_custom_button(__("立即同步日报"), () => call("start_latest_sync", __("正在提交广告日报任务…")), __("广告同步"));
		frm.add_custom_button(__("同步历史范围"), () => call("start_history_sync", __("正在提交广告历史任务…")), __("广告同步"));
	},
});
