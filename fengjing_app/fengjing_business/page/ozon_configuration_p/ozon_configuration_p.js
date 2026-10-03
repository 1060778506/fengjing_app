frappe.pages["ozon_configuration_p"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Ozon配置中心"),
		single_column: true,
	});
	wrapper.platformConfigurationCenter = new OzonConfigurationCenter({
		page,
		wrapper,
		platform: "ozon",
		api: "fengjing_app.fengjing_business.page.ozon_configuration_p.ozon_configuration_p",
		route: "ozon_configuration_p",
		brand: "Ozon",
		brandMark: "O",
	});
};

frappe.pages["ozon_configuration_p"].on_page_show = function (wrapper) {
	const center = wrapper.platformConfigurationCenter;
	if (center?.loaded && !center.dirty) center.load(true);
};

frappe.pages["ozon_configuration_p"].on_page_hide = function (wrapper) {
	wrapper.platformConfigurationCenter?.exitFullscreen();
};

class OzonConfigurationCenter {
	constructor(options) {
		Object.assign(this, options);
		this.activeSection = "stores";
		this.selectedStoreName = null;
		this.creatingStore = false;
		this.storeSearch = "";
		this.controls = new Map();
		this.loaded = false;
		this.loading = false;
		this.dirty = false;
		this.mountingControls = false;
		this.data = null;
		this.root = $(`<div class="pc-root pc-${this.platform}"></div>`).appendTo(this.page.main);
		this.page.set_primary_action(__("刷新配置"), () => this.refreshWithGuard(), "refresh");
		this.page.add_inner_button(__("新建店铺"), () => this.startNewStore());
		this.bindEvents();
		this.renderLoading();
		this.load();
	}

	bindEvents() {
		this.root.on("click", "[data-action]", (event) => this.handleAction(event));
		this.root.on("input", "[data-role='store-search']", (event) => {
			this.storeSearch = event.currentTarget.value || "";
			this.renderStoreList();
		});
	}

	async call(method, args = {}, freeze = false, freezeMessage = "") {
		const response = await frappe.call({ method: `${this.api}.${method}`, args, freeze, freeze_message: freezeMessage });
		return response.message;
	}

	async load(silent = false) {
		if (this.loading) return;
		this.loading = true;
		if (!silent) this.root.addClass("pc-is-loading");
		try {
			this.data = await this.call("get_dashboard");
			const stores = this.stores();
			if (!this.creatingStore && !stores.some((store) => store.name === this.selectedStoreName)) {
				this.selectedStoreName = stores[0]?.name || null;
			}
			if (!this.data.sections.some((section) => section.key === this.activeSection)) this.activeSection = "stores";
			this.loaded = true;
			this.dirty = false;
			this.render();
		} catch (error) {
			this.renderError(error);
		} finally {
			this.loading = false;
			this.root.removeClass("pc-is-loading");
		}
	}

	get active() { return this.data?.sections?.find((section) => section.key === this.activeSection); }
	get storeSection() { return this.data?.sections?.find((section) => section.key === "stores"); }
	stores() { return this.storeSection?.documents || []; }
	currentStore() { return this.creatingStore ? null : this.stores().find((store) => store.name === this.selectedStoreName) || null; }

	currentDocument(section = this.active) {
		if (!section) return null;
		if (section.key === "stores") return this.currentStore();
		const store = this.currentStore();
		if (!store) return null;
		return (section.documents || []).find((document) => String(document.values?.[section.primary_field] || "") === String(store.name)) || null;
	}

	renderLoading() {
		this.root.html(`<div class="pc-loading"><div class="pc-loading-mark"><span></span><span></span><span></span></div><strong>${__("正在准备配置工作台")}</strong><small>${__("读取店铺、配置和运行状态…")}</small></div>`);
	}

