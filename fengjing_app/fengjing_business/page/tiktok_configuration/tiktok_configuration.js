frappe.pages.tiktok_configuration.on_page_load = (wrapper) => {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "TikTok 配置中心",
		single_column: true,
	});
	wrapper.tiktokConfiguration = new TikTokConfigurationCenter(page);
};

frappe.pages.tiktok_configuration.on_page_show = (wrapper) => {
	const center = wrapper.tiktokConfiguration;
	if (center?.loaded && !center.dirty && !center.working) center.load();
};

class TikTokConfigurationCenter {
	constructor(page) {
		this.page = page;
		this.api = "fengjing_app.fengjing_business.doctype.tiktok_order_configuration.tiktok_sync";
		this.section = "store";
		this.search = "";
		this.icons = {
			store: "shop", orders: "bag-check", aftersales: "arrow-return-left",
			statements: "receipt", statement_transactions: "list-ul",
			payouts: "bank", shop_performance: "bar-chart",
		};
		this.root = $('<div class="tt-config"></div>').appendTo(page.main);
		const resize = new ResizeObserver(() => this.fitViewport());
		resize.observe(document.documentElement);
		this.root.on("click", "[data-action]", (event) => this.handle(event.currentTarget));
		this.root.on("submit", "form", (event) => {
			event.preventDefault();
			this.save(event.currentTarget);
		});
		this.root.on("input", "[data-search]", (event) => {
			this.search = event.currentTarget.value;
			this.root.find("[data-stores]").html(this.storesHtml());
		});
		this.root.on("input change", "[data-editor] input", () => {
			this.dirty = true;
			this.root.find('[data-action="reset"]').prop("disabled", false);
		});
		this.page.set_primary_action("刷新配置", () => this.confirmDiscard(() => this.load()), "refresh");
		this.page.add_inner_button("新建店铺", () => this.confirmDiscard(() => frappe.new_doc("TikTok Order Configuration")));
		this.root.html('<div class="tt-empty" role="status">正在读取 TikTok 配置…</div>');
		this.load();
	}

	get task() {
		return this.data?.tasks.find((task) => task.kind === this.section);
	}

	get store() {
		return this.data?.store;
	}

	escape(value) {
		return frappe.utils.escape_html(String(value ?? ""));
	}

	icon(key) {
		return `<i class="bi bi-${key}" aria-hidden="true"></i>`;
	}

	time(value) {
		return value ? frappe.datetime.str_to_user(value) : "—";
	}

	button(label, action, icon, tone = "", disabled = false) {
		return `<button type="button" class="tt-button ${tone}" data-action="${action}" ${disabled ? "disabled" : ""}>
			${icon ? this.icon(icon) : ""}<span>${label}</span>
		</button>`;
	}

	confirmDiscard(action) {
		if (this.loading || this.working) return;
		if (!this.dirty) return action();
		frappe.confirm("配置有未保存的修改，是否放弃修改？", () => {
			this.dirty = false;
			this.render();
			action();
		});
	}

	async load(store = this.store?.name) {
		if (this.loading) return;
		this.loading = true;
		this.root.attr("aria-busy", "true");
		try {
			const response = await frappe.call({
				method: this.api + ".get_overview", type: "GET",
				args: store ? { tiktok_store: store } : {},
			});
			this.data = response.message;
			if (!this.store) this.section = "store";
			this.loaded = true;
			this.dirty = false;
			this.render();
		} catch {
			if (!this.loaded) {
				this.root.html(`<div class="tt-empty" role="status"><h3>暂时无法读取 TikTok 配置</h3>${this.button("重新加载", "refresh", "arrow-clockwise")}</div>`);
			}
		} finally {
			this.loading = false;
			this.root.attr("aria-busy", "false");
		}
	}

	render() {
		const selection = this.store
			? `${this.escape(this.store.store_name || this.store.name)} · ${this.task ? this.escape(this.task.label) : "店铺授权"}`
			: "TikTok Shop";
		this.root.html(`
			<header class="tt-topbar">
				<div class="tt-brand"><span class="tt-brand-icon">${this.icon("tiktok")}</span>
					<div><h2>TikTok 配置中心</h2><p>${selection}</p></div>
				</div>
				${this.button("刷新", "refresh", "arrow-clockwise")}
			</header>
			<div class="tt-console">
				<aside class="tt-rail tt-stores">
					<header><h3>选择店铺</h3><button type="button" class="tt-add" data-action="new-store" title="新建店铺" aria-label="新建店铺">${this.icon("plus-lg")}</button></header>
					<label class="tt-search">${this.icon("search")}<input type="search" data-search aria-label="搜索店铺" placeholder="搜索店铺" value="${this.escape(this.search)}"></label>
					<div class="tt-store-list" data-stores>${this.storesHtml()}</div>
				</aside>
				<nav class="tt-rail tt-menu" aria-label="配置菜单">
					<header><h3>配置菜单</h3></header>
					<div class="tt-menu-list">${this.menuHtml()}</div>
				</nav>
				<section class="tt-editor" data-editor aria-label="当前配置">${this.editorHtml()}</section>
			</div>`);
		this.fitViewport();
	}

