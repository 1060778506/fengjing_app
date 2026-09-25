frappe.pages['amazon-finance'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Amazon财务交易中心页面',
		single_column: true
	});
}