	renderError(error) {
		this.root.html(`<div class="pc-empty-state pc-error-state"><div class="pc-empty-icon">!</div><h3>${__("配置工作台读取失败")}</h3><p>${this.escape(error?.message || error || __("未知错误"))}</p><button type="button" class="pc-button pc-button-primary" data-action="refresh">${__("重新加载")}</button></div>`);
	}

	render() {
		if (!this.data) return;
		const store = this.currentStore();
		this.root.html(`
			<header class="pc-topbar">
				<div class="pc-brand"><div class="pc-brand-mark">${this.escape(this.brandMark || this.brand?.slice(0, 1) || "F")}</div><div><span>${this.escape(this.brand)} · CONFIGURATION</span><h2>${this.escape(this.data.title)}</h2><p>${store ? `${this.escape(this.storeLabel(store))} · ${this.escape(this.active?.title || "")}` : this.escape(this.data.subtitle)}</p></div></div>
				<div class="pc-top-actions"><span class="pc-saved-state" data-role="save-state"><i></i>${__("配置已同步")}</span><button type="button" data-action="refresh" title="${__("刷新配置")}">${this.icon("refresh")}</button><button type="button" data-action="fullscreen" title="${__("页面内全屏")}">${this.icon("expand")}</button></div>
			</header>
			<div class="pc-console">
				<aside class="pc-store-rail">
					<div class="pc-rail-head"><div><span>${__("第一步")}</span><h3>${__("选择店铺")}</h3></div><button type="button" data-action="new-store" title="${__("新建店铺")}">${this.icon("plus")}</button></div>
					<label class="pc-store-search">${this.icon("search")}<input data-role="store-search" value="${this.escape(this.storeSearch)}" placeholder="${__("搜索店铺")}"></label>
					<div class="pc-store-list" data-role="store-list">${this.storeListHtml()}</div>
					<div class="pc-rail-foot"><span></span>${__("凭证仅在保存时传输")}</div>
				</aside>
				<nav class="pc-config-rail"><div class="pc-config-head"><span>${__("第二步")}</span><h3>${__("配置菜单")}</h3><p>${__("选择要维护的业务配置")}</p></div><div class="pc-config-menu">${this.configMenuHtml()}</div></nav>
				<main class="pc-editor-pane" data-role="editor">${this.editorHtml()}</main>
			</div>
		`);
		this.mountEditorControls();
	}

	storeListHtml() {
		const query = this.storeSearch.trim().toLowerCase();
		const stores = this.stores().filter((store) => !query || Object.values(store.values || {}).some((value) => String(value ?? "").toLowerCase().includes(query)));
		const createItem = this.creatingStore ? `<button type="button" class="pc-store-item active is-new" data-action="new-store"><span class="pc-store-avatar">＋</span><span><b>${__("新店铺")}</b><small>${__("尚未保存")}</small></span></button>` : "";
		if (!stores.length && !createItem) return `<div class="pc-store-empty"><p>${query ? __("没有匹配的店铺") : __("还没有店铺")}</p><button type="button" data-action="new-store">${__("新建店铺")}</button></div>`;
		return createItem + stores.map((store) => {
			const enabled = !this.storeSection.enabled_field || Number(store.values?.[this.storeSection.enabled_field]);
			const status = store.values?.[this.storeSection.status_field] || (enabled ? __("已启用") : __("已停用"));
			return `<button type="button" class="pc-store-item ${store.name === this.selectedStoreName && !this.creatingStore ? "active" : ""}" data-action="select-store" data-name="${this.escape(store.name)}"><span class="pc-store-avatar">${this.initials(this.storeLabel(store))}</span><span><b>${this.escape(this.storeLabel(store))}</b><small><i class="${enabled ? "on" : "off"}"></i>${this.escape(status)}</small></span>${this.icon("chevron")}</button>`;
		}).join("");
	}

	renderStoreList() { this.root.find("[data-role='store-list']").html(this.storeListHtml()); }