	fitViewport() {
		const console = this.root.find(".tt-console")[0];
		if (console?.getClientRects().length) {
			const height = Math.floor(window.innerHeight - console.getBoundingClientRect().top - 12);
			console.style.setProperty("--tt-console-height", `${height}px`);
		}
	}

	storesHtml() {
		const query = this.search.trim().toLowerCase();
		const stores = this.data.stores.filter((store) =>
			[store.store_name, store.name, store.shop_id, store.country].some((value) => String(value || "").toLowerCase().includes(query))
		);
		if (!stores.length) return `<div class="tt-rail-empty">${query ? "没有匹配的店铺" : "还没有店铺"}</div>`;
		return stores.map((store) => {
			const selected = store.name === this.store?.name;
			const label = this.escape(store.store_name || store.name);
			return `<button type="button" class="tt-nav-item ${selected ? "is-active" : ""}" data-action="select-store" data-value="${this.escape(store.name)}" title="${label}" aria-pressed="${selected}">
				<span class="tt-nav-icon">${this.icon("shop")}</span>
				<span class="tt-nav-label"><strong>${label}</strong><small><i class="tt-dot ${store.authorized_at ? "is-green" : ""}"></i>${store.authorized_at ? "已授权" : "待授权"}</small></span>
				${this.icon("chevron-right")}
			</button>`;
		}).join("");
	}

	menuHtml() {
		const sections = [{ kind: "store", label: "店铺授权" }, ...this.data.tasks];
		return sections.map(({ kind, label, settings }) => {
			const active = this.section === kind;
			const text = kind === "store" ? "连接与店铺信息" : !settings ? "未配置" :
				Number(settings.enabled) ? "定时同步已启用" : "定时同步已关闭";
			const state = settings?.task_status === "失败" ? "is-red" :
				Number(settings?.enabled) ? "is-green" : "";
			return `<button type="button" class="tt-nav-item ${active ? "is-active" : ""}" data-action="select-section" data-value="${kind}" aria-pressed="${active}" ${kind !== "store" && !this.store ? "disabled" : ""}>
				<span class="tt-nav-icon">${this.icon(this.icons[kind])}</span>
				<span class="tt-nav-label"><strong>${this.escape(label)}</strong><small>${text}</small></span>
				<i class="tt-dot ${state}" aria-hidden="true"></i>
			</button>`;
		}).join("");
	}

	editorHead(label, detail, state, tone) {
		return `<header class="tt-editor-head">
			<div class="tt-editor-title"><span class="tt-nav-icon">${this.icon(this.icons[this.section])}</span><div><h3>${label}</h3><p>${detail}</p></div></div>
			<span class="tt-status ${tone || ""}"><i class="tt-dot"></i>${this.escape(state)}</span>
		</header>`;
	}

	metrics(items) {
		return `<dl class="tt-metrics">${items.map(([label, value]) =>
			`<div><dt>${label}</dt><dd>${this.escape(value || "—")}</dd></div>`
		).join("")}</dl>`;
	}

	editorHtml() {
		if (!this.store) return `<div class="tt-empty"><span class="tt-nav-icon">${this.icon("shop")}</span><h3>添加 TikTok 店铺</h3><p>完成授权后即可配置各项数据同步。</p>${this.button("新建店铺", "new-store", "plus-lg", "tt-primary")}</div>`;
		if (!this.task) return this.storeHtml();
		return this.settingsHtml(this.task);
	}

	storeHtml() {
		const store = this.store;
		const name = this.escape(store.store_name || store.name);
		return `
			${this.editorHead("店铺授权", name, store.authorized_at ? "已授权" : "待授权", store.authorized_at ? "tt-success" : "tt-warning")}
			<div class="tt-scroll">
				<section class="tt-card">
					<header><h4>店铺信息</h4>${this.button("测试连接", "test-authorization", "plug", "tt-soft")}</header>
					${this.metrics([
						["店铺 ID", store.shop_id], ["站点国家", store.country],
						["店铺时区", store.shop_timezone || "America/Los_Angeles"], ["授权时间", this.time(store.authorized_at)],
					])}
				</section>
			</div>
			<footer class="tt-footer">
				${this.button("全部店铺", "store-list", "list-ul")}
				${this.button("打开店铺授权", "open-store", "box-arrow-up-right", "tt-primary")}
			</footer>`;
	}

