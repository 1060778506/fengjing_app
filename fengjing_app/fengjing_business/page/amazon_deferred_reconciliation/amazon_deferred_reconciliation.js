frappe.pages["amazon-deferred-reconciliation"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("亚马逊递延交易对账"),
		single_column: true,
	});
	wrapper.amazonDeferredReconciliation = new AmazonDeferredReconciliationPage(page);
};

frappe.pages["amazon-deferred-reconciliation"].on_page_show = function (wrapper) {
	wrapper.amazonDeferredReconciliation?.render();
};

class AmazonDeferredReconciliationPage {
	constructor(page) {
		this.page = page;
		this.snapshots = [];
		this.selected = null;
		this.activeTab = "transactions";
		this.search = "";
		this.renderShell();
		this.bindEvents();
		this.render();
	}

	renderShell() {
		$(this.page.main).html(`
			<div class="adr-root">
				<header class="adr-header">
					<div><h1>${__("亚马逊递延交易对账")}</h1><p>${__("将余额、递延交易和订单放在同一份快照中核对。金额按站点和币种分别显示。")}</p></div>
					<button class="btn btn-primary adr-capture" disabled title="${__("抓取和保存逻辑尚未接入")}">${__("抓取并生成快照")}</button>
				</header>
				<div class="adr-layout">
					<aside class="adr-sidebar">
						<div class="adr-sidebar-head"><h2>${__("数据快照")}</h2><span class="adr-snapshot-count">0</span></div>
						<input class="form-control adr-search" type="search" placeholder="${__("搜索快照")}" aria-label="${__("搜索快照")}">
						<div class="adr-snapshot-list"></div>
					</aside>
					<main class="adr-main">
						<section class="adr-context">
							<div><span class="adr-eyebrow">${__("当前快照")}</span><h2 class="adr-title">${__("尚无快照")}</h2><p class="adr-subtitle">${__("抓取功能接入后，可在左侧打开任意一次完整快照。")}</p></div>
							<div class="adr-status"><span class="adr-status-dot"></span><span class="adr-status-text">${__("等待接入")}</span></div>
						</section>
						<section class="adr-source-times">
							<div><span>${__("余额抓取")}</span><strong data-time="balance">—</strong></div>
							<div><span>${__("递延交易抓取")}</span><strong data-time="transactions">—</strong></div>
							<div><span>${__("订单抓取")}</span><strong data-time="orders">—</strong></div>
						</section>
						<div class="adr-currency-cards"></div>
						<section class="adr-panel adr-reconcile-panel">
							<div class="adr-panel-heading"><h3>${__("站点与币种核对")}</h3><span>${__("不同币种不合计；差额仅供核查，不自动判定错误来源。")}</span></div>
							<div class="adr-table-wrap"><table><thead><tr><th>${__("站点 / 店铺")}</th><th>${__("币种")}</th><th>${__("余额中的递延")}</th><th>${__("递延明细合计")}</th><th>${__("差额")}</th><th>${__("交易数")}</th></tr></thead><tbody class="adr-reconcile-rows"></tbody></table></div>
						</section>
						<section class="adr-panel adr-detail-panel">
							<div class="adr-panel-heading"><h3>${__("快照内容")}</h3><span class="adr-detail-count"></span></div>
							<div class="adr-tabs" role="tablist">
								<button type="button" role="tab" class="adr-tab is-active" data-tab="transactions">${__("递延交易")}</button>
								<button type="button" role="tab" class="adr-tab" data-tab="orders">${__("关联订单")}</button>
								<button type="button" role="tab" class="adr-tab" data-tab="balances">${__("余额账户")}</button>
							</div>
							<div class="adr-detail-content"></div>
						</section>
					</main>
				</div>
			</div>`);
		this.$ = $(this.page.main).find(".adr-root");
	}

	bindEvents() {
		this.$.on("input", ".adr-search", (event) => {
			this.search = event.currentTarget.value.trim().toLowerCase();
			this.renderList();
		});
		this.$.on("click", "[data-snapshot]", (event) => {
			this.selected = this.snapshots.find((row) => row.name === event.currentTarget.dataset.snapshot) || null;
			this.render();
		});
		this.$.on("click", "[data-tab]", (event) => {
			this.activeTab = event.currentTarget.dataset.tab;
			this.renderDetails();
		});
	}

