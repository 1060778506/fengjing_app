frappe.pages['amazon_configuration'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: '亚马逊配置中心',
		single_column: true
	});
	wrapper.platformConfigurationCenter = new AmazonConfigurationCenter({
		page,
		wrapper,
		platform: 'amazon',
		api: 'fengjing_app.fengjing_business.page.amazon_configuration.amazon_configuration',
		route: 'amazon_configuration',
		eyebrow: 'AMAZON · AUTOMATION CONTROL'
	});
};

frappe.pages['amazon_configuration'].on_page_show = function (wrapper) {
	const center = wrapper.platformConfigurationCenter;
	if (center && center.loaded) center.load(true);
};

class AmazonConfigurationCenter {
	constructor(options) {
		Object.assign(this, options);
		this.activeSection = 'stores';
		this.search = '';
		this.filter = 'all';
		this.layout = 'grid';
		this.autoRefresh = true;
		this.loaded = false;
		this.loading = false;
		this.data = null;
		this.root = $(`<div class="pc-root pc-${this.platform}"></div>`).appendTo(this.page.main);
		this.page.set_primary_action(__('刷新状态'), () => this.load(), 'refresh');
		this.page.add_inner_button(__('新建店铺'), () => this.openEditor('stores'));
		this.bindEvents();
		this.renderLoading();
		this.load();
		this.timer = window.setInterval(() => {
			if (this.autoRefresh && frappe.get_route_str() === this.route && !this.loading) {
				this.load(true);
			}
		}, 30000);
	}

	bindEvents() {
		this.root.on('click', '[data-section]', (event) => {
			this.activeSection = event.currentTarget.dataset.section;
			this.render();
		});
		this.root.on('input', '[data-role="search"]', (event) => {
			this.search = event.currentTarget.value || '';
			this.renderCardsOnly();
		});
		this.root.on('change', '[data-role="filter"]', (event) => {
			this.filter = event.currentTarget.value;
			this.renderCardsOnly();
		});
		this.root.on('click', '[data-action]', (event) => this.handleAction(event));
	}

	async call(method, args = {}, freeze = false, freezeMessage = '') {
		const response = await frappe.call({
			method: `${this.api}.${method}`,
			args,
			freeze,
			freeze_message: freezeMessage
		});
		return response.message;
	}

	async load(silent = false) {
		if (this.loading) return;
		this.loading = true;
		if (!silent) this.root.addClass('pc-is-loading');
		try {
			this.data = await this.call('get_dashboard');
			if (!this.data.sections.some((section) => section.key === this.activeSection)) {
				this.activeSection = this.data.sections[0]?.key || '';
			}
			this.loaded = true;
			this.render();
		} catch (error) {
			this.renderError(error);
		} finally {
			this.loading = false;
			this.root.removeClass('pc-is-loading');
		}
	}

	renderLoading() {
		this.root.html(`
			<div class="pc-loading">
				<div class="pc-loading-orbit"><i></i><i></i><i></i></div>
				<strong>${__('正在读取平台配置')}</strong>
				<span>${__('连接配置、任务与运行状态…')}</span>
			</div>
		`);
	}

	renderError(error) {
		const message = this.escape(error?.message || error || __('页面读取失败'));
		this.root.html(`
			<div class="pc-error-state">
				<div class="pc-error-mark">!</div>
				<h3>${__('配置中心暂时无法读取')}</h3>
				<p>${message}</p>
				<button class="btn btn-primary" data-action="refresh">${__('重新加载')}</button>
			</div>
		`);
	}

	get active() {
		return this.data?.sections?.find((section) => section.key === this.activeSection);
	}

	allDocuments() {
		return (this.data?.sections || []).flatMap((section) =>
			(section.documents || []).map((document) => ({ section, document }))
		);
	}

	statistics() {
		const documents = this.allDocuments();
		let enabled = 0;
		let running = 0;
		let failed = 0;
		documents.forEach(({ section, document }) => {
			const values = document.values || {};
			if (!section.enabled_field || Number(values[section.enabled_field])) enabled += 1;
			const status = String(values[section.status_field] || '').toLowerCase();
			const error = values[section.error_field];
			if (['running', 'waiting'].includes(status)) running += 1;
			if (status === 'failed' || status === 'unavailable' || error) failed += 1;
		});
		return { total: documents.length, enabled, running, failed };
	}