	configMenuHtml() {
		return (this.data.sections || []).map((section) => {
			const document = this.currentDocument(section);
			const state = document && this.isFailed(section, document) ? "error" : document ? "ready" : "empty";
			const disabled = section.key !== "stores" && !this.currentStore();
			return `<button type="button" class="pc-config-item ${section.key === this.activeSection ? "active" : ""}" data-action="select-section" data-section="${section.key}" ${disabled ? "disabled" : ""}><span class="pc-config-icon">${this.icon(section.key)}</span><span><b>${this.escape(section.title)}</b><small>${this.escape(this.menuSubtitle(section, document))}</small></span><i class="pc-menu-state ${state}"></i></button>`;
		}).join("");
	}

	menuSubtitle(section, document) {
		if (section.key === "stores") return this.creatingStore ? __("正在新建") : __("身份与API凭证");
		if (!document) return __("尚未建立配置");
		return !section.enabled_field || Number(document.values?.[section.enabled_field]) ? __("已配置并启用") : __("已配置但停用");
	}

	editorHtml() {
		const section = this.active;
		const store = this.currentStore();
		if (!section || (!store && !this.creatingStore)) {
			return `<div class="pc-empty-state"><div class="pc-empty-icon">${this.icon("stores")}</div><span>${__("配置工作台")}</span><h3>${__("请先选择一个店铺")}</h3><p>${__("从左侧选择现有店铺，或者新建店铺，然后在中间选择要维护的配置。")}</p><button type="button" class="pc-button pc-button-primary" data-action="new-store">${this.icon("plus")}${__("新建店铺")}</button></div>`;
		}
		const document = this.currentDocument(section);
		const isNew = !document;
		const title = section.key === "stores" && isNew ? __("建立新店铺") : section.title;
		const context = section.key === "stores" && isNew ? __("填写店铺身份与API连接信息") : `${this.storeLabel(store)} · ${section.doctype}`;
		return `
			<div class="pc-editor-head"><div class="pc-editor-title"><span class="pc-editor-icon">${this.icon(section.key)}</span><div><small>${this.escape(context)}</small><h3>${this.escape(title)}</h3><p>${this.escape(section.description || "")}</p></div></div><div class="pc-editor-status">${this.statusHtml(section, document)}</div></div>
			${document && (section.actions || []).length ? `<div class="pc-taskbar"><span>${__("手动操作")}</span><div>${section.actions.map((action) => `<button type="button" data-action="run" data-task="${action.key}" class="pc-task-${action.style || "ghost"}">${this.escape(action.label)}</button>`).join("")}</div></div>` : ""}
				<div class="pc-editor-scroll">
				${isNew && section.key !== "stores" ? `<div class="pc-notice">${this.icon("info")}<div><b>${__("尚未建立这项配置")}</b><span>${__("下面已经关联当前店铺，填写后保存即可创建。")}</span></div></div>` : ""}
				${this.errorHtml(section, document)}
				<div class="pc-form-sections">${this.formSectionsHtml(section)}</div>
			</div>
			<footer class="pc-editor-footer"><div>${document && section.permissions.delete ? `<button type="button" class="pc-button pc-button-danger" data-action="delete">${this.icon("trash")}${__("删除此配置")}</button>` : ""}</div><div class="pc-save-actions"><button type="button" class="pc-button pc-button-secondary" data-action="reset">${__("撤销修改")}</button>${(isNew ? section.permissions.create : section.permissions.write) ? `<button type="button" class="pc-button pc-button-primary" data-action="save">${this.icon("save")}${isNew ? __("创建并保存") : __("保存配置")}</button>` : ""}</div></footer>`;
	}

