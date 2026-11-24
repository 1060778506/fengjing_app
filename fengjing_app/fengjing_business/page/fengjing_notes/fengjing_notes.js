frappe.pages["fengjing_notes"].on_page_load = async function (wrapper) {
	const assets = "/assets/fengjing_app/notes/fengjing_notes";
	const version = "20261124-centered-title";
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "丰境笔记",
		single_column: true,
	});
	page.main.html('<div class="fj-notes-mount">正在加载笔记…</div>');
	const mount = page.main.find(".fj-notes-mount")[0];
	wrapper.resizeNotes = () => {
		if (mount.getClientRects().length) {
			const top = mount.getBoundingClientRect().top + window.scrollY;
			mount.style.setProperty("--fj-notes-top", `${Math.max(0, Math.round(top))}px`);
		}
	};
	wrapper.resizeNotes();
	window.addEventListener("resize", wrapper.resizeNotes);
	try {
		await new Promise((resolve, reject) => {
			const stylesheet = document.createElement("link");
			stylesheet.rel = "stylesheet";
			stylesheet.href = `${assets}.css?v=${version}`;
			stylesheet.onload = resolve;
			stylesheet.onerror = () => {
				stylesheet.remove();
				reject(new Error("丰境笔记样式加载失败"));
			};
			document.head.appendChild(stylesheet);
		});
		const { mountNotes } = await import(`${assets}.js?v=${version}`);
		mountNotes(mount);
	} catch (error) {
		console.error("丰境笔记加载失败", error);
		mount.textContent = "丰境笔记加载失败，请刷新页面重试。";
	}
};

frappe.pages["fengjing_notes"].on_page_show = function (wrapper) {
	requestAnimationFrame(() => wrapper.resizeNotes?.());
};
