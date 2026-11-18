frappe.pages["fengjing_idea_board"].on_page_load = async function (wrapper) {
	const assetVersion = "20261118-menu-without-flex-width";
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "丰境经营蓝图",
		single_column: true,
	});
	page.main.html('<div class="fjib-mount">正在加载经营蓝图…</div>');
	const mount = page.main.find(".fjib-mount")[0];
	if (!document.querySelector('link[data-fjib-style]')) {
		const link = document.createElement("link");
		link.rel = "stylesheet";
		link.href = `/assets/fengjing_app/idea_board/fengjing_idea_board.css?v=${assetVersion}`;
		link.dataset.fjibStyle = "1";
		document.head.appendChild(link);
	}
	try {
		const module = await import(`/assets/fengjing_app/idea_board/fengjing_idea_board.js?v=${assetVersion}`);
		wrapper.fjibRoot = module.mountIdeaBoard(mount);
	} catch (error) {
		console.error("经营蓝图加载失败", error);
		mount.textContent = "经营蓝图加载失败，请刷新页面重试。";
	}
};
