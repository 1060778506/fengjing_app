frappe.ui.form.on("TikTok Order Configuration", {
	refresh(frm) {
		if (!frm.is_new()) frm.add_custom_button("测试连接", async () => {
			if (frm.is_dirty()) return frappe.msgprint("请先保存店铺配置。");
			const { message } = await frappe.call({
				method: "fengjing_app.fengjing_business.doctype.tiktok_order_configuration.tiktok_order_configuration.test_authorization",
				args: { configuration: frm.doc.name },
				freeze: true,
			});
			frappe.show_alert({ message: message.message, indicator: "green" });
		});
	},
	register_api() {
		window.open("https://partner.us.tiktokshop.com/", "_blank", "noopener,noreferrer");
	},
	async authorize_shop(frm) {
		if (frm.is_new() || frm.is_dirty()) {
			frappe.msgprint("请先保存 TikTok 订单配置，再开始授权。");
			return;
		}
		const { message } = await frappe.call({
			method: "fengjing_app.fengjing_business.doctype.tiktok_order_configuration.tiktok_order_configuration.start_authorization",
			args: { configuration: frm.doc.name },
			freeze: true,
		});
		window.location.assign(message.authorization_url);
	},
});