	render() {
		if (!this.data) return;
		const stats = this.statistics();
		const generatedAt = this.formatTime(this.data.generated_at);
		this.root.html(`
			<section class="pc-hero">
				<div class="pc-hero-copy">
					<span class="pc-eyebrow">${this.escape(this.eyebrow)}</span>
					<h2>${this.escape(this.data.title)}</h2>
					<p>${this.escape(this.data.subtitle)}</p>
					<div class="pc-live"><i></i>${__('自动任务已连接')} · ${__('更新于')} ${generatedAt}</div>
				</div>
				<div class="pc-hero-actions">
					<button data-action="toggle-auto" class="pc-hero-button ${this.autoRefresh ? 'active' : ''}">
						<span class="pc-button-dot"></span>${this.autoRefresh ? __('自动刷新中') : __('自动刷新已停')}
					</button>
					<button data-action="fullscreen" class="pc-icon-button" title="${__('页面内全屏')}">⛶</button>
					<button data-action="refresh" class="pc-icon-button" title="${__('刷新')}">↻</button>
				</div>
			</section>
			<section class="pc-stat-grid">
				${this.statCard(__('全部配置'), stats.total, 'all', '01')}
				${this.statCard(__('已启用'), stats.enabled, 'enabled', '02')}
				${this.statCard(__('任务运行中'), stats.running, 'running', '03')}
				${this.statCard(__('需要处理'), stats.failed, stats.failed ? 'danger' : 'healthy', '04')}
			</section>
			<section class="pc-workspace">
				<nav class="pc-tabs">${this.renderTabs()}</nav>
				<div class="pc-section-head">
					<div><span>${this.escape(this.active?.doctype || '')}</span><h3>${this.escape(this.active?.title || '')}</h3><p>${this.escape(this.active?.description || '')}</p></div>
					${this.active?.permissions?.create ? `<button class="pc-add-button" data-action="add"><b>＋</b>${__('新建配置')}</button>` : ''}
				</div>
				<div class="pc-toolbar">
					<label class="pc-search"><span>⌕</span><input data-role="search" value="${this.escape(this.search)}" placeholder="${__('搜索店铺、状态或配置内容')}"></label>
					<select data-role="filter">
						<option value="all" ${this.filter === 'all' ? 'selected' : ''}>${__('全部状态')}</option>
						<option value="enabled" ${this.filter === 'enabled' ? 'selected' : ''}>${__('仅已启用')}</option>
						<option value="disabled" ${this.filter === 'disabled' ? 'selected' : ''}>${__('仅已停用')}</option>
						<option value="running" ${this.filter === 'running' ? 'selected' : ''}>${__('运行中')}</option>
						<option value="failed" ${this.filter === 'failed' ? 'selected' : ''}>${__('存在异常')}</option>
					</select>
					<div class="pc-layout-switch"><button data-action="layout-grid" class="${this.layout === 'grid' ? 'active' : ''}">▦</button><button data-action="layout-list" class="${this.layout === 'list' ? 'active' : ''}">☷</button></div>
				</div>
				<div data-role="cards">${this.cardsHtml()}</div>
			</section>
		`);
	}

	statCard(label, value, type, index) {
		return `<article class="pc-stat pc-stat-${type}"><span>${index}</span><strong>${value}</strong><p>${this.escape(label)}</p></article>`;
	}

	renderTabs() {
		return (this.data.sections || []).map((section, index) => {
			const failed = (section.documents || []).filter((document) => this.isFailed(section, document)).length;
			return `<button data-section="${section.key}" class="${section.key === this.activeSection ? 'active' : ''}"><span>0${index + 1}</span>${this.escape(section.title)}<b>${section.documents.length}</b>${failed ? '<i></i>' : ''}</button>`;
		}).join('');
	}

	filteredDocuments() {
		const section = this.active;
		const query = this.search.trim().toLowerCase();
		return (section?.documents || []).filter((document) => {
			const values = document.values || {};
			const enabled = !section.enabled_field || Number(values[section.enabled_field]);
			const status = String(values[section.status_field] || '').toLowerCase();
			let matchesFilter = true;
			if (this.filter === 'enabled') matchesFilter = Boolean(enabled);
			if (this.filter === 'disabled') matchesFilter = !enabled;
			if (this.filter === 'running') matchesFilter = ['running', 'waiting'].includes(status);
			if (this.filter === 'failed') matchesFilter = this.isFailed(section, document);
			if (!matchesFilter) return false;
			if (!query) return true;
			return Object.values(values).some((value) => String(value ?? '').toLowerCase().includes(query));
		});
	}

	renderCardsOnly() {
		const target = this.root.find('[data-role="cards"]');
		if (target.length) target.html(this.cardsHtml());
	}

