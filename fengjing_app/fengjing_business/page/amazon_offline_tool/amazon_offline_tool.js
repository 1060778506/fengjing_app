frappe.pages['amazon-offline-tool'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: '亚马逊CSV离线翻译工具',
		single_column: true
	});
}