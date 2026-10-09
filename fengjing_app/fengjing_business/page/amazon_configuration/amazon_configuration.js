frappe.pages["amazon_configuration"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("亚马逊配置中心"),
		single_column: true,
	});
	wrapper.platformConfigurationCenter = new AmazonConfigurationCenter({
		page,
		wrapper,
		platform: "amazon",
		api: "fengjing_app.fengjing_business.page.amazon_configuration.amazon_configuration",
		route: "amazon_configuration",
		brand: "Amazon",
		brandMark: "a",
	});
};

frappe.pages["amazon_configuration"].on_page_show = function (wrapper) {
	const center = wrapper.platformConfigurationCenter;
	if (center?.loaded && !center.dirty) center.load(true);
};

frappe.pages["amazon_configuration"].on_page_hide = function (wrapper) {
	wrapper.platformConfigurationCenter?.exitFullscreen();
};

class AmazonConfigurationCenter {
	constructor(options) {
		Object.assign(this, options);
		this.activeSection = "stores";
		this.selectedStoreName = null;
		this.creatingStore = false;
		this.storeSearch = "";
		this.rankingProductSearch = "";
		this.controls = new Map();
		this.initialValues = new Map();
		this.controlMountId = 0;
		this.loaded = false;
		this.loading = false;
		this.dirty = false;
		this.mountingControls = false;
		this.data = null;
		this.root = $(`<div class="pc-root pc-${this.platform}"></div>`).appendTo(this.page.main);
		this.resizeEvent = `resize.pc-${this.platform}-configuration`;
		$(window).off(this.resizeEvent).on(this.resizeEvent, () => this.fitViewport());
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
		this.root.on("input", "[data-role='ranking-product-search']", (event) => {
			this.rankingProductSearch = event.currentTarget.value || "";
			this.renderRankingProductRows();
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
						<div class="pc-rail-head"><h3>${__("选择店铺")}</h3><button type="button" data-action="new-store" title="${__("新建店铺")}">${this.icon("plus")}</button></div>
					<label class="pc-store-search">${this.icon("search")}<input data-role="store-search" value="${this.escape(this.storeSearch)}" placeholder="${__("搜索店铺")}"></label>
					<div class="pc-store-list" data-role="store-list">${this.storeListHtml()}</div>
					<div class="pc-rail-foot"><span></span>${__("凭证仅在保存时传输")}</div>
				</aside>
					<nav class="pc-config-rail"><div class="pc-config-head"><h3>${__("配置菜单")}</h3><p>${__("选择要维护的业务配置")}</p></div><div class="pc-config-menu">${this.configMenuHtml()}</div></nav>
				<main class="pc-editor-pane" data-role="editor">${this.editorHtml()}</main>
			</div>
		`);
		this.mountEditorControls();
		window.requestAnimationFrame(() => this.fitViewport());
	}

	fitViewport() {
		if (!this.root?.length || window.innerWidth <= 900) return;
		const top = this.root[0].getBoundingClientRect().top;
		const height = Math.max(560, Math.floor(window.innerHeight - top - 6));
		this.root.css("--pc-viewport-height", `${height}px`);
	}

	storeListHtml() {
		const query = this.storeSearch.trim().toLowerCase();
		const stores = this.stores().filter((store) => !query || Object.values(store.values || {}).some((value) => String(value ?? "").toLowerCase().includes(query)));
		const createItem = this.creatingStore ? `<button type="button" class="pc-store-item active is-new" data-action="new-store"><span class="pc-store-avatar">＋</span><span><b>${__("新店铺")}</b><small>${__("尚未保存")}</small></span></button>` : "";
		if (!stores.length && !createItem) return `<div class="pc-store-empty"><p>${query ? __("没有匹配的店铺") : __("还没有店铺")}</p><button type="button" data-action="new-store">${__("新建店铺")}</button></div>`;
		return createItem + stores.map((store) => {
			const enabled = !this.storeSection.enabled_field || Number(store.values?.[this.storeSection.enabled_field]);
			const status = this.translateStatus(store.values?.[this.storeSection.status_field] || (enabled ? "Enabled" : "Disabled"));
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

	currentLedgerGroup(section = this.active) {
		if (!section || section.key !== "fba_ledger") return null;
		const document = this.currentDocument(section);
		const masterName = document?.values?.master_configuration;
		return (section.groups || []).find((group) => group.master_name === masterName)
			|| section.group
			|| null;
	}

	editorHtml() {
		const section = this.active;
		const store = this.currentStore();
		if (!section || (!store && !this.creatingStore)) {
			return `<div class="pc-empty-state"><div class="pc-empty-icon">${this.icon("stores")}</div><span>${__("配置工作台")}</span><h3>${__("请先选择一个店铺")}</h3><p>${__("从左侧选择现有店铺，或者新建店铺，然后在中间选择要维护的配置。")}</p><button type="button" class="pc-button pc-button-primary" data-action="new-store">${this.icon("plus")}${__("新建店铺")}</button></div>`;
		}
		if (section.key === "fba_ledger" && this.currentLedgerGroup(section)) return this.ledgerGroupEditorHtml(section);
		const document = this.currentDocument(section);
		const isNew = !document;
		const title = section.key === "stores" && isNew ? __("建立新店铺") : section.title;
		const context = section.key === "stores"
			? (isNew ? __("填写店铺身份与 API 连接信息") : __("店铺连接与凭证"))
			: `${this.storeLabel(store)} · ${section.title}`;
		return `
			<div class="pc-editor-head"><div class="pc-editor-title"><span class="pc-editor-icon">${this.icon(section.key)}</span><div><small>${this.escape(context)}</small><h3>${this.escape(title)}</h3><p>${this.escape(section.description || "")}</p></div></div><div class="pc-editor-status">${this.statusHtml(section, document)}</div></div>
			<div class="pc-editor-scroll">
				${isNew && section.key !== "stores" ? `<div class="pc-notice">${this.icon("info")}<div><b>${__("尚未建立这项配置")}</b><span>${__("下面已经关联当前店铺，填写后保存即可创建。")}</span></div></div>` : ""}
				${this.errorHtml(section, document)}
				<div class="pc-form-sections">${this.formSectionsHtml(section)}</div>
			</div>
			<footer class="pc-editor-footer"><div>${document && section.permissions.delete ? `<button type="button" class="pc-button pc-button-danger" data-action="delete">${this.icon("trash")}${__("删除此配置")}</button>` : ""}</div><div class="pc-save-actions"><button type="button" class="pc-button pc-button-secondary" data-action="reset">${__("撤销修改")}</button>${(isNew ? section.permissions.create : section.permissions.write) ? `<button type="button" class="pc-button pc-button-primary" data-action="save">${this.icon("save")}${isNew ? __("创建并保存") : __("保存配置")}</button>` : ""}</div></footer>`;
	}

	ledgerGroupEditorHtml(section) {
		const group = this.currentLedgerGroup(section) || {};
		const shared = group.shared || {};
		const mixed = new Set(group.mixed_fields || []);
		const sharedValue = (fieldname, fallback = "—") => mixed.has(fieldname)
			? `<span class="pc-ledger-mixed">${__("配置不一致")}</span>`
			: this.escape(shared[fieldname] ?? fallback);
		const overallTone = this.statusTone(group.status);
		const quotaNotice = group.quota_message
			? `<div class="pc-ledger-alert warning">${this.icon("warning")}<div><b>${__("亚马逊报告生成频率受限")}</b><span>${this.escape(group.quota_message)}</span></div></div>`
			: "";
		const mismatchNotice = mixed.size
			? `<div class="pc-ledger-alert warning">${this.icon("warning")}<div><b>${__("公共设置不一致")}</b><span>${__("运行前需要统一这些设置：{0}", [[...mixed].join("、")])}</span></div></div>`
			: "";
		const actions = (group.actions || []).map((action) => `<button type="button" data-action="run-ledger-group" data-task="${this.escape(action.key)}" class="pc-task-${action.style || "ghost"}" ${group.actions_ready ? "" : "disabled"} title="${this.escape(group.action_notice || "")}">${this.icon(this.actionIcon(action.key))}${this.escape(action.label)}</button>`).join("");
		return `
			<div class="pc-editor-head pc-ledger-editor-head"><div class="pc-editor-title"><span class="pc-editor-icon">${this.icon("fba_ledger")}</span><div><small>${this.escape(group.master_label || __("同一卖家 · 多国家站点"))}</small><h3>${this.escape(section.title)}</h3><p>${this.escape(section.description || "")}</p></div></div><div class="pc-editor-status"><span class="pc-status ${overallTone}"><i></i>${this.escape(this.translateStatus(group.status))}</span><span class="pc-runtime"><small>${__("已启用站点")}</small><b>${Number(group.enabled_countries || 0)}</b></span><span class="pc-runtime"><small>${__("API区域")}</small><b>${Number(group.region_count || 0)}</b></span><span class="pc-runtime"><small>${__("下次继续")}</small><b>${this.formatTime(group.next_retry_at)}</b></span></div></div>
			<div class="pc-editor-scroll pc-ledger-scroll">
				${quotaNotice}${mismatchNotice}
				<section class="pc-ledger-public-card">
					<header><div><span>${__("公共区域")}</span><h4>${__("统一抓取设置与操作")}</h4><p>${__("公共时间规则只显示一次，任务完成后仍按国家分别保存。")}</p></div><div class="pc-ledger-actions">${actions}</div></header>
					<div class="pc-ledger-shared-grid">
						<div><small>${__("历史范围")}</small><b>${sharedValue("history_start_date")} <em>→</em> ${sharedValue("history_end_date")}</b></div>
						<div><small>${__("日常运行间隔")}</small><b>${sharedValue("sync_interval_hours")} ${__("小时")}</b></div>
						<div><small>${__("日常回看范围")}</small><b>${sharedValue("routine_lookback_days")} ${__("天")}</b></div>
						<div><small>${__("汇总方式")}</small><b>${sharedValue("summary_time_aggregation")} · ${sharedValue("summary_location_aggregation")}</b></div>
						<div><small>${__("各站点汇总最新日期")}</small><b>${this.formatDate(group.summary_latest_date)}</b><span>${this.formatCount(group.summary_records)} ${__("条")}</span></div>
						<div><small>${__("各站点明细最新日期")}</small><b>${this.formatDate(group.detail_latest_date)}</b><span>${this.formatCount(group.detail_records)} ${__("条")}</span></div>
					</div>
					<div class="pc-ledger-action-note">${this.icon("info")}<span>${this.escape(group.action_notice || "")}</span></div>
				</section>
				<section class="pc-ledger-country-card">
					<header><div><span>${__("站点结果")}</span><h4>${this.escape((group.api_regions || []).join(" · ") || __("已关联国家站点"))}</h4><p>${__("每个站点继续保留独立店铺、成本中心、状态和保存数据。")}</p></div></header>
					<div class="pc-ledger-table-wrap"><table class="pc-ledger-table"><thead><tr><th>${__("国家与店铺")}</th><th>${__("运行状态")}</th><th>${__("汇总报告")}</th><th>${__("明细报告")}</th><th>${__("最后成功")}</th><th>${__("配置")}</th></tr></thead><tbody>${this.ledgerCountryRowsHtml(group.countries || [])}</tbody></table></div>
				</section>
			</div>
			<footer class="pc-editor-footer"><div><span class="pc-ledger-footer-note">${this.escape(group.action_notice || "")}</span></div><div class="pc-save-actions"><button type="button" class="pc-button pc-button-secondary" data-action="refresh">${this.icon("refresh")}${__("刷新状态")}</button></div></footer>`;
	}

	ledgerCountryRowsHtml(countries) {
		if (!countries.length) return `<tr><td colspan="6" class="pc-ranking-empty">${__("尚未建立FBA库存分类账配置")}</td></tr>`;
		const countryLabels = { "United States": __("美国"), Canada: __("加拿大"), Mexico: __("墨西哥"), Brazil: __("巴西") };
		return countries.map((row) => {
			const statusTone = this.statusTone(row.status, row.error);
			const summaryTone = this.statusTone(row.summary_status, row.error);
			const detailTone = this.statusTone(row.detail_status, row.error);
			const configUrl = `/app/amazon-fba-inventory-ledger-configuration/${encodeURIComponent(row.configuration_name || "")}`;
			return `<tr class="${Number(row.enabled) ? "" : "is-disabled"}">
				<td><div class="pc-ledger-country"><span>${this.initials(countryLabels[row.country] || row.country || row.store_name)}</span><div><b>${this.escape(countryLabels[row.country] || row.country || __("未知国家"))}</b><small>${this.escape(row.store_name || row.amazon_store || "—")}</small><em>${this.escape(row.marketplace_id || "—")}</em></div></div></td>
				<td><span class="pc-product-state ${statusTone}">${this.escape(Number(row.enabled) ? this.translateStatus(row.status) : __("已停用"))}</span><small>${Number(row.history_completed) ? __("历史已完成") : __("历史未完成")}</small>${row.error ? `<em class="pc-ledger-row-error" title="${this.escape(row.error)}">${this.escape(row.error)}</em>` : ""}</td>
				<td><span class="pc-product-state ${summaryTone}">${this.escape(this.translateStatus(row.summary_status))}</span><b>${this.formatDate(row.summary_latest_date)}</b><small>${this.formatCount(row.summary_records)} ${__("条")}</small></td>
				<td><span class="pc-product-state ${detailTone}">${this.escape(this.translateStatus(row.detail_status))}</span><b>${this.formatDate(row.detail_latest_date)}</b><small>${this.formatCount(row.detail_records)} ${__("条")}</small></td>
				<td><b>${this.formatTime(row.last_success_at)}</b><small>${this.escape(row.cost_center || "—")}</small></td>
				<td><a class="pc-ledger-config-link" href="${configUrl}">${this.icon("settings")}${__("打开配置")}</a></td>
			</tr>`;
		}).join("");
	}

	statusTone(status, error = "") {
		const value = String(status || "").toLowerCase();
		if (error || value === "failed" || value === "unavailable") return "danger";
		if (value === "running" || value === "waiting") return "running";
		if (value === "completed" || value === "success" || value === "available") return "success";
		return "neutral";
	}

	statusHtml(section, document) {
		if (!document) return `<span class="pc-status neutral"><i></i>${__("新配置")}</span>`;
		const values = document.values || {};
		const enabled = !section.enabled_field || Number(values[section.enabled_field]);
		const rawStatus = values[section.status_field] || (enabled ? "Enabled" : "Disabled");
		const status = this.translateStatus(rawStatus);
		const tone = this.isFailed(section, document) ? "danger" : /running|waiting|运行|等待/i.test(rawStatus) ? "running" : enabled ? "success" : "neutral";
		return `<span class="pc-status ${tone}"><i></i>${this.escape(status)}</span>${section.last_field ? `<span class="pc-runtime"><small>${__("最近执行")}</small><b>${this.formatTime(values[section.last_field])}</b></span>` : ""}${section.next_field ? `<span class="pc-runtime"><small>${__("下次执行")}</small><b>${this.formatTime(values[section.next_field])}</b></span>` : ""}`;
	}

	errorHtml(section, document) {
		if (!document) return "";
		const error = document.values?.[section.error_field] || document.values?.history_last_error;
		return error ? `<div class="pc-notice danger">${this.icon("warning")}<div><b>${__("最近一次运行出现异常")}</b><span>${this.escape(error)}</span></div></div>` : "";
	}

	fieldGroups(section) {
		const groups = [];
		let current = { key: "basic", title: __("基础设置"), fields: [] };
		(section.fields || []).forEach((field) => {
			if (field.fieldtype === "Section Break") {
				if (current.fields.length || this.actionsForGroup(section, current).length) groups.push(current);
				current = { key: field.fieldname || "section", title: this.translateSection(field.label || __("配置选项")), fields: [] };
				return;
			}
			if (field.fieldtype === "Column Break" || field.read_only) return;
			if (section.key === "stores" && this.currentStore() && field.fieldname === section.primary_field) return;
			current.fields.push(field);
		});
		if (current.fields.length || this.actionsForGroup(section, current).length) groups.push(current);
		return groups;
	}

	actionGroup(actionKey, sectionKey) {
		if (actionKey === "test") return "connection";
		if (actionKey === "history") return "history";
		if (actionKey === "discover") return "discovery";
		if (actionKey === "full") return "schedule";
		if (String(actionKey).startsWith("recheck_")) return actionKey;
		if (actionKey === "latest") {
			if (["orders", "finances"].includes(sectionKey)) return "incremental";
			if (sectionKey === "ranking" && this.platform === "ozon") return "automatic";
			return "schedule";
		}
		return "basic";
	}

	actionsForGroup(section, group) {
		if (!this.currentDocument(section)) return [];
		const groupKey = String(group.key || "").toLowerCase();
		if (section.key === "ranking" && this.platform === "amazon") {
			const targets = { discover: "discovery_schedule_section", latest: "schedule_section", full: "schedule_section" };
			return (section.actions || []).filter((action) => targets[action.key] === groupKey);
		}
		return (section.actions || []).filter((action) => groupKey.includes(this.actionGroup(action.key, section.key)));
	}

	actionButtonsHtml(actions) {
		if (!actions.length) return "";
		return `<div class="pc-section-actions">${actions.map((action) => `<button type="button" data-action="run" data-task="${action.key}" class="pc-task-${action.style || "ghost"}">${this.icon(this.actionIcon(action.key))}${this.escape(action.label)}</button>`).join("")}</div>`;
	}

	formSectionsHtml(section) {
		const groups = this.fieldGroups(section);
		if (!groups.length) return `<div class="pc-no-fields">${__("这项配置没有可编辑字段。")}</div>`;
		const mergedRankingKeys = new Set(["schedule_section", "discovery_schedule_section"]);
		const renderedGroups = section.key === "ranking" && this.platform === "amazon"
			? groups.filter((group) => !mergedRankingKeys.has(group.key))
			: groups;
		let html = renderedGroups.map((group, index) => {
			const actions = this.actionsForGroup(section, group);
			return `<section class="pc-form-section"><header><div class="pc-section-title"><span>${String(index + 1).padStart(2, "0")}</span><h4>${this.escape(group.title)}</h4></div>${this.actionButtonsHtml(actions)}</header>${group.fields.length ? `<div class="pc-form-grid">${group.fields.map((field) => `<div class="pc-form-slot ${this.isWideField(field) ? "wide" : ""}" data-fieldname="${this.escape(field.fieldname)}"></div>`).join("")}</div>` : ""}</section>`;
		}).join("");
		if (section.key === "ranking" && this.platform === "amazon" && this.currentDocument(section)) {
			html += this.rankingProductsSectionHtml(section, renderedGroups.length + 1);
		}
		return html;
	}

	rankingProductsSectionHtml(section, index) {
		const document = this.currentDocument(section);
		const values = document?.values || {};
		const products = document?.products || [];
		const enabled = products.filter((row) => Number(row.enabled) && !Number(row.deleted_from_store)).length;
		const failed = products.filter((row) => String(row.last_fetch_status || "").toLowerCase() === "failed").length;
		const metric = (label, value, tone = "") => `<div class="pc-ranking-metric ${tone}"><small>${this.escape(label)}</small><b>${this.escape(value || "—")}</b></div>`;
		return `<section class="pc-form-section pc-ranking-products"><header><div class="pc-section-title"><span>${String(index).padStart(2, "0")}</span><div><h4>${__("排名商品与同步")}</h4><small>${__("三个操作按钮共同使用下方已启用的商品清单")}</small></div></div>${this.actionButtonsHtml(section.actions || [])}</header>
			<div class="pc-ranking-summary">
				${metric(__("商品总数"), products.length)}
				${metric(__("启用商品"), enabled, "success")}
				${metric(__("抓取失败"), failed, failed ? "danger" : "success")}
				${metric(__("上次发现商品"), this.formatTime(values.last_discovery_at))}
				${metric(__("上次抓取排名"), this.formatTime(values.last_fetch_at))}
				${metric(__("下次自动抓取"), this.formatTime(values.next_fetch_at))}
			</div>
			<div class="pc-ranking-results">
				<div><small>${__("商品发现结果")}</small><b>${this.escape(values.last_discovery_result || __("尚无记录"))}</b>${values.last_discovery_error ? `<em>${this.escape(values.last_discovery_error)}</em>` : ""}</div>
				<div><small>${__("排名抓取结果")}</small><b>${this.escape(values.last_fetch_result || __("尚无记录"))}</b></div>
			</div>
			<div class="pc-ranking-toolbar"><label>${this.icon("search")}<input data-role="ranking-product-search" value="${this.escape(this.rankingProductSearch)}" placeholder="${__("搜索 ASIN、SKU、商品名称或对应物料")}"></label><span data-role="ranking-product-count">${products.length} ${__("个商品")}</span></div>
			<div class="pc-ranking-table-wrap"><table class="pc-ranking-table"><thead><tr><th>${__("商品")}</th><th>ASIN / SKU</th><th>${__("状态")}</th><th>${__("对应物料")}</th><th>${__("排名抓取")}</th><th>${__("最近错误")}</th></tr></thead><tbody data-role="ranking-product-list">${this.rankingProductRowsHtml()}</tbody></table></div>
		</section>`;
	}

	rankingProductRowsHtml() {
		const document = this.activeSection === "ranking" ? this.currentDocument(this.active) : null;
		const query = this.rankingProductSearch.trim().toLowerCase();
		const products = (document?.products || []).filter((row) => !query || [row.asin, row.sku, row.product_title, row.corresponding_item, row.listing_status].some((value) => String(value || "").toLowerCase().includes(query)));
		if (!products.length) return `<tr><td colspan="6" class="pc-ranking-empty">${query ? __("没有匹配的商品") : __("尚未发现商品，请先点击“发现商品”或“完整同步”")}</td></tr>`;
		return products.map((row) => {
			const image = this.safeImageUrl(row.amazon_image_url);
			const enabled = Number(row.enabled) && !Number(row.deleted_from_store);
			const fetchStatus = this.translateStatus(row.last_fetch_status || "Not Fetched");
			const fetchTone = String(row.last_fetch_status || "").toLowerCase() === "failed" ? "danger" : String(row.last_fetch_status || "").toLowerCase() === "success" ? "success" : "neutral";
			const productUrl = `/app/amazon-ranking-product/${encodeURIComponent(row.name || "")}`;
			return `<tr>
				<td><div class="pc-ranking-product">${image ? `<img src="${image}" alt="">` : `<span>${this.icon("ranking")}</span>`}<div><a href="${productUrl}">${this.escape(row.product_title || row.asin || __("未命名商品"))}</a><small>${this.escape(row.source || "")}${Number(row.is_competitor) ? ` · ${__("竞品")}` : ""}</small></div></div></td>
				<td><a class="pc-ranking-asin" href="${productUrl}">${this.escape(row.asin || "—")}</a><small>${this.escape(row.sku || "—")}</small></td>
				<td><span class="pc-product-state ${enabled ? "success" : "neutral"}">${enabled ? __("已启用") : __("已停用")}</span><small>${this.escape(row.listing_status || (Number(row.deleted_from_store) ? __("店铺已删除") : "—"))}</small></td>
				<td>${row.corresponding_item ? `<a href="/app/item/${encodeURIComponent(row.corresponding_item)}">${this.escape(row.corresponding_item)}</a>` : "—"}</td>
				<td><span class="pc-product-state ${fetchTone}">${this.escape(fetchStatus)}</span><small>${this.formatTime(row.last_fetch_at)}<br>${__("下次")}：${this.formatTime(row.next_fetch_at)}</small></td>
				<td class="pc-ranking-error">${this.escape(row.last_error || "—")}</td>
			</tr>`;
		}).join("");
	}

	renderRankingProductRows() {
		const rows = this.root.find("[data-role='ranking-product-list']");
		if (!rows.length) return;
		rows.html(this.rankingProductRowsHtml());
		const count = (this.currentDocument(this.active)?.products || []).filter((row) => !this.rankingProductSearch.trim() || [row.asin, row.sku, row.product_title, row.corresponding_item, row.listing_status].some((value) => String(value || "").toLowerCase().includes(this.rankingProductSearch.trim().toLowerCase()))).length;
		this.root.find("[data-role='ranking-product-count']").text(`${count} ${__("个商品")}`);
	}

	mountEditorControls() {
		this.controls.clear();
		this.initialValues.clear();
		const mountId = ++this.controlMountId;
		const pendingValues = [];
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
			const value = document ? document.values?.[field.fieldname] : (prefill[field.fieldname] ?? field.default ?? null);
			if (field.fieldtype === "Check") {
				this.controls.set(field.fieldname, this.makeCheckControl(parent, df, value));
				return;
			}
			const control = frappe.ui.form.make_control({ parent, df, render_input: true });
			pendingValues.push(Promise.resolve(control.set_value(value)));
			if (isStoredSecret && control.$input) control.$input.attr("placeholder", __("已保存，留空保持不变"));
			control.$wrapper.off(".pc-editor").on("input.pc-editor change.pc-editor", ":input", () => this.markDirty());
			this.controls.set(field.fieldname, control);
		});
		Promise.all(pendingValues).then(() => {
			if (mountId !== this.controlMountId) return;
			this.initialValues = new Map([...this.controls].map(([fieldname, control]) => [fieldname, this.controlValue(control)]));
			this.mountingControls = false;
			this.setDirtyState(false);
		});
	}

	makeCheckControl(parent, df, value) {
		const checked = this.isChecked(value);
		parent.html(`<label class="pc-check-control"><input type="checkbox" ${checked ? "checked" : ""}><span class="pc-check-box">${this.icon("check")}</span><span class="pc-check-copy"><b>${this.escape(df.label || df.fieldname)}</b>${df.description ? `<small>${this.escape(df.description)}</small>` : ""}</span></label>`);
		const $input = parent.find("input[type='checkbox']");
		$input.on("change.pc-editor", () => this.markDirty());
		return {
			df,
			$input,
			$wrapper: parent,
			get_value: () => ($input.prop("checked") ? 1 : 0),
			set_value: (nextValue) => $input.prop("checked", this.isChecked(nextValue)),
		};
	}

	markDirty() {
		if (this.mountingControls) return;
		const dirty = [...this.controls].some(([fieldname, control]) => this.controlValue(control) !== this.initialValues.get(fieldname));
		this.setDirtyState(dirty);
	}

	controlValue(control) {
		const value = control.get_value();
		if (control.df.fieldtype === "Check") return this.isChecked(value) ? "1" : "0";
		return value === null || value === undefined ? "" : String(value);
	}

	setDirtyState(dirty) {
		this.dirty = dirty;
		const state = this.root.find("[data-role='save-state']");
		state.toggleClass("dirty", dirty).html(`<i></i>${dirty ? __("有未保存修改") : __("配置已同步")}`);
		this.root.find("[data-action='save']").toggleClass("is-dirty", dirty);
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
		if (action === "select-store") return this.navigate(() => { this.creatingStore = false; this.selectedStoreName = button.dataset.name; this.rankingProductSearch = ""; this.render(); });
		if (action === "select-section") return this.navigate(() => { this.activeSection = button.dataset.section; this.rankingProductSearch = ""; this.render(); });
		if (action === "new-store") return this.startNewStore();
		if (action === "refresh") return this.refreshWithGuard();
		if (action === "fullscreen") return this.toggleFullscreen();
		if (action === "reset") return this.navigate(() => { this.dirty = false; this.render(); });
		if (action === "save") return this.saveCurrent();
		if (action === "delete") return this.deleteCurrent();
		if (action === "run") return this.runTask(button.dataset.task, button);
		if (action === "run-ledger-group") return this.runLedgerGroupTask(button.dataset.task, button);
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

	runLedgerGroupTask(taskKey, button) {
		const section = this.active;
		const group = this.currentLedgerGroup(section);
		const task = group?.actions?.find((item) => item.key === taskKey);
		if (!task || !group?.actions_ready || !group?.master_name) return;
		const execute = async () => {
			button.disabled = true;
			button.classList.add("working");
			try {
				const result = await this.call(
					"run_group_action",
					{ section_key: section.key, action_key: taskKey, master_name: group.master_name },
					true,
					__("正在提交公共任务…")
				);
				frappe.show_alert({ message: result?.message || __("公共任务已提交"), indicator: "green" }, 8);
				window.setTimeout(() => { if (!this.dirty) this.load(true); }, 900);
			} finally {
				button.disabled = false;
				button.classList.remove("working");
			}
		};
		if (task.confirm) {
			frappe.confirm(
				__("确定执行“{0}”吗？同一 API 区域的国家站点将共用一组 Amazon 报告请求。", [task.label]),
				execute
			);
		} else execute();
	}

	toggleFullscreen() {
		this.root.toggleClass("pc-fullscreen");
		const fullscreen = this.root.hasClass("pc-fullscreen");
		$("body").toggleClass("pc-fullscreen-open", fullscreen);
		if (!fullscreen) window.requestAnimationFrame(() => this.fitViewport());
	}
	exitFullscreen() {
		this.root.removeClass("pc-fullscreen");
		$("body").removeClass("pc-fullscreen-open");
		window.requestAnimationFrame(() => this.fitViewport());
	}
	storeLabel(store) { return store?.values?.[this.storeSection?.primary_field] || store?.name || __("未命名店铺"); }

	isFailed(section, document) {
		const values = document?.values || {};
		const status = String(values[section.status_field] || "").toLowerCase();
		return Boolean(values[section.error_field] || values.history_last_error || ["failed", "unavailable"].includes(status));
	}

	isWideField(field) { return ["Long Text", "Small Text", "Text"].includes(field.fieldtype); }
	isChecked(value) { return value === true || ["1", "true", "yes"].includes(String(value ?? "").toLowerCase()); }

	translateStatus(value) {
		const status = String(value || "").trim();
		const labels = {
			"Not Tested": __("未测试"), Available: __("连接正常"), Unavailable: __("连接不可用"),
			Enabled: __("已启用"), Disabled: __("已停用"), Idle: __("空闲"),
			"Not Started": __("尚未开始"), Running: __("运行中"), Waiting: __("等待中"),
			Completed: __("已完成"), Failed: __("失败"), Success: __("成功"),
		};
		return labels[status] || status;
	}

	actionIcon(action) {
		if (String(action).includes("recheck_")) return "recheck";
		if (String(action).includes("history")) return "history";
		return action;
	}

	translateSection(label) {
		const labels = {
			"Store Identity": __("店铺身份"), "Amazon Marketplace": __("亚马逊站点"), "API Credentials": __("API 凭证"), "Connection Status": __("连接设置"),
			"Basic Configuration": __("基础配置"), "Schedule and Result": __("调度设置"), "Product Discovery Result": __("商品发现"), "Runtime Status": __("运行设置"),
			"Historical Order Sync": __("历史订单同步"), "Incremental Order Sync": __("增量订单同步"), "Historical Financial Sync": __("历史财务同步"), "Incremental Financial Sync": __("增量财务同步"), "Manual Sync Actions": __("手动同步"),
			"Historical Ranking Sync": __("历史排名同步"), "Automatic Ranking Sync": __("自动排名同步"), "Historical Settlement Statement Sync": __("历史结算报告同步"),
			"Snapshot Schedule": __("库存快照计划"), "Inventory Ledger History": __("库存分类账历史"), "Routine Ledger Recheck": __("分类账日常复核"),
			"Ledger Summary Report": __("库存分类账汇总报告"), "Ledger Detail Report": __("库存分类账明细报告"),
			"Recent 7 Days Recheck": __("近7天核对"), "Recent 14 Days Recheck": __("近14天核对"), "Recent 30 Days Recheck": __("近30天核对"), "Recent 90 Days Recheck": __("近90天核对"), "Recent 180 Days Recheck": __("近180天核对"),
		};
		return labels[label] || label;
	}

	formatTime(value) {
		if (!value) return "—";
		try { return frappe.datetime.str_to_user(String(value).split(".")[0]); } catch (error) { return this.escape(value); }
	}
	formatDate(value) {
		if (!value) return "—";
		return this.escape(String(value).slice(0, 10));
	}
	formatCount(value) {
		return Number(value || 0).toLocaleString("zh-CN");
	}
	safeImageUrl(value) {
		if (!value) return "";
		try {
			const url = new URL(String(value), window.location.origin);
			return ["http:", "https:"].includes(url.protocol) ? this.escape(url.href) : "";
		} catch (error) { return ""; }
	}

	initials(value) { const text = String(value || this.brand || "F").replace(/[^A-Za-z0-9\u4e00-\u9fa5]/g, ""); return this.escape(text.slice(0, 2).toUpperCase() || (this.platform === "ozon" ? "OZ" : "AM")); }
	selectorEscape(value) { return String(value || "").replace(/["\\]/g, "\\$&"); }
	escape(value) { return frappe.utils.escape_html(String(value ?? "")); }

	icon(name) {
		const icons = {
			stores: "shop-window", ranking: "bar-chart-line", orders: "box-seam", finances: "wallet2", fba_inventory: "boxes", fba_ledger: "journal-text", awd_inventory: "building",
			prices: "tags", settlements: "receipt",
			refresh: "arrow-clockwise", expand: "arrows-fullscreen", plus: "plus-lg", search: "search", chevron: "chevron-right",
			info: "info-circle", warning: "exclamation-triangle", trash: "trash3", save: "check2-circle", check: "check-lg", settings: "gear",
			test: "plug", latest: "arrow-repeat", history: "clock-history", discover: "search", full: "cloud-download", recheck: "calendar-check",
		};
		return `<i class="bi bi-${icons[name] || icons.info}" aria-hidden="true"></i>`;
	}
}
