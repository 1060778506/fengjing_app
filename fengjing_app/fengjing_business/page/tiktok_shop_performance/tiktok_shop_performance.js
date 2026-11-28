frappe.pages["tiktok_shop_performance"].on_page_show = function (wrapper) {
	if (wrapper.tiktokAnalysis) return wrapper.tiktokAnalysis.load();
	if (wrapper.loadingTikTokAnalysis) return;
	wrapper.loadingTikTokAnalysis = true;
	const page = frappe.ui.make_app_page({
		parent: wrapper, title: "TikTok 店铺表现", single_column: true,
	});
	frappe.require([
		"/assets/fengjing_app/styles/tiktok_analysis.css",
		"/assets/fengjing_app/js/runtime/tiktok_analysis.js",
		"/assets/fengjing_app/js/图表-echarts.js",
	], () => {
		wrapper.tiktokAnalysis = new frappe.ui.TikTokAnalysis(page, "performance");
		wrapper.tiktokAnalysis.load();
	});
};

frappe.pages["tiktok_shop_performance"].on_page_hide = function (wrapper) {
	wrapper.tiktokAnalysis?.disposeChart();
};