	statusHtml(section, document) {
		if (!document) return `<span class="pc-status neutral"><i></i>${__("新配置")}</span>`;
		const values = document.values || {};
		const enabled = !section.enabled_field || Number(values[section.enabled_field]);
		const status = values[section.status_field] || (enabled ? __("已启用") : __("已停用"));
		const tone = this.isFailed(section, document) ? "danger" : /running|waiting|运行|等待/i.test(status) ? "running" : enabled ? "success" : "neutral";
		return `<span class="pc-status ${tone}"><i></i>${this.escape(status)}</span>${section.last_field ? `<span class="pc-runtime"><small>${__("最近执行")}</small><b>${this.formatTime(values[section.last_field])}</b></span>` : ""}${section.next_field ? `<span class="pc-runtime"><small>${__("下次执行")}</small><b>${this.formatTime(values[section.next_field])}</b></span>` : ""}`;
	}

	errorHtml(section, document) {
		if (!document) return "";
		const error = document.values?.[section.error_field] || document.values?.history_last_error;
		return error ? `<div class="pc-notice danger">${this.icon("warning")}<div><b>${__("最近一次运行出现异常")}</b><span>${this.escape(error)}</span></div></div>` : "";
	}

	fieldGroups(section) {
		const groups = [];
		let current = { title: __("基础设置"), fields: [] };
		(section.fields || []).forEach((field) => {
			if (field.fieldtype === "Section Break") {
				if (current.fields.length) groups.push(current);
				current = { title: this.translateSection(field.label || __("配置选项")), fields: [] };
				return;
			}
			if (field.fieldtype === "Column Break" || field.read_only) return;
			current.fields.push(field);
		});
		if (current.fields.length) groups.push(current);
		return groups;
	}

	formSectionsHtml(section) {
		const groups = this.fieldGroups(section);
		if (!groups.length) return `<div class="pc-no-fields">${__("这项配置没有可编辑字段。")}</div>`;
		return groups.map((group, index) => `<section class="pc-form-section"><header><span>${String(index + 1).padStart(2, "0")}</span><h4>${this.escape(group.title)}</h4></header><div class="pc-form-grid">${group.fields.map((field) => `<div class="pc-form-slot ${this.isWideField(field) ? "wide" : ""}" data-fieldname="${this.escape(field.fieldname)}"></div>`).join("")}</div></section>`).join("");
	}

	mountEditorControls() {
		this.controls.clear();
		const section = this.active;
		if (!section || (!this.currentStore() && !this.creatingStore)) return;
		const document = this.currentDocument(section);
		const prefill = section.key !== "stores" && this.currentStore() ? { [section.primary_field]: this.currentStore().name } : {};
		this.mountingControls = true;
		this.fieldGroups(section).flatMap((group) => group.fields).forEach((field) => {
			const parent = this.root.find(`[data-fieldname="${this.selectorEscape(field.fieldname)}"]`);
			if (!parent.length) return;
			const isStoredSecret = field.sensitive && document?.password_set?.[field.fieldname];
			const isStoreLink = section.key !== "stores" && field.fieldname === section.primary_field;
			const df = { ...field, read_only: isStoreLink || (document && field.set_only_once) ? 1 : 0, reqd: isStoredSecret ? 0 : field.reqd, description: isStoredSecret ? __("密钥已安全保存；留空表示保持原值。") : field.description, change: () => this.markDirty() };
			const control = frappe.ui.form.make_control({ parent, df, render_input: true });
			const value = document ? document.values?.[field.fieldname] : (prefill[field.fieldname] ?? field.default ?? null);
			control.set_value(value);
			if (isStoredSecret && control.$input) control.$input.attr("placeholder", __("已保存，留空保持不变"));
			control.$wrapper.off(".pc-editor").on("input.pc-editor change.pc-editor", ":input", () => this.markDirty());
			this.controls.set(field.fieldname, control);
		});
		window.setTimeout(() => { this.mountingControls = false; }, 0);
	}

	markDirty() {
		if (this.mountingControls || this.dirty) return;
		this.dirty = true;
		this.root.find("[data-role='save-state']").addClass("dirty").html(`<i></i>${__("有未保存修改")}`);
		this.root.find("[data-action='save']").addClass("is-dirty");
	}