	cardsHtml() {
		const section = this.active;
		const documents = this.filteredDocuments();
		if (!documents.length) {
			return `<div class="pc-empty"><div>◇</div><h4>${__('没有符合条件的配置')}</h4><p>${__('调整筛选条件，或新建第一条配置。')}</p>${section?.permissions?.create ? `<button data-action="add">＋ ${__('新建配置')}</button>` : ''}</div>`;
		}
		return `<div class="pc-card-grid pc-layout-${this.layout}">${documents.map((document) => this.cardHtml(section, document)).join('')}</div>`;
	}

	cardHtml(section, document) {
		const values = document.values || {};
		const primary = values[section.primary_field] || document.name;
		const enabled = !section.enabled_field || Number(values[section.enabled_field]);
		const status = values[section.status_field] || (enabled ? __('已启用') : __('已停用'));
		const error = values[section.error_field] || values.history_last_error || '';
		const progress = section.progress_field ? Number(values[section.progress_field] || 0) : 0;
		const extraStatus = values.performance_api_status;
		return `
			<article class="pc-config-card ${this.isFailed(section, document) ? 'has-error' : ''}" data-name="${this.escape(document.name)}">
				<header>
					<div class="pc-card-identity"><span>${this.initials(primary)}</span><div><small>${this.escape(section.title)}</small><h4>${this.escape(primary)}</h4><code>${this.escape(document.name)}</code></div></div>
					<div class="pc-card-tools">
						<span class="pc-enabled ${enabled ? 'on' : 'off'}"><i></i>${enabled ? __('已启用') : __('已停用')}</span>
						<button data-action="edit" data-name="${this.escape(document.name)}" title="${__('编辑')}">✎</button>
						<button data-action="open" data-name="${this.escape(document.name)}" title="${__('打开原始单据')}">↗</button>
					</div>
				</header>
				<div class="pc-card-status">
					${this.statusPill(status, error)}
					${extraStatus ? `<span class="pc-secondary-status">Performance · ${this.escape(extraStatus)}</span>` : ''}
				</div>
				${progress ? `<div class="pc-progress"><span><i style="width:${Math.min(100, Math.max(0, progress))}%"></i></span><b>${progress.toFixed(1)}%</b></div>` : ''}
				<div class="pc-time-grid">
					${this.timeItem(__('最近执行'), values[section.last_field])}
					${this.timeItem(__('下次执行'), values[section.next_field])}
				</div>
				${error ? `<div class="pc-error-line" title="${this.escape(error)}"><b>!</b><span>${this.escape(error)}</span></div>` : ''}
				<footer>
					<div class="pc-action-list">${(section.actions || []).map((action) => `<button class="pc-action-${action.style || 'ghost'}" data-action="run" data-task="${action.key}" data-name="${this.escape(document.name)}">${this.escape(action.label)}</button>`).join('')}</div>
					${section.permissions.delete ? `<button class="pc-delete" data-action="delete" data-name="${this.escape(document.name)}" title="${__('删除配置')}">⌫</button>` : ''}
				</footer>
			</article>
		`;
	}

	statusPill(status, error) {
		const value = String(status || __('未知'));
		const lower = value.toLowerCase();
		let tone = 'neutral';
		if (['success', 'available', 'completed', '已完成', '成功'].includes(lower)) tone = 'success';
		if (['running', 'waiting', 'paused', '运行中', '等待执行'].includes(lower)) tone = 'running';
		if (error || ['failed', 'unavailable', '失败'].includes(lower)) tone = 'danger';
		return `<span class="pc-status pc-status-${tone}"><i></i>${this.escape(value)}</span>`;
	}

	timeItem(label, value) {
		return `<div><span>${this.escape(label)}</span><strong>${value ? this.formatTime(value) : '—'}</strong></div>`;
	}

	isFailed(section, document) {
		const values = document.values || {};
		const status = String(values[section.status_field] || '').toLowerCase();
		return Boolean(values[section.error_field] || values.history_last_error || ['failed', 'unavailable'].includes(status));
	}

	async handleAction(event) {
		const button = event.currentTarget;
		const action = button.dataset.action;
		const name = button.dataset.name;
		if (action === 'refresh') return this.load();
		if (action === 'add') return this.openEditor(this.activeSection);
		if (action === 'edit') return this.openEditor(this.activeSection, name);
		if (action === 'open') return frappe.set_route('Form', this.active.doctype, name);
		if (action === 'delete') return this.deleteDocument(name);
		if (action === 'run') return this.runTask(name, button.dataset.task, button);
		if (action === 'toggle-auto') {
			this.autoRefresh = !this.autoRefresh;
			return this.render();
		}
		if (action === 'layout-grid' || action === 'layout-list') {
			this.layout = action.replace('layout-', '');
			return this.render();
		}
		if (action === 'fullscreen') {
			this.root.toggleClass('pc-fullscreen');
			$('body').toggleClass('pc-fullscreen-open', this.root.hasClass('pc-fullscreen'));
		}
	}

