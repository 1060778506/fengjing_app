// Copyright (c) 2026, Fengjing and contributors
// For license information, please see license.txt

frappe.ui.form.on("Amazon Advertising Master Configuration", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("发现广告站点"), () => {
			frappe.call({
				method: "fengjing_app.fengjing_business.doctype.amazon_advertising_master_configuration.amazon_advertising_master_configuration.discover_advertising_profiles",
				args: { name: frm.doc.name },
				freeze: true,
				freeze_message: __("正在读取Amazon广告Profile…"),
				callback(r) {
					const data = r.message || {};
					frappe.msgprint(__("识别到 {0} 个Profile；新建 {1} 个、更新 {2} 个站点配置。", [
						(data.profiles || []).length,
						(data.created || []).length,
						(data.updated || []).length,
					]));
				},
			});
		}, __("广告同步"));
	},
});