	// 仅供后续数据接入调用；本阶段不请求 API，也不创建或修改快照。
	setSnapshots(rows) {
		this.snapshots = Array.isArray(rows) ? rows : [];
		this.selected = this.snapshots.find((row) => row.name === this.selected?.name) || this.snapshots[0] || null;
		this.render();
	}

	render() {
		this.renderList();
		this.renderContext();
		this.renderSummary();
		this.renderDetails();
	}

	renderList() {
		this.$.find(".adr-snapshot-count").text(this.snapshots.length);
		const rows = this.snapshots.filter((row) =>
			[row.snapshot_label, row.amazon_store, row.marketplace_id, row.requested_at]
				.some((value) => String(value || "").toLowerCase().includes(this.search))
		);
		this.$.find(".adr-snapshot-list").html(rows.map((row) => `
			<button type="button" class="adr-snapshot ${row.name === this.selected?.name ? "is-selected" : ""}" data-snapshot="${this.esc(row.name)}">
				<span class="adr-snapshot-title">${this.esc(row.snapshot_label || this.date(row.requested_at) || __("未命名快照"))}</span>
				<span class="adr-snapshot-meta">${this.esc(row.amazon_store || row.snapshot_scope || __("全部站点"))} · ${this.esc(row.snapshot_status || __("等待中"))}</span>
				<span class="adr-snapshot-age">${this.esc(this.age(row.completed_at || row.requested_at))}</span>
			</button>`).join("") || `<div class="adr-sidebar-empty">${this.snapshots.length ? __("没有匹配的快照") : __("暂无快照。后续每次成功抓取都会在这里保留一条记录。")}</div>`);
	}

	renderContext() {
		const row = this.selected;
		this.$.find(".adr-title").text(row?.snapshot_label || __("尚无快照"));
		this.$.find(".adr-subtitle").text(row
			? `${__("生成于")} ${this.date(row.completed_at || row.requested_at)} · ${this.age(row.completed_at || row.requested_at)}`
			: __("抓取功能接入后，可在左侧打开任意一次完整快照。"));
		this.$.find(".adr-status-text").text(row?.snapshot_status || __("等待接入"));
		this.$.find(".adr-status").toggleClass("is-complete", row?.snapshot_status === "已完成");
		for (const [key, field] of Object.entries({ balance: "balance_fetched_at", transactions: "transactions_fetched_at", orders: "orders_fetched_at" })) {
			this.$.find(`[data-time=${key}]`).text(row?.[field] ? `${this.date(row[field])} · ${this.age(row[field])}` : "—");
		}
	}

	renderSummary() {
		const balances = this.rows(this.selected?.balance_json);
		const transactions = this.uniqueTransactions(this.rows(this.selected?.deferred_transactions_json));
		const groups = new Map();
		const getGroup = (row) => {
			const key = [row.amazon_store || row.store || "", row.marketplace_id || "", row.currency_code || ""].join("|");
			if (!groups.has(key)) groups.set(key, { store: row.amazon_store || row.store || "", marketplace: row.marketplace_id || "", currency: row.currency_code || "", balance: 0, detail: 0, count: 0 });
			return groups.get(key);
		};
		balances.forEach((row) => { getGroup(row).balance += this.number(row.deferred_balance); });
		transactions.forEach((row) => { const group = getGroup(row); group.detail += this.number(row.total_amount ?? row.net_amount); group.count += 1; });
		const result = [...groups.values()].sort((a, b) => `${a.currency}|${a.store}`.localeCompare(`${b.currency}|${b.store}`));
		const byCurrency = new Map();
		result.forEach((row) => {
			if (!byCurrency.has(row.currency)) byCurrency.set(row.currency, { currency: row.currency, balance: 0, detail: 0, count: 0 });
			const item = byCurrency.get(row.currency);
			item.balance += row.balance;
			item.detail += row.detail;
			item.count += row.count;
		});
		this.$.find(".adr-currency-cards").html([...byCurrency.values()].map((row) => `
			<article class="adr-currency-card"><span>${this.esc(row.currency || __("未知币种"))}</span><strong>${this.money(row.balance, row.currency)}</strong><small>${__("余额中的递延")}</small><div><span>${__("明细合计")} <b>${this.money(row.detail, row.currency)}</b></span><span>${__("待核差额")} <b class="${Math.abs(row.balance - row.detail) > 0.005 ? "adr-difference" : ""}">${this.money(row.balance - row.detail, row.currency)}</b></span></div></article>`).join("") || `<div class="adr-empty adr-empty-cards">${__("尚无快照数据。接入抓取后，这里会分别显示每种币种的递延金额。")}</div>`);
		this.$.find(".adr-reconcile-rows").html(result.map((row) => `
			<tr><td><strong>${this.esc(row.store || __("未归属店铺"))}</strong><small>${this.esc(row.marketplace || "—")}</small></td><td>${this.esc(row.currency || "—")}</td><td>${this.money(row.balance, row.currency)}</td><td>${this.money(row.detail, row.currency)}</td><td class="${Math.abs(row.balance - row.detail) > 0.005 ? "adr-difference" : ""}">${this.money(row.balance - row.detail, row.currency)}</td><td>${row.count}</td></tr>`).join("") || `<tr><td colspan="6" class="adr-empty">${__("暂无可核对的余额和递延交易")}</td></tr>`);
	}

