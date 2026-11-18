frappe.pages["fengjing_idea_board"].on_page_load = async function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "丰境想法池",
		single_column: true,
	});
	page.main.html('<div class="fjib-mount">正在加载想法池…</div>');
	const mount = page.main.find(".fjib-mount")[0];
	if (!document.querySelector('link[data-fjib-style]')) {
		const link = document.createElement("link");
		link.rel = "stylesheet";
		link.href = "/assets/fengjing_app/idea_board/fengjing_idea_board.css";
		link.dataset.fjibStyle = "1";
		document.head.appendChild(link);
	}
	try {
		const module = await import("/assets/fengjing_app/idea_board/fengjing_idea_board.js");
		wrapper.fjibRoot = module.mountIdeaBoard(mount);
	} catch (error) {
		console.error("想法池加载失败", error);
		mount.textContent = "想法池加载失败，请刷新页面重试。";
	}
};