	async saveCurrent() {
		const section = this.active;
		const document = this.currentDocument(section);
		const values = {};
		for (const [fieldname, control] of this.controls.entries()) {
			const value = control.get_value();
			if (control.df.reqd && (value === null || value === undefined || value === "")) {
				frappe.msgprint(__("请填写必填字段：{0}", [control.df.label || fieldname]));
				control.$input?.focus();
				return;
			}
			values[fieldname] = value;
		}
		const result = await this.call("save_configuration", { section_key: section.key, name: document?.name || null, values: JSON.stringify(values) }, true, __("正在保存配置…"));
		if (section.key === "stores") this.selectedStoreName = result.name;
		this.creatingStore = false;
		this.dirty = false;
		frappe.show_alert({ message: result?.message || __("配置已保存"), indicator: "green" }, 5);
		await this.load(true);
	}

	async handleAction(event) {
		const button = event.currentTarget;
		const action = button.dataset.action;
		if (action === "select-store") return this.navigate(() => { this.creatingStore = false; this.selectedStoreName = button.dataset.name; this.render(); });
		if (action === "select-section") return this.navigate(() => { this.activeSection = button.dataset.section; this.render(); });
		if (action === "new-store") return this.startNewStore();
		if (action === "refresh") return this.refreshWithGuard();
		if (action === "fullscreen") return this.toggleFullscreen();
		if (action === "reset") return this.navigate(() => { this.dirty = false; this.render(); });
		if (action === "save") return this.saveCurrent();
		if (action === "delete") return this.deleteCurrent();
		if (action === "run") return this.runTask(button.dataset.task, button);
	}

	navigate(callback) {
		if (!this.dirty) return callback();
		frappe.confirm(__("当前修改尚未保存，确定放弃这些修改吗？"), () => { this.dirty = false; callback(); });
	}

	startNewStore() {
		this.navigate(() => { this.creatingStore = true; this.selectedStoreName = null; this.activeSection = "stores"; this.storeSearch = ""; this.render(); });
	}

	refreshWithGuard() { this.navigate(() => this.load()); }

	deleteCurrent() {
		const document = this.currentDocument();
		if (!document) return;
		frappe.confirm(__("确定删除“{0}”吗？如果其他配置仍在引用，系统会阻止删除。", [document.name]), async () => {
			const result = await this.call("delete_configuration", { section_key: this.activeSection, name: document.name }, true, __("正在删除配置…"));
			if (this.activeSection === "stores") this.selectedStoreName = null;
			frappe.show_alert({ message: result?.message || __("配置已删除"), indicator: "orange" }, 5);
			await this.load(true);
		});
	}

	runTask(taskKey, button) {
		const section = this.active;
		const document = this.currentDocument(section);
		const task = section?.actions?.find((item) => item.key === taskKey);
		if (!document || !task) return;
		const execute = async () => {
			button.disabled = true;
			button.classList.add("working");
			try {
				const result = await this.call("run_action", { section_key: section.key, name: document.name, action_key: taskKey }, true, __("正在提交后台任务…"));
				frappe.show_alert({ message: result?.message || __("任务已提交"), indicator: "green" }, 7);
				window.setTimeout(() => { if (!this.dirty) this.load(true); }, 900);
			} finally { button.disabled = false; button.classList.remove("working"); }
		};
		if (task.confirm) frappe.confirm(__("确定执行“{0}”吗？任务将在后台运行。", [task.label]), execute); else execute();
	}

	toggleFullscreen() { this.root.toggleClass("pc-fullscreen"); $("body").toggleClass("pc-fullscreen-open", this.root.hasClass("pc-fullscreen")); }
	exitFullscreen() { this.root.removeClass("pc-fullscreen"); $("body").removeClass("pc-fullscreen-open"); }
	storeLabel(store) { return store?.values?.[this.storeSection?.primary_field] || store?.name || __("未命名店铺"); }