	historyNote(limit) {
		if (!limit) return "";
		const range = limit.earliest
			? `官方最早支持 ${this.escape(limit.earliest)}${limit.days == null ? "" : `，距今 ${limit.days} 天`}。`
			: "";
		const latest = limit.latest ? `补抓结束日期最晚选 ${this.escape(limit.latest)}（店铺当地日期）。` : "";
		const verified = limit.verified_earliest ? `最近实测最早可查 ${this.escape(limit.verified_earliest)}。` : "";
		return `<p class="tt-field-note">${range}${verified}${this.escape(limit.note)}${latest}</p>`;
	}

	rechecksHtml(task, values, busy) {
		const days = this.data.recheck_days || [];
		if (!days.length) return "";
		return `<section class="tt-card">
			<header><h4>定期核对</h4></header>
			<p class="tt-field-note">按店铺时区核对截至昨天的数据。启用定时同步后，勾选的周期会自动运行。</p>
			<div class="tt-rechecks">${days.map((span) => {
				const enabled = Number(values[`enable_recheck_${span}`]);
				const last = values[`recheck_${span}_last_at`];
				const next = values[`recheck_${span}_next_at`];
				return `<div class="tt-recheck-row">
					<label class="tt-recheck-check"><input name="enable_recheck_${span}" type="checkbox" ${enabled ? "checked" : ""}><span>${span} 天自动核对</span></label>
					<label class="tt-recheck-interval">每 <input aria-label="${span} 天核对的间隔" name="recheck_${span}_interval_days" type="number" required min="1" step="1" value="${this.escape(values[`recheck_${span}_interval_days`])}"> 天</label>
					<span class="tt-recheck-last">上次 ${this.escape(this.time(last))}${enabled && Number(values.enabled) ? `<br>下次 ${this.escape(this.time(next))}` : ""}</span>
					${this.button(`核对 ${span} 天`, `recheck_${span}`, "calendar-check", "", busy || !task.settings)}
				</div>`;
			}).join("")}</div>
		</section>`;
	}

	settingsHtml(task) {
		const settings = task.settings;
		const values = settings || task.defaults || {};
		const name = this.escape(this.store.store_name || this.store.name);
		const tone = { 成功: "tt-success", 失败: "tt-danger", 运行中: "tt-warning", 排队中: "tt-warning", 待续跑: "tt-warning" }[settings?.task_status];
		const state = settings ? (settings.task_status || "尚未运行") : "未配置";
		const busy = ["排队中", "运行中", "待续跑"].includes(settings?.task_status);
		const runningHistory = settings?.history_cursor && settings.history_status !== "完成";
		const runningRecheck = settings?.task_mode?.startsWith("核对") && settings?.recheck_cursor &&
			(settings.task_status === "失败" || busy);
		const historyMin = this.escape(task.history_limit?.earliest);
		const historyMax = this.escape(task.history_limit?.latest);
		return `
			${this.editorHead(this.escape(task.label) + "同步配置", name, state, tone)}
			<form class="tt-editor-form">
				<div class="tt-scroll">
					${settings?.last_error ? `<div class="tt-alert"><i class="bi bi-exclamation-circle" aria-hidden="true"></i><span>${this.escape(settings.last_error)}</span></div>` : ""}
					<section class="tt-card">
						<header><h4>日常同步</h4>${settings ? this.button("增量同步", "incremental", "arrow-repeat", "tt-soft", busy) : ""}</header>
						<div class="tt-fields">
							<label class="tt-check"><input name="enabled" type="checkbox" ${Number(values.enabled) ? "checked" : ""}> 启用定时同步</label>
							<label>同步间隔（分钟）<input name="interval_minutes" type="number" required min="1" step="1" value="${this.escape(values.interval_minutes)}"></label>
							<label>增量回看天数<input name="lookback_days" type="number" required min="1" step="1" value="${this.escape(values.lookback_days)}"></label>
						</div>
					</section>
					<section class="tt-card">
						<header><h4>历史补抓</h4>${settings ? this.button("历史补抓", "history", "clock-history", "tt-soft", busy) : ""}</header>
						${this.historyNote(task.history_limit)}
						<div class="tt-fields">
							<label>开始日期<input name="history_start_date" type="date" min="${historyMin}" max="${historyMax}" value="${this.escape(values.history_start_date)}"></label>
							<label>结束日期<input name="history_end_date" type="date" min="${historyMin}" max="${historyMax}" value="${this.escape(values.history_end_date)}"></label>
						</div>
					</section>
					${this.rechecksHtml(task, values, busy)}
					${settings ? `<section class="tt-card">
						<header><h4>运行状态</h4>${settings.task_status === "失败" ? this.button("重试", "retry", "arrow-clockwise", "tt-soft") : ""}</header>
						${this.metrics([
							["最近成功", this.time(settings.last_success_at)],
							["下次定时同步", Number(settings.enabled) ? this.time(settings.next_sync_at) : "已关闭"],
							["历史补抓状态", settings.history_status || "尚未开始"],
							...(runningRecheck ? [[settings.task_mode + "剩余范围", settings.recheck_cursor + " 至 " + settings.recheck_until]] : []),
							...(runningHistory ? [["剩余日期", settings.history_cursor + " 至 " + settings.history_until]] : []),
						])}
					</section>` : ""}
				</div>
				<footer class="tt-footer">
					<div class="tt-actions">${this.button("查看数据", "records", "table")}${settings ? this.button("打开配置表", "open-config", "box-arrow-up-right") : ""}</div>
					<div class="tt-actions">${this.button("撤销修改", "reset", "", "", true)}<button type="submit" class="tt-button tt-primary">${this.icon("check2")}<span>${settings ? "保存配置" : "创建配置"}</span></button></div>
				</footer>
			</form>`;
	}