	dialogFields(section, document) {
		const result = [];
		let pendingSection = null;
		let pendingColumn = null;
		(section.fields || []).forEach((field) => {
			if (field.fieldtype === 'Section Break') {
				pendingSection = field;
				pendingColumn = null;
				return;
			}
			if (field.fieldtype === 'Column Break') {
				pendingColumn = field;
				return;
			}
			if (field.read_only) return;
			if (pendingSection) {
				result.push({ fieldtype: 'Section Break', label: pendingSection.label || '' });
				pendingSection = null;
			}
			if (pendingColumn) {
				result.push({ fieldtype: 'Column Break' });
				pendingColumn = null;
			}
			const passwordNote = field.sensitive && document
				? __('密钥已经安全保存；留空表示保持原值。')
				: field.description;
			result.push({
				fieldname: field.fieldname,
				fieldtype: field.fieldtype,
				label: field.label,
				options: field.options,
				description: passwordNote,
				default: field.default,
				reqd: field.sensitive && document ? 0 : field.reqd,
				depends_on: field.depends_on,
				mandatory_depends_on: field.mandatory_depends_on
			});
		});
		return result;
	}

	openEditor(sectionKey, name = null) {
		const section = this.data.sections.find((item) => item.key === sectionKey);
		const document = name ? section.documents.find((item) => item.name === name) : null;
		const dialog = new frappe.ui.Dialog({
			title: document ? `${__('编辑')} · ${document.values[section.primary_field] || document.name}` : `${__('新建')} · ${section.title}`,
			size: 'extra-large',
			fields: this.dialogFields(section, document),
			primary_action_label: __('保存配置'),
			primary_action: async () => {
				const values = dialog.get_values();
				if (!values) return;
				dialog.get_primary_btn().prop('disabled', true).text(__('保存中…'));
				try {
					const result = await this.call('save_configuration', {
						section_key: section.key,
						name: document?.name || null,
						values: JSON.stringify(values)
					}, true, __('正在保存配置…'));
					dialog.hide();
					frappe.show_alert({ message: result?.message || __('配置已保存'), indicator: 'green' }, 5);
					await this.load(true);
				} catch (error) {
					dialog.get_primary_btn().prop('disabled', false).text(__('保存配置'));
				}
			}
		});
		dialog.show();
		dialog.$wrapper.addClass('pc-config-dialog');
		if (document) dialog.set_values(document.values || {});
		Object.entries(document?.password_set || {}).forEach(([fieldname, isSet]) => {
			if (!isSet) return;
			const control = dialog.get_field(fieldname);
			if (control?.$input) control.$input.attr('placeholder', __('已保存，留空保持不变'));
		});
	}

	deleteDocument(name) {
		frappe.confirm(
			__('确定删除配置 {0} 吗？如果其他配置仍引用它，系统会阻止删除。', [name]),
			async () => {
				const result = await this.call('delete_configuration', { section_key: this.activeSection, name }, true, __('正在删除配置…'));
				frappe.show_alert({ message: result?.message || __('配置已删除'), indicator: 'orange' }, 5);
				await this.load(true);
			}
		);
	}

	runTask(name, taskKey, button) {
		const task = this.active.actions.find((item) => item.key === taskKey);
		const execute = async () => {
			button.disabled = true;
			button.classList.add('working');
			try {
				const result = await this.call('run_action', {
					section_key: this.activeSection,
					name,
					action_key: taskKey
				}, true, __('正在提交后台任务…'));
				frappe.show_alert({ message: result?.message || __('任务已提交'), indicator: 'green' }, 7);
				window.setTimeout(() => this.load(true), 900);
			} finally {
				button.disabled = false;
				button.classList.remove('working');
			}
		};
		if (task?.confirm) {
			frappe.confirm(__('确定执行“{0}”吗？任务将在后台运行。', [task.label]), execute);
		} else {
			execute();
		}
	}

	formatTime(value) {
		if (!value) return '—';
		try {
			return frappe.datetime.str_to_user(String(value).split('.')[0]);
		} catch (error) {
			return this.escape(value);
		}
	}

	initials(value) {
		const text = String(value || 'A').replace(/[^A-Za-z0-9\u4e00-\u9fa5]/g, '');
		return this.escape(text.slice(0, 2).toUpperCase() || 'AM');
	}

	escape(value) {
		return frappe.utils.escape_html(String(value ?? ''));
	}
}