	isFailed(section, document) {
		const values = document?.values || {};
		const status = String(values[section.status_field] || "").toLowerCase();
		return Boolean(values[section.error_field] || values.history_last_error || ["failed", "unavailable"].includes(status));
	}

	isWideField(field) { return ["Long Text", "Small Text", "Text"].includes(field.fieldtype); }

	translateSection(label) {
		const labels = {
			"Store Identity": __("店铺身份"), "Amazon Marketplace": __("亚马逊站点"), "API Credentials": __("API 凭证"), "Connection Status": __("连接设置"),
			"Basic Configuration": __("基础配置"), "Schedule and Result": __("调度设置"), "Product Discovery Result": __("商品发现"), "Runtime Status": __("运行设置"),
			"Historical Order Sync": __("历史订单同步"), "Incremental Order Sync": __("增量订单同步"), "Historical Financial Sync": __("历史财务同步"), "Incremental Financial Sync": __("增量财务同步"), "Manual Sync Actions": __("手动同步"),
			"Historical Ranking Sync": __("历史排名同步"), "Automatic Ranking Sync": __("自动排名同步"), "Historical Settlement Statement Sync": __("历史结算报告同步"),
			"Recent 7 Days Recheck": __("近7天核对"), "Recent 14 Days Recheck": __("近14天核对"), "Recent 30 Days Recheck": __("近30天核对"), "Recent 90 Days Recheck": __("近90天核对"), "Recent 180 Days Recheck": __("近180天核对"),
		};
		return labels[label] || label;
	}

	formatTime(value) {
		if (!value) return "—";
		try { return frappe.datetime.str_to_user(String(value).split(".")[0]); } catch (error) { return this.escape(value); }
	}

	initials(value) { const text = String(value || this.brand || "F").replace(/[^A-Za-z0-9\u4e00-\u9fa5]/g, ""); return this.escape(text.slice(0, 2).toUpperCase() || (this.platform === "ozon" ? "OZ" : "AM")); }
	selectorEscape(value) { return String(value || "").replace(/["\\]/g, "\\$&"); }
	escape(value) { return frappe.utils.escape_html(String(value ?? "")); }

	icon(name) {
		const paths = {
			stores: '<path d="M4 10h16M5 10v10h14V10M3 10l2-6h14l2 6M9 20v-6h6v6"/>', ranking: '<path d="M4 19V9m6 10V5m6 14v-7m4 7H2"/>', orders: '<path d="M4 7l8-4 8 4-8 4-8-4Zm0 0v10l8 4 8-4V7M12 11v10"/>', finances: '<path d="M3 7h18v12H3zM3 10h18M7 15h3"/>', prices: '<path d="M3 12V5a2 2 0 0 1 2-2h7l9 9-9 9-9-9Zm5-4h.01"/>', settlements: '<path d="M6 3h12v18H6zM9 8h6m-6 4h6m-6 4h4"/>',
			refresh: '<path d="M20 6v5h-5M4 18v-5h5M6.1 9A7 7 0 0 1 18 6l2 5M4 13l2 5a7 7 0 0 0 11.9-3"/>', expand: '<path d="M8 3H3v5m13-5h5v5M8 21H3v-5m13 5h5v-5"/>', plus: '<path d="M12 5v14M5 12h14"/>', search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>', chevron: '<path d="m9 18 6-6-6-6"/>', info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>', warning: '<path d="M12 3 2.5 20h19L12 3Zm0 6v5m0 3h.01"/>', trash: '<path d="M4 7h16M9 7V4h6v3m3 0-1 14H7L6 7m4 4v6m4-6v6"/>', save: '<path d="M4 4h13l3 3v13H4zM8 4v6h8V4M8 20v-6h8v6"/>',
		};
		return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.info}</svg>`;
	}
}