	async handle(button) {
		const { action, value } = button.dataset;
		if (action === "reset") {
			this.dirty = false;
			this.root.find("[data-editor]").html(this.editorHtml());
			return;
		}
		if (action === "test-authorization" && !this.loading && !this.working) {
			return this.runAction(button, async () => {
				const result = await frappe.xcall(
					"fengjing_app.fengjing_business.doctype.tiktok_order_configuration.tiktok_order_configuration.test_authorization",
					{ configuration: this.store.name },
				);
				frappe.show_alert({ message: result.message, indicator: "green" });
			});
		}
		if (action.startsWith("recheck_")) {
			if (this.loading || this.working) return;
			if (this.dirty) return frappe.msgprint("请先保存配置。");
			return frappe.confirm(
				`重新核对最近 ${action.slice(8)} 个完整店铺日的数据？`,
				() => this.start(button, action),
			);
		}
		if (["incremental", "history", "retry"].includes(action)) return this.start(button, action);
		this.confirmDiscard(() => {
			if (action === "refresh") return this.load();
			if (action === "select-store") return this.load(value);
			if (action === "select-section") {
				this.dirty = false;
				this.section = value;
				this.render();
				this.root.find(`[data-action="select-section"][data-value="${value}"]`).trigger("focus");
			}
			if (action === "new-store") return frappe.new_doc("TikTok Order Configuration");
			if (action === "open-store") return frappe.set_route("Form", "TikTok Order Configuration", this.store.name);
			if (action === "open-config") return frappe.set_route("Form", this.task.configuration, this.task.settings.name);
			if (action === "store-list") return frappe.set_route("List", "TikTok Order Configuration");
			if (action === "records") {
				frappe.route_options = { tiktok_store: this.store.name };
				return frappe.set_route("List", this.task.storage);
			}
		});
	}

	async save(form) {
		if (this.loading || this.working) return;
		const fields = new FormData(form);
		const values = Object.fromEntries(fields);
		values.enabled = Number(fields.has("enabled"));
		for (const days of this.data.recheck_days || []) {
			const field = `enable_recheck_${days}`;
			values[field] = Number(fields.has(field));
		}
		for (const field of ["history_start_date", "history_end_date"]) values[field] ||= null;
		const task = this.task;
		await this.runAction(form.querySelector('[type="submit"]'), async () => {
			await frappe.xcall(this.api + ".save_configuration", {
				kind: task.kind, tiktok_store: this.store.name, values, modified: task.settings?.modified,
			});
			frappe.show_alert({ message: "配置已保存", indicator: "green" });
			this.dirty = false;
		});
	}

	async start(button, action) {
		if (this.loading || this.working) return;
		if (this.dirty) return frappe.msgprint("请先保存配置。");
		const task = this.task;
		await this.runAction(button, async () => {
			await frappe.xcall(this.api + ".request_sync", {
				kind: task.kind, configuration: task.settings.name, action,
			});
			frappe.show_alert({ message: "同步任务已加入队列", indicator: "green" });
		});
	}

	async runAction(button, action) {
		this.working = true;
		const editor = this.root.find("[data-editor]")[0];
		editor.inert = true;
		button.disabled = true;
		try {
			await action();
			await this.load();
		} catch {
			// Frappe displays the server validation message.
		} finally {
			this.working = false;
			editor.inert = false;
			button.disabled = false;
		}
	}
}
