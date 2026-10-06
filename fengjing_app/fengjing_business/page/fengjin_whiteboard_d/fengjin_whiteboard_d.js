frappe.pages['fengjin_whiteboard_d'].on_page_load = function(wrapper) {
	const page = frappe.ui.make_app_page({
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
	let storage = null;
	let whiteboard = null;
	let synchronizingFields = false;
	let spacePressed = false;

	const titleField = page.add_field({
		fieldname: 'whiteboard_title',
		fieldtype: 'Data',
		label: '白板名称',
		default: '未命名白板',
		change: () => {
			if (!synchronizingFields && storage) {
				storage.setMetadata({ whiteboard_title: titleField.get_value() });
			}
		}
	});
	const folderField = page.add_field({
		fieldname: 'folder_name',
		fieldtype: 'Data',
		label: '所属文件夹',
		change: () => {
			if (!synchronizingFields && storage) {
				storage.setMetadata({ folder_name: folderField.get_value() });
			}
		}
	});

	function setStatus({ status, message }) {
		const colors = {
			dirty: 'orange',
			error: 'red',
			loading: 'blue',
			new: 'gray',
			restored: 'orange',
			saved: 'green',
			saving: 'blue'
		};
		page.set_indicator(message || '白板', colors[status] || 'gray');
	}

	async function syncDocumentFields(doc) {
		synchronizingFields = true;
		try {
			await titleField.set_value(doc.whiteboard_title || '未命名白板');
			await folderField.set_value(doc.folder_name || '');
		} finally {
			synchronizingFields = false;
		}
	}

	function confirmIfDirty(message) {
		if (!storage?.isDirty) return Promise.resolve(true);
		return new Promise(resolve => {
			frappe.confirm(message, () => resolve(true), () => resolve(false));
		});
	}

	function showError(title, error) {
		frappe.msgprint({
			title,
			message: error?.message || '请稍后重试或查看错误日志。',
			indicator: 'red'
		});
	}

	function replaceScene(scene) {
		storage.setCaptureEnabled(false);
		whiteboard.replaceScene(container, scene);
	}

	async function saveCurrent() {
		try {
			const doc = await storage.saveNow({ force: true });
			frappe.show_alert({ message: `白板“${doc.whiteboard_title}”已保存`, indicator: 'green' }, 4);
		} catch (error) {
			showError('白板保存失败', error);
		}
	}

	async function createNew() {
		if (!await confirmIfDirty('当前白板还有未保存内容，确定新建白板吗？')) return;
		try {
			const created = await storage.createNew();
			await syncDocumentFields(created.document);
			replaceScene(created.scene);
		} catch (error) {
			showError('新建白板失败', error);
		}
	}

	async function openWhiteboard() {
		if (!await confirmIfDirty('当前白板还有未保存内容，确定打开其他白板吗？')) return;
		let whiteboards;
		try {
			whiteboards = await storage.list();
		} catch (error) {
			showError('白板列表读取失败', error);
			return;
		}
		if (!whiteboards.length) {
			frappe.show_alert({ message: '当前没有已保存的白板', indicator: 'orange' }, 4);
			return;
		}
		frappe.prompt(
			[
				{
					fieldname: 'whiteboard',
					fieldtype: 'Autocomplete',
					label: '选择白板',
					options: whiteboards.map(board => ({
						value: board.name,
						label: board.whiteboard_title || board.name,
						description: [board.folder_name, board.modified].filter(Boolean).join(' · ')
					})),
					reqd: 1
				}
			],
			async values => {
				try {
					const scene = await storage.load(values.whiteboard);
					await syncDocumentFields(storage.document);
					replaceScene(scene);
				} catch (error) {
					showError('打开白板失败', error);
				}
			},
			'打开白板',
			'打开'
		);
	}

	function saveAs() {
		frappe.prompt(
			[
				{
					fieldname: 'whiteboard_title',
					fieldtype: 'Data',
					label: '新白板名称',
					default: storage.document.whiteboard_title || '未命名白板',
					reqd: 1
				},
				{
					fieldname: 'folder_name',
					fieldtype: 'Data',
					label: '所属文件夹',
					default: storage.document.folder_name || ''
				}
			],
			async values => {
				try {
					const doc = await storage.saveAs(values.whiteboard_title, values.folder_name);
					await syncDocumentFields(doc);
					frappe.show_alert({ message: `已另存为“${doc.whiteboard_title}”`, indicator: 'green' }, 4);
				} catch (error) {
					showError('白板另存为失败', error);
				}
			},
			'另存为白板',
			'保存'
		);
	}

	function deleteCurrent() {
		if (!storage?.document.name) {
			frappe.show_alert({ message: '当前白板尚未保存，无需删除', indicator: 'orange' }, 4);
			return;
		}
		const title = storage.document.whiteboard_title || '未命名白板';
		const deletedName = storage.document.name;
		frappe.confirm(
			`确定删除白板“${frappe.utils.escape_html(title)}”吗？该白板中的图片附件也会一起删除。`,
			async () => {
				try {
					const result = await storage.deleteCurrent();
					if (deletedName && frappe.model?.clear_doc) {
						frappe.model.clear_doc('Fengjin Excalidraw whiteboard storage', deletedName);
					}
					await syncDocumentFields(result.document);
					replaceScene(result.scene);
					frappe.show_alert({ message: `白板“${title}”已删除`, indicator: 'green' }, 4);
				} catch (error) {
					showError('删除白板失败', error);
				}
			}
		);
	}

	page.set_primary_action('保存', saveCurrent, 'save');
	page.add_inner_button('另存为', saveAs);
	page.add_inner_button('打开', openWhiteboard);
	page.add_inner_button('新建', createNew);
	const deleteButton = page.add_inner_button('删除白板', deleteCurrent);
	deleteButton.removeClass('btn-default').addClass('btn-danger');
	page.set_indicator('正在加载…', 'blue');

	window.EXCALIDRAW_ASSET_PATH = excalidrawAssetRoot;
	frappe.require([
		`${excalidrawAssetRoot}index.css`,
		'/assets/fengjing_app/js/whiteboard/fengjing_excalidraw.css'
	], async () => {
		try {
			window.fengjingWhiteboardAssetVersion ??= Date.now();
			const moduleUrl = `${whiteboardModuleRoot}fengjing_excalidraw.js?v=${window.fengjingWhiteboardAssetVersion}`;
			const whiteboardModule = await import(moduleUrl);
			whiteboard = whiteboardModule.default || window.FengjingWhiteboard;

			if (!whiteboard || !whiteboardModule.FengjingWhiteboardStorage) {
				throw new Error('白板模块没有提供存储接口');
			}

			storage = new whiteboardModule.FengjingWhiteboardStorage({
				onStatusChange: setStatus,
				onDocumentChange: syncDocumentFields
			});
			const initialScene = await storage.restoreDraft();
			await syncDocumentFields(storage.document);
			$main.find('.fjwb-loading').remove();
			whiteboard.mount(container, {
				initialData: initialScene,
				onChange: (elements, appState, files) => storage.captureChange(elements, appState, files),
				excalidrawAPI: () => {
					requestAnimationFrame(() => requestAnimationFrame(() => storage.setCaptureEnabled(true)));
				}
			});
			if (!storage.isDirty && !storage.document.name) {
				setStatus({ status: 'new', message: '尚未新建白板' });
			}
			wrapper.fengjing_whiteboard = { container, page, storage, whiteboard };
		} catch (error) {
			console.error('丰境白板加载失败', error);
			$main.find('.fjwb-loading')
				.removeClass('fjwb-loading')
				.addClass('fjwb-error')
				.text('白板资源加载失败，请重新构建 fengjing_app 前端资源。');
			page.set_indicator('加载失败', 'red');
		}
	});

	wrapper.addEventListener('keydown', event => {
		if (event.code === 'Space' && !event.target.matches?.('input, textarea, select, [contenteditable="true"]')) {
			spacePressed = true;
		}
		if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 's') return;
		if (!event.target.closest?.('.fjwb-canvas')) return;
		if (event.target.matches?.('input, textarea, select, [contenteditable="true"]')) return;
		event.preventDefault();
		event.stopImmediatePropagation();
		saveCurrent();
	}, true);

	wrapper.addEventListener('keyup', event => {
		if (event.code === 'Space') spacePressed = false;
	}, true);

	window.addEventListener('blur', () => {
		spacePressed = false;
	});

	wrapper.addEventListener('wheel', event => {
		if (!container.contains(event.target)) return;
		if (!event.target.matches?.('canvas, textarea, iframe')) return;
		if (event.ctrlKey || event.metaKey || event.shiftKey || spacePressed) return;

		event.preventDefault();
		event.stopImmediatePropagation();
		event.target.dispatchEvent(new WheelEvent('wheel', {
			bubbles: true,
			cancelable: true,
			view: window,
			ctrlKey: true,
			metaKey: true,
			clientX: event.clientX,
			clientY: event.clientY,
			deltaX: event.deltaX,
			deltaY: event.deltaY,
			deltaZ: event.deltaZ,
			deltaMode: event.deltaMode
		}));
	}, { capture: true, passive: false });

	window.addEventListener('beforeunload', event => {
		if (!storage?.isDirty || !frappe.get_route()?.includes('fengjin_whiteboard_d')) return;
		event.preventDefault();
		event.returnValue = '';
	});
};
