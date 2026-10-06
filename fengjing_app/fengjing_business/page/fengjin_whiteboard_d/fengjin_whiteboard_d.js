frappe.pages['fengjin_whiteboard_d'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: '丰境白板绘图',
		single_column: true
	});

	$(wrapper).addClass('fjwb-page');
	const $main = $(wrapper).find('.layout-main-section');
	$main.html(`
		<div class="fjwb-shell">
			<div class="fjwb-loading">正在加载白板…</div>
			<div class="fjwb-canvas" aria-label="丰境白板绘图"></div>
		</div>
	`);

	const container = $main.find('.fjwb-canvas')[0];
	const excalidrawAssetRoot = '/assets/fengjing_app/node_modules/@excalidraw/excalidraw/dist/prod/';
	const whiteboardModuleRoot = '/assets/fengjing_app/whiteboard/excalidraw/';
	window.EXCALIDRAW_ASSET_PATH = excalidrawAssetRoot;
	frappe.require([
		`${excalidrawAssetRoot}index.css`,
		'/assets/fengjing_app/js/whiteboard/fengjing_excalidraw.css'
	], async () => {
		try {
			window.fengjingWhiteboardAssetVersion ??= Date.now();
			const moduleUrl = `${whiteboardModuleRoot}fengjing_excalidraw.js?v=${window.fengjingWhiteboardAssetVersion}`;
			const whiteboardModule = await import(moduleUrl);
			const whiteboard = whiteboardModule.default || window.FengjingWhiteboard;

			if (!whiteboard) {
				throw new Error('白板模块没有提供加载接口');
			}

			$main.find('.fjwb-loading').remove();
			whiteboard.mount(container);
			wrapper.fengjing_whiteboard = { container, whiteboard, page };
		} catch (error) {
			console.error('丰境白板加载失败', error);
			$main.find('.fjwb-loading')
				.removeClass('fjwb-loading')
				.addClass('fjwb-error')
				.text('白板资源加载失败，请重新构建 fengjing_app 前端资源。');
		}
	});
};