	renderDetails() {
		this.$.find(".adr-tab").each((_, element) => {
			const active = element.dataset.tab === this.activeTab;
			$(element).toggleClass("is-active", active).attr("aria-selected", active);
		});
		const rows = this.activeTab === "transactions"
			? this.uniqueTransactions(this.rows(this.selected?.deferred_transactions_json))
			: this.rows(this.selected?.[this.activeTab === "orders" ? "orders_json" : "balance_json"]);
		this.$.find(".adr-detail-count").text(`${rows.length} ${__("条记录")}`);
		const columns = {
			transactions: [["posted_date", __("记账时间")], ["amazon_order_id", __("订单号")], ["transaction_id", __("交易 ID")], ["store", __("店铺")], ["transaction_status", __("状态")], ["release_date", __("预计释放")], ["total_amount", __("交易金额")]],
			orders: [["amazon_order_id", __("订单号")], ["store", __("店铺")], ["purchase_date", __("购买时间")], ["order_status", __("订单状态")], ["order_total", __("订单金额")]],
			balances: [["amazon_store", __("店铺")], ["marketplace_id", __("站点")], ["account_type", __("账户类型")], ["total_balance", __("总余额")], ["available_balance", __("可用")], ["reserved_balance", __("预留")], ["deferred_balance", __("递延")]],
		}[this.activeTab];
		const moneyFields = new Set(["total_amount", "order_total", "total_balance", "available_balance", "reserved_balance", "deferred_balance"]);
		this.$.find(".adr-detail-content").html(`<div class="adr-table-wrap"><table><thead><tr>${columns.map(([, label]) => `<th>${label}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${columns.map(([key]) => `<td>${moneyFields.has(key) ? this.money(row[key], row.currency_code) : this.esc(row[key] || "—")}</td>`).join("")}</tr>`).join("") || `<tr><td colspan="${columns.length}" class="adr-empty">${__("当前快照没有这类明细")}</td></tr>`}</tbody></table></div>`);
	}

	rows(raw) {
		try {
			const value = typeof raw === "string" ? JSON.parse(raw) : raw;
			return Array.isArray(value) ? value : Array.isArray(value?.rows) ? value.rows : [];
		} catch {
			return [];
		}
	}

	uniqueTransactions(rows) {
		const seen = new Set();
		return rows.filter((row) => {
			const key = [row.store || row.amazon_store || "", row.marketplace_id || "", row.transaction_id || row.name || ""].join("|");
			if (seen.has(key)) return false;
			seen.add(key);
			return true;
		});
	}

	number(value) { return Number(value || 0); }
	money(value, currency) { return `${this.esc(currency || "")} ${this.number(value).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`.trim(); }
	date(value) { return value ? String(value).replace("T", " ").replace(/\.\d+$/, "").slice(0, 19) : ""; }
	age(value) {
		if (!value) return "—";
		const date = new Date(String(value).replace(" ", "T"));
		const minutes = Math.floor((Date.now() - date.getTime()) / 60000);
		if (!Number.isFinite(minutes) || minutes < 0) return "—";
		if (minutes < 1) return __("不到 1 分钟");
		if (minutes < 60) return `${minutes} ${__("分钟前")}`;
		const hours = Math.floor(minutes / 60);
		return hours < 24 ? `${hours} ${__("小时前")}` : `${Math.floor(hours / 24)} ${__("天前")}`;
	}
	esc(value) { return frappe.utils.escape_html(String(value ?? "")); }
}
