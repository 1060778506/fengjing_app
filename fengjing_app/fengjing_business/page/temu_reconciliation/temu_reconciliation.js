const TEMU_RECONCILIATION_METHOD =
	"fengjing_app.fengjing_business.page.temu_managed_bookkee.temu_financial_analytics.get_reconciliation";
const TEMU_RECONCILIATION_CHECK_METHOD =
	"fengjing_app.fengjing_business.page.temu_managed_bookkee.temu_financial_analytics.set_reconciliation_detail_checked";
const TEMU_RECONCILIATION_CHECK_ALL_METHOD =
	"fengjing_app.fengjing_business.page.temu_managed_bookkee.temu_financial_analytics.set_reconciliation_details_checked";
const TEMU_NON_SELLER_SUBSIDY_ACCOUNT = "5001_fj - 主营业务收入 - 丰境";
const TEMU_AFTER_SALE_ACCOUNT = "560108_fj - 平台售后费【销售费用】 - 丰境";

frappe.pages["temu_reconciliation"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: "Temu财务对账明细", single_column: true });
	frappe.require("/assets/fengjing_app/js/图表-echarts.js", () => {
		wrapper.temuReconciliation = new TemuReconciliationPage(wrapper, page);
	});
};

frappe.pages["temu_reconciliation"].on_page_show = function (wrapper) {
	wrapper.temuReconciliation?.refresh();
};

class TemuReconciliationPage {
	constructor(wrapper, page) {
		this.wrapper = wrapper;
		this.page = page;
		this.pageNo = 1;
		this.detailRegion = "";
		this.detailType = "";
		this.availableMonths = [];
		this.initialMonthApplied = false;
		this.drawerOpen = false;
		this.selectedMonths = new Set();
		this.monthSelectionStarted = false;
		this.loadSerial = 0;
		this.data = {};
		this.chartInstance = null;
		this.loaded = false;
		this.bulkChecking = false;
		this.pendingChecks = 0;
		this.render();
		this.bind();
		this.renderMonthButtons();
		this.load();
	}

	render() {
		$(this.wrapper).addClass("trc-page");
		this.$ = $(this.page.main).html(`
			<div class="trc-shell">
				<section class="trc-hero"><div><span>TEMU RECONCILIATION</span><h2>待结算、结算与履约保障核对</h2><p>按批次、区域和SKU对照资金，并检查ERPNext物料绑定。</p></div><nav><a href="/app/temu_finance_overvi">财务总览</a><a href="/app/temu_ad_analysis">广告分析</a><a href="/app/temu_managed_bookkee">文件批次</a></nav></section>
				<section class="trc-filters">
					<div class="trc-months"><span>月份</span><div class="trc-month-buttons" role="group" aria-label="月份范围"></div></div>
					<label>批次<select data-filter="batch"><option value="">全部批次</option></select></label>
					<label>区域<select data-filter="region"><option value="">全部区域</option></select></label>
					<label>币种<select data-filter="currency"><option value="">全部币种</option></select></label>
					<label>物料绑定<select data-filter="binding"><option value="">全部</option><option value="bound">已绑定</option><option value="unbound">未绑定</option></select></label>
					<label>数据状态<select data-filter="status"><option value="">全部</option><option>都有数据</option><option>仅待结算</option><option>仅结算</option><option>仅履约保障</option></select></label>
					<label>开始日期<input type="date" data-filter="date_from"></label><label>结束日期<input type="date" data-filter="date_to"></label>
					<label class="trc-search">SKU、商品或物料<input data-filter="search" placeholder="输入关键词"></label>
					<button class="btn btn-primary trc-query">查询</button><button class="btn btn-default trc-reset">重置</button>
				</section>
				<section class="trc-kpis"></section>
				<section class="trc-card trc-detail-card"><header><div><span>交易明细</span><h3>最近结算交易</h3></div><div class="trc-detail-actions"><span class="trc-detail-amount"></span><small>最多显示当前筛选范围最近200条</small><button type="button" class="btn btn-default trc-summary-open" aria-expanded="false">交易汇总</button></div></header><div class="trc-settlement-comparison"></div><div class="trc-detail-filters"><div class="trc-detail-regions" role="group" aria-label="交易区域"></div><div class="trc-detail-types" role="group" aria-label="交易类型"></div></div><div class="trc-table-wrap"><table class="trc-detail-table"><thead><tr><th class="trc-check-all-heading"><label title="全选当前显示的明细并保存"><input type="checkbox" class="trc-detail-check-all" aria-label="全选当前显示的明细"> 全选</label></th><th>账务时间</th><th>订单</th><th>商品 / 物料</th><th>区域</th><th>交易类型</th><th>数量</th><th>金额</th><th>原销售回款</th></tr></thead><tbody></tbody></table></div><div class="trc-summary-overlay" hidden><div class="trc-summary-backdrop" data-close-summary></div><aside class="trc-summary-drawer" role="dialog" aria-modal="true" aria-label="交易汇总"><div class="trc-summary-heading"><h3>交易汇总</h3><button type="button" data-close-summary aria-label="关闭交易汇总">×</button></div><p>当前筛选范围全部 SKU；金额合计为结算交易与履约保障的净额。</p><div class="trc-summary-body"></div></aside></div></section>
				<section class="trc-card trc-chart-card"><header><div><span>差异观察</span><h3>当前页SKU资金差异</h3></div><small>已结算 + 履约保障 − 待结算</small></header><div id="trc-difference" class="trc-chart"></div></section>
				<section class="trc-card"><header><div><span>SKU汇总</span><h3>待结算与结算对照</h3></div><div class="trc-page-info"></div></header><div class="trc-table-wrap"><table class="trc-summary-table"><thead><tr><th>商品 / 对应物料</th><th>区域</th><th>状态</th><th>待结算</th><th>结算净额</th><th>履约保障</th><th>差异</th><th>交易</th></tr></thead><tbody></tbody></table></div><footer><button class="btn btn-default trc-prev">上一页</button><button class="btn btn-default trc-next">下一页</button></footer></section>
			</div>`);
	}

	bind() {
		this.$.on("click", "[data-month]", (event) => this.selectMonth(event.currentTarget.dataset.month));
		this.$.on("change", "[data-filter=date_from], [data-filter=date_to]", () => {
			this.selectedMonths.clear();
			this.monthSelectionStarted = false;
			this.renderMonthButtons();
		});
		this.$.on("click", ".trc-query", () => { this.pageNo = 1; this.detailType = ""; this.load(); });
		this.$.on("keyup", "[data-filter=search]", (event) => { if (event.key === "Enter") { this.pageNo = 1; this.detailType = ""; this.load(); } });
		this.$.on("click", ".trc-reset", () => { this.$.find("[data-filter]").val(""); this.availableMonths = []; this.selectedMonths.clear(); this.monthSelectionStarted = false; this.initialMonthApplied = false; this.renderMonthButtons(); this.pageNo = 1; this.detailRegion = ""; this.detailType = ""; this.load(); });
		this.$.on("click", ".trc-prev", () => { if (this.pageNo > 1) { this.pageNo -= 1; this.load(); } });
		this.$.on("click", ".trc-next", () => { if (this.pageNo < (this.data.pages || 1)) { this.pageNo += 1; this.load(); } });
		this.$.on("click", "[data-detail-region]", (event) => {
			this.detailRegion = event.currentTarget.dataset.detailRegion;
			this.load();
		});
		this.$.on("click", "[data-detail-type]", (event) => {
			this.detailType = event.currentTarget.dataset.detailType;
			this.load();
		});
		this.$.on("change", ".trc-detail-checked", (event) => this.saveDetailChecked(event.currentTarget));
		this.$.on("change", ".trc-detail-check-all", (event) => this.saveAllDetailsChecked(event.currentTarget));
		this.$.on("click", ".trc-summary-open", () => this.toggleSummaryDrawer(!this.drawerOpen));
		this.$.on("click", ".trc-summary-grid [data-copy-column]", (event) => this.copySummaryColumn(event.currentTarget));
		this.$.on("keydown", ".trc-summary-grid [data-copy-column]", (event) => {
			if (event.key === "Enter" || event.key === " ") {
				event.preventDefault();
				this.copySummaryColumn(event.currentTarget);
			}
		});
		this.$.on("click", "[data-close-summary]", () => this.toggleSummaryDrawer(false));
		document.addEventListener("keydown", (event) => { if (event.key === "Escape" && this.drawerOpen) this.toggleSummaryDrawer(false); });
		this.$.on("change", "[data-filter=batch]", (event) => {
			const batch = (this.data.options?.batches || []).find((row) => row.value === event.target.value);
			if (batch) { this.$.find("[data-filter=date_from]").val(batch.date_from || ""); this.$.find("[data-filter=date_to]").val(batch.date_to || ""); this.selectedMonths.clear(); this.monthSelectionStarted = false; this.renderMonthButtons(); }
		});
		window.addEventListener("resize", frappe.utils.debounce(() => {
			this.chartInstance?.resize();
			if (this.drawerOpen) this.positionSummaryDrawer();
		}, 120));
	}

	monthKeys() {
		const [year, month] = frappe.datetime.get_today().slice(0, 7).split("-").map(Number);
		const windowMonths = new Set(Array.from({ length: 13 }, (_, index) => {
			const date = new Date(year, month - 1 + index - 6, 1);
			return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
		}));
		const months = this.availableMonths.filter((value) => windowMonths.has(value));
		const latest = this.availableMonths[this.availableMonths.length - 1];
		if (latest && !months.includes(latest)) months.push(latest);
		return months.sort();
	}

	selectLatestMonth(reload = true) {
		const latest = this.availableMonths[this.availableMonths.length - 1];
		this.selectedMonths = latest ? new Set([latest]) : new Set();
		this.monthSelectionStarted = false;
		if (latest) {
			this.$.find("[data-filter=date_from]").val(this.monthBounds(latest).from);
			this.$.find("[data-filter=date_to]").val(this.monthBounds(latest).to);
		}
		this.renderMonthButtons();
		if (reload) { this.pageNo = 1; this.load(); }
	}

	monthBounds(month) {
		const [year, value] = month.split("-").map(Number);
		const prefix = `${year}-${String(value).padStart(2, "0")}`;
		return { from: `${prefix}-01`, to: `${prefix}-${String(new Date(year, value, 0).getDate()).padStart(2, "0")}` };
	}

	renderMonthButtons() {
		this.$.find(".trc-month-buttons").html(this.monthKeys().map((month) => {
			const [year, value] = month.split("-");
			return `<button type="button" data-month="${month}" class="trc-month-button${this.selectedMonths.has(month) ? " is-active" : ""}" aria-pressed="${this.selectedMonths.has(month)}">${year}年${Number(value)}月</button>`;
		}).join(""));
	}

	selectMonth(month) {
		const months = this.monthKeys();
		if (!months.includes(month)) return;
		if (!this.monthSelectionStarted || !this.selectedMonths.size) {
			this.selectedMonths = new Set([month]);
			this.monthSelectionStarted = true;
		} else if (this.selectedMonths.has(month)) {
			if (this.selectedMonths.size > 1) this.selectedMonths.delete(month);
		} else {
			this.selectedMonths.add(month);
		}
		const indexes = months.map((value, index) => this.selectedMonths.has(value) ? index : -1).filter((index) => index >= 0);
		if (!indexes.length) return;
		this.selectedMonths = new Set(months.slice(Math.min(...indexes), Math.max(...indexes) + 1));
		const selected = [...this.selectedMonths].sort();
		this.$.find("[data-filter=date_from]").val(this.monthBounds(selected[0]).from);
		this.$.find("[data-filter=date_to]").val(this.monthBounds(selected[selected.length - 1]).to);
		this.renderMonthButtons();
		this.pageNo = 1;
		this.detailType = "";
		this.load();
	}

	filters() { const result = {}; this.$.find("[data-filter]").each((_, element) => { result[element.dataset.filter] = element.value || ""; }); return result; }

	async load() {
		const serial = ++this.loadSerial;
		this.$.addClass("is-loading");
		try {
			const response = await frappe.call({ method: TEMU_RECONCILIATION_METHOD, args: { filters: this.filters(), page: this.pageNo, page_size: 50, detail_region: this.detailRegion, detail_type: this.detailType } });
			if (serial !== this.loadSerial) return;
			this.data = response.message || {};
			this.availableMonths = this.data.options?.available_months || [];
			if (!this.initialMonthApplied) {
				this.initialMonthApplied = true;
				if (this.availableMonths.length && !this.filters().date_from && !this.filters().date_to) {
					this.selectLatestMonth(false);
					this.load();
					return;
				}
			}
			this.pageNo = this.data.page || 1;
			this.renderMonthButtons();
			this.fillOptions();
			this.renderData();
			this.loaded = true;
		} catch (error) {
			if (serial !== this.loadSerial) return;
			console.error(error); frappe.msgprint({ title: "Temu对账明细加载失败", message: error.message || "请查看错误日志。", indicator: "red" });
		} finally { if (serial === this.loadSerial) this.$.removeClass("is-loading"); }
	}

	refresh() { if (this.loaded) this.load(); }

	fillOptions() {
		const options = this.data.options || {};
		this.setOptions("batch", options.batches || [], "全部批次");
		this.setOptions("region", options.regions || [], "全部区域");
		this.setOptions("currency", options.currencies || [], "全部币种");
	}

	setOptions(name, values, empty) {
		const select = this.$.find(`[data-filter=${name}]`), old = select.val();
		select.html(`<option value="">${empty}</option>` + values.map((value) => { const item = typeof value === "object" ? value : { value, label: value }; return `<option value="${this.esc(item.value)}">${this.esc(item.label)}</option>`; }).join(""));
		select.val(old);
	}

	renderData() {
		const s = this.data.summary || {};
		const summaries = this.data.summary_by_currency || {};
		const currency = this.filters().currency || Object.keys(summaries)[0] || this.data.options?.currencies?.[0] || "";
		const card = (label, field, index) => `<article class="k${index}"><span>${label}</span>${Object.entries(summaries).map(([code, values]) => `<b class="${values[field] < 0 ? "negative" : ""}">${this.money(values[field], code)}</b>`).join("") || `<b>${this.money(0, currency)}</b>`}</article>`;
		this.$.find(".trc-kpis").html(card("待结算", "pending", 0) + card("结算净额", "settled", 1) + card("履约保障", "protection", 2) + card("总差异", "difference", 3) + `<article><span>SKU组</span><b>${this.num(s.groups)}</b><small>${this.num(s.unbound)} 个未绑定物料</small></article>`);
		this.renderSummary(currency);
		this.renderSettlementComparison();
		this.renderDetailFilters();
		this.renderDetails();
		this.renderDetailSummary();
		if (this.drawerOpen) this.positionSummaryDrawer();
		this.renderChart(currency);
	}

	renderSummary(defaultCurrency) {
		const rows = this.data.rows || [];
		this.$.find(".trc-page-info").text(`共 ${this.num(this.data.total)} 组 · 第 ${this.data.page || 1}/${this.data.pages || 1} 页`);
		this.$.find(".trc-prev").prop("disabled", (this.data.page || 1) <= 1);
		this.$.find(".trc-next").prop("disabled", (this.data.page || 1) >= (this.data.pages || 1));
		this.$.find(".trc-summary-table tbody").html(rows.map((row) => {
			const item = row.is_bound ? `<a href="/app/item/${encodeURIComponent(row.item_code)}">${this.esc(row.item_code)}</a><small>${this.esc(row.item_name)}</small>` : `<em>未绑定ERPNext物料</em>`;
			return `<tr><td><div class="trc-product">${row.item_image ? `<img src="${this.esc(row.item_image)}" loading="lazy">` : "<i>SKU</i>"}<span><b>${this.esc(row.product_name || row.sku)}</b><small>${this.esc(row.sku)} · ${this.esc(row.sku_code || "无货号")}</small>${item}</span></div></td><td>${this.esc(row.region)}</td><td><span class="trc-status s-${this.statusClass(row.status)}">${this.esc(row.status)}</span></td><td>${this.money(row.pending_amount, row.currency || defaultCurrency)}</td><td>${this.money(row.settlement_amount, row.currency || defaultCurrency)}</td><td>${this.money(row.protection_amount, row.currency || defaultCurrency)}</td><td class="${row.difference < 0 ? "negative" : "positive"}">${this.money(row.difference, row.currency || defaultCurrency)}</td><td><b>${this.num(row.transaction_count)}条</b><small>${this.esc(row.types || "—")}</small></td></tr>`;
		}).join("") || `<tr><td colspan="8" class="trc-empty">当前条件没有对账数据</td></tr>`);
	}

	renderSettlementComparison() {
		const values = this.data.settlement_comparison || [];
		const { date_from: from, date_to: to } = this.filters();
		const range = from && to ? `${this.esc(from)} 至 ${this.esc(to)}` : this.esc(from || to || "全部日期");
		this.$.find(".trc-settlement-comparison").html(`
			<div class="trc-comparison-heading"><b>结算交易核对</b><span>${range} · 全部区域 · 全部交易</span></div>
			${values.length ? values.map((row) => {
				const matches = Math.abs(Number(row.difference || 0)) < 0.005;
				return `<div class="trc-comparison-row">
					<div class="trc-comparison-result"><span>财务总览结算净额 ${this.money(row.overview_settlement, row.currency)}</span><b class="${matches ? "is-matched" : "is-different"}">${matches ? "一致" : `差额 ${this.money(row.difference, row.currency)}`}</b></div>
					<div class="trc-comparison-formula"><span>销售回款 ${this.money(row.sales_receipts, row.currency)}</span><span>− 销售冲回 ${this.money(row.sales_reversals, row.currency)}</span><span>+ 非商责补贴 ${this.money(row.non_seller_subsidy, row.currency)}</span><strong>= ${this.money(row.calculated, row.currency)}</strong></div>
				</div>`;
			}).join("") : `<div class="trc-comparison-empty">当前日期范围暂无结算交易可对比</div>`}
		`);
	}

	renderDetailFilters() {
		const button = (value, label, selected, attribute) => `<button type="button" class="trc-filter-button${selected === value ? " is-active" : ""}" ${attribute}="${this.esc(value)}" aria-pressed="${selected === value}">${this.esc(label)}</button>`;
		this.$.find(".trc-detail-regions").html(["", "全球", "欧区", "美区"].map((region) => button(region, region || "全部", this.detailRegion, "data-detail-region")).join(""));
		const preferred = ["销售回款", "销售冲回", "非商责补贴", "售后问题", "售后补寄"];
		const types = [...new Set([...(this.data.options?.transaction_types || []), this.detailType].filter(Boolean))].sort((left, right) => {
			const leftOrder = preferred.indexOf(left), rightOrder = preferred.indexOf(right);
			return (leftOrder < 0 ? preferred.length : leftOrder) - (rightOrder < 0 ? preferred.length : rightOrder) || left.localeCompare(right, "zh-CN");
		});
		this.$.find(".trc-detail-types").html(button("", "全部", this.detailType, "data-detail-type") + types.map((type) => button(type, type, this.detailType, "data-detail-type")).join(""));
	}

	renderDetails() {
		const receiptOnly = this.detailType === "销售回款";
		this.$.find(".trc-detail-table").toggleClass("trc-sales-receipt-only", receiptOnly);
		this.$.find(".trc-detail-table tbody").html((this.data.details || []).map((row) => {
			const item = row.is_bound ? `<a href="/app/item/${encodeURIComponent(row.item_code)}">${this.esc(row.item_code)}</a>${row.item_name ? `<small class="trc-item-name">${this.esc(row.item_name)}</small>` : ""}` : `<em>未绑定</em>`;
			const image = row.item_image ? `<img src="${this.esc(row.item_image)}" loading="lazy" alt="">` : "<i>SKU</i>";
			const checked = `<input type="checkbox" class="trc-detail-checked" data-doctype="${this.esc(row.doctype)}" data-name="${this.esc(row.name)}" aria-label="核对 ${this.esc(row.order_number || row.name)}" ${row.is_checked ? "checked" : ""}>`;
			const time = row.accounting_time ? this.esc(String(row.accounting_time).replace("T", " ").slice(0, 19)) : `<span class="trc-undated">源文件无账务时间</span><small>批次 ${this.esc(row.batch_period || row.batch)}</small>`;
			const receipt = row.original_receipt_amount !== null && row.original_receipt_amount !== undefined
				? `${this.money(row.original_receipt_amount, row.currency)} <small>(${this.esc(row.original_receipt_month?.slice(0, 4))}年${this.esc(Number(row.original_receipt_month?.slice(5, 7)))}月)</small>` : "—";
			return `<tr><td>${checked}</td><td>${time}</td><td>${this.esc(row.order_number || "—")}</td><td><div class="trc-product">${image}<span><b>${this.esc(row.product_name || row.sku)}</b><small>SKU ${this.esc(row.sku || "—")}</small>${item}</span></div></td><td>${this.esc(row.region || "—")}</td><td>${this.esc(row.transaction_type || "—")}</td><td>${this.num(row.quantity)}</td><td class="${row.amount < 0 ? "negative" : "positive"}">${this.money(row.amount, row.currency)}</td><td class="trc-original-receipt">${receipt}</td></tr>`;
		}).join("") || `<tr><td colspan="${receiptOnly ? 8 : 9}" class="trc-empty">暂无结算交易明细</td></tr>`);
		this.syncSelectAll();
	}

	syncSelectAll() {
		const inputs = this.$.find(".trc-detail-checked");
		const checked = inputs.filter(":checked").length;
		this.$.find(".trc-detail-check-all")
			.prop("checked", !!inputs.length && checked === inputs.length)
			.prop("indeterminate", checked > 0 && checked < inputs.length)
			.prop("disabled", !inputs.length || this.bulkChecking || this.pendingChecks > 0);
	}

	renderDetailSummary() {
		const summary = this.data.detail_summary || {};
		const groups = summary.groups || [];
		const displayRows = this.buildSummaryDisplayRows(groups);
		this.summaryDisplayRows = displayRows;
		const isSalesReversal = this.detailType === "销售冲回";
		const isNonSellerSubsidy = this.detailType === "非商责补贴";
		const isAfterSale = this.detailType === "售后问题";
		const adjustmentCount = displayRows.length - groups.length;
		const totals = summary.amount_by_currency || {};
		const quantityByCurrency = displayRows.reduce((result, row) => {
			const currency = row.currency || "未知";
			result[currency] = (result[currency] || 0) + Number(row.quantity || 0);
			return result;
		}, {});
		const currencies = Object.keys(totals);
		if (!currencies.length && this.filters().currency) currencies.push(this.filters().currency);
		const transactionTotals = summary.transaction_amount_by_currency || {};
		const protectionTotals = summary.protection_amount_by_currency || {};
		const columnOrder = isSalesReversal
			? ["product", "itemName", "costCenter", "itemId", "reversalFlag", "amount", "region", "type", "count", "quantity", "unitRate"]
			: isNonSellerSubsidy || isAfterSale
				? ["product", "costCenter", "itemId", "account", "amount", "region", "quantity", "unitRate", "itemName", "type", "count"]
				: ["product", "costCenter", "itemId", "quantity", "unitRate", "region", "amount", "itemName", "type", "count"];
		const columnClasses = {
			product: "trc-col-product", costCenter: "trc-col-cost-center", itemId: "trc-col-item-id", account: "trc-col-account",
			quantity: "trc-col-quantity", unitRate: "trc-col-unit-rate", region: "trc-col-region",
			reversalFlag: "trc-col-reversal-flag", amount: "trc-col-amount", itemName: "trc-col-item-name",
			type: "trc-col-type", count: "trc-col-count",
		};
		const columnHeadings = {
			product: "<th>商品</th>",
			costCenter: '<th data-copy-column="cost_center" tabindex="0" title="点击复制成本中心整列">成本中心</th>',
			itemId: '<th data-copy-column="item_code" tabindex="0" title="点击复制物料 ID 整列">物料 ID</th>',
			account: '<th data-copy-column="account" tabindex="0" title="点击复制科目整列">科目表</th>',
			quantity: isSalesReversal || isNonSellerSubsidy ? "<th>数量</th>" : '<th data-copy-column="quantity" tabindex="0" title="点击复制数量整列">数量</th>',
			unitRate: isSalesReversal || isNonSellerSubsidy ? "<th>平均单价</th>" : '<th data-copy-column="unit_rate" tabindex="0" title="点击复制平均单价整列">平均单价</th>',
			region: isAfterSale ? '<th data-copy-column="region" tabindex="0" title="点击复制摘要整列">摘要</th>' : '<th data-copy-column="region" tabindex="0" title="点击复制区域整列">区域(说明)</th>',
			reversalFlag: '<th class="trc-summary-reversal-flag" data-copy-column="reversal_flag" tabindex="0" title="点击复制回冲标识整列">回冲标识</th>',
			amount: isSalesReversal || isAfterSale ? '<th data-copy-column="amount" tabindex="0" title="点击复制不带负号的金额整列">金额</th>' : isNonSellerSubsidy ? '<th data-copy-column="amount" tabindex="0" title="点击复制贷方金额整列">金额(贷方)</th>' : "<th>金额</th>",
			itemName: "<th>物料名称</th>", type: "<th>交易类型</th>", count: "<th>笔数</th>",
		};
		const tableRows = displayRows.map((row) => {
			const image = row.item_image ? `<img src="${this.esc(row.item_image)}" loading="lazy" alt="">` : "<i>SKU</i>";
			const title = row.product_name || row.item_name || row.sku || "";
			const shortTitle = title.slice(0, Math.ceil(title.length / 3)) + (title.length > 1 ? "…" : "");
			const itemId = row.is_bound ? `<a href="/app/item/${encodeURIComponent(row.item_code)}">${this.esc(row.item_code)}</a>` : "未绑定物料";
			const unitRate = row.unit_rate === null ? "—" : Number(row.unit_rate).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: row.rate_precision });
			const cells = {
				product: `<td><div class="trc-summary-product">${image}<div><b title="${this.esc(title)}">${this.esc(shortTitle)}</b><small title="SKU ${this.esc(row.sku)}">SKU ${this.esc(row.sku)}${row.adjustment ? " · 调整行" : ""}</small></div></div></td>`,
				costCenter: `<td class="trc-summary-cost-center" title="${this.esc(row.cost_center || "")}">${this.esc(row.cost_center || "—")}</td>`,
				itemId: `<td class="trc-summary-item-id">${itemId}</td>`,
				account: `<td class="trc-summary-account" title="${this.esc(isAfterSale ? TEMU_AFTER_SALE_ACCOUNT : TEMU_NON_SELLER_SUBSIDY_ACCOUNT)}">${this.esc(isAfterSale ? TEMU_AFTER_SALE_ACCOUNT : TEMU_NON_SELLER_SUBSIDY_ACCOUNT)}</td>`,
				quantity: `<td class="trc-summary-quantity">${this.num(row.quantity)}</td>`,
				unitRate: `<td class="trc-summary-unit-rate">${unitRate}</td>`,
				region: `<td class="trc-summary-region" title="${this.esc(this.summaryRegionLabel(row))}">${this.esc(this.summaryRegionLabel(row))}</td>`,
				reversalFlag: '<td class="trc-summary-reversal-flag">-1</td>',
				amount: `<td class="trc-summary-amount ${row.amount < 0 ? "negative" : "positive"}">${this.money(row.amount, row.currency)}</td>`,
				itemName: `<td class="trc-summary-item-name" title="${this.esc(row.item_name || "")}">${this.esc(row.item_name || "—")}</td>`,
				type: `<td>${this.esc(row.transaction_type)}</td>`,
				count: `<td>${row.adjustment ? "—" : this.num(row.count)}</td>`,
			};
			return `<tr${row.adjustment ? ' class="trc-adjustment-row"' : ""}>${columnOrder.map((key) => cells[key]).join("")}</tr>`;
		}).join("");
		this.$.find(".trc-detail-amount").html(currencies.map((currency) => `<span>交易金额 ${this.money(totals[currency] || 0, currency)}</span>`).join("") || "交易金额 —");
		this.$.find(".trc-summary-body").html(`
			<div class="trc-summary-stats"><b>${this.num(summary.count)} 条交易</b><span>${this.num(groups.length)} 个 SKU／区域／类型分组${adjustmentCount ? ` · ${adjustmentCount} 条单价调整行` : ""} · 点击表头复制整列</span></div>
			<div class="trc-summary-totals">${currencies.map((currency) => {
				const totalAmount = totals[currency] || 0;
				const transactionAmount = transactionTotals[currency] || 0;
				const protectionAmount = protectionTotals[currency] || 0;
				return `<article class="trc-total-card"><b class="${totalAmount < 0 ? "negative" : "positive"}">${this.money(totalAmount, currency)}</b><span>${this.esc(currency)} 金额合计</span></article>` + (isSalesReversal ? "" : `<article class="trc-total-card"><b>${this.num(quantityByCurrency[currency] || 0)}</b><span>数量合计</span></article>`) + (protectionAmount ? `<article class="trc-total-card"><b class="${transactionAmount < 0 ? "negative" : "positive"}">${this.money(transactionAmount, currency)}</b><span>其中结算交易</span></article><article class="trc-total-card"><b class="${protectionAmount < 0 ? "negative" : "positive"}">${this.money(protectionAmount, currency)}</b><span>其中履约保障</span></article>` : "");
			}).join("")}</div>
			<div class="trc-summary-table-wrap"><table class="trc-summary-grid${isSalesReversal ? " trc-reversal-summary" : ""}${isNonSellerSubsidy ? " trc-subsidy-summary" : ""}${isAfterSale ? " trc-after-sale-summary" : ""}">
				<colgroup>${columnOrder.map((key) => `<col class="${columnClasses[key]}">`).join("")}</colgroup>
				<thead><tr>${columnOrder.map((key) => columnHeadings[key]).join("")}</tr></thead>
				<tbody>${tableRows || `<tr><td colspan="${columnOrder.length}" class="trc-summary-empty">当前条件下没有交易</td></tr>`}</tbody>
			</table></div>
		`);
	}

	summaryRegionLabel(row) {
		const region = String(row.region || "未分类");
		const type = String(row.transaction_type || "");
		return ["销售回款", "销售冲回", "非商责补贴", "售后问题"].includes(type) ? `${type}-${region}` : region;
	}

	buildSummaryDisplayRows(groups) {
		return groups.flatMap((row) => {
			const quantity = Number(row.quantity || 0);
			const cents = Math.round(Number(row.amount || 0) * 100);
			if (!Number.isSafeInteger(quantity) || Math.abs(quantity) <= 1 || !Number.isSafeInteger(cents) || cents % Math.abs(quantity) === 0) {
				return [{ ...row, unit_rate: quantity ? Number(row.amount || 0) / quantity : null, rate_precision: Number.isSafeInteger(quantity) ? 2 : 6, adjustment: false }];
			}
			const baseRateCents = Math.trunc(cents / quantity);
			const baseQuantity = quantity - Math.sign(quantity);
			const baseAmountCents = baseQuantity * baseRateCents;
			const adjustmentQuantity = Math.sign(quantity);
			const adjustmentAmountCents = cents - baseAmountCents;
			return [
				{ ...row, quantity: baseQuantity, amount: baseAmountCents / 100, unit_rate: baseRateCents / 100, rate_precision: 2, adjustment: false },
				{ ...row, quantity: adjustmentQuantity, amount: adjustmentAmountCents / 100, unit_rate: adjustmentAmountCents / adjustmentQuantity / 100, rate_precision: 2, adjustment: true },
			];
		});
	}

	async copySummaryColumn(heading) {
		const rows = this.summaryDisplayRows || [];
		if (!rows.length) return;
		const column = heading.dataset.copyColumn;
		if (column === "amount" && !["销售冲回", "非商责补贴", "售后问题"].includes(this.detailType)) return;
		if (column === "reversal_flag" && this.detailType !== "销售冲回") return;
		if (column === "account" && !["非商责补贴", "售后问题"].includes(this.detailType)) return;
		if (["quantity", "unit_rate"].includes(column) && ["销售冲回", "非商责补贴"].includes(this.detailType)) return;
		const values = rows.map((row) => {
			if (column === "cost_center") return String(row.cost_center || "");
			if (column === "item_code") return row.is_bound ? String(row.item_code || "") : "";
			if (column === "account") return this.detailType === "售后问题" ? TEMU_AFTER_SALE_ACCOUNT : TEMU_NON_SELLER_SUBSIDY_ACCOUNT;
			if (column === "quantity") return String(Number(row.quantity || 0));
			if (column === "unit_rate") return row.unit_rate === null ? "" : Number(row.unit_rate).toFixed(row.rate_precision);
			if (column === "region") return `${this.summaryRegionLabel(row)};`;
			if (column === "reversal_flag") return "-1";
			if (column === "amount") return (["销售冲回", "售后问题"].includes(this.detailType) ? Math.abs(Number(row.amount || 0)) : Number(row.amount || 0)).toFixed(2);
			return "";
		});
		const content = values.map((value) => value.replace(/[\r\n\t]/g, " ")).join("\n");
		try {
			await navigator.clipboard.writeText(content);
			frappe.show_alert({ message: `已复制 ${heading.textContent.trim()} ${values.length} 行${column === "amount" && ["销售冲回", "售后问题"].includes(this.detailType) ? "（已去掉负号）" : ""}`, indicator: "green" });
		} catch (error) {
			frappe.show_alert({ message: "复制失败，请检查浏览器剪贴板权限", indicator: "red" });
		}
	}

	toggleSummaryDrawer(open) {
		this.drawerOpen = open;
		this.$.find(".trc-detail-card").toggleClass("trc-drawer-open", open);
		this.$.find(".trc-summary-overlay").prop("hidden", !open);
		if (open) this.positionSummaryDrawer();
		else this.$.find(".trc-detail-card").css("min-height", "");
		this.$.find(".trc-summary-open").attr("aria-expanded", String(open));
	}

	positionSummaryDrawer() {
		const card = this.$.find(".trc-detail-card")[0];
		const heading = this.$.find(".trc-detail-table thead")[0];
		if (!card || !heading) return;
		const top = Math.max(0, heading.getBoundingClientRect().bottom - card.getBoundingClientRect().top);
		this.$.find(".trc-summary-overlay").css("top", `${Math.round(top)}px`);
		const drawer = this.$.find(".trc-summary-drawer")[0];
		if (drawer) card.style.minHeight = `${Math.ceil(top + drawer.scrollHeight + 16)}px`;
	}

	async saveDetailChecked(input) {
		const checked = input.checked;
		this.pendingChecks += 1;
		input.disabled = true;
		this.syncSelectAll();
		try {
			await frappe.call({
				method: TEMU_RECONCILIATION_CHECK_METHOD,
				args: { doctype: input.dataset.doctype, name: input.dataset.name, checked: checked ? 1 : 0 },
			});
			const row = (this.data.details || []).find((item) => item.doctype === input.dataset.doctype && item.name === input.dataset.name);
			if (row) row.is_checked = checked ? 1 : 0;
			this.syncSelectAll();
			frappe.show_alert({ message: checked ? "已保存核对状态" : "已取消核对", indicator: "green" });
		} catch (error) {
			input.checked = !checked;
			this.syncSelectAll();
			frappe.msgprint({ title: "核对状态保存失败", message: error.message || "请重试。", indicator: "red" });
		} finally {
			this.pendingChecks -= 1;
			input.disabled = false;
			this.syncSelectAll();
		}
	}

	async saveAllDetailsChecked(input) {
		if (this.bulkChecking) return;
		const records = (this.data.details || []).map(({ doctype, name }) => ({ doctype, name }));
		if (!records.length) return;
		const checked = input.checked;
		const serial = this.loadSerial;
		this.bulkChecking = true;
		this.$.find(".trc-detail-checked, .trc-detail-check-all").prop("disabled", true);
		try {
			await frappe.call({ method: TEMU_RECONCILIATION_CHECK_ALL_METHOD, args: { records, checked: checked ? 1 : 0 } });
			if (serial === this.loadSerial) {
				this.data.details.forEach((row) => { row.is_checked = checked ? 1 : 0; });
				this.$.find(".trc-detail-checked").prop("checked", checked);
			}
			frappe.show_alert({ message: `已保存当前显示的 ${records.length} 条核对状态`, indicator: "green" });
		} catch (error) {
			frappe.msgprint({ title: "批量保存核对状态失败", message: error.message || "请重试。", indicator: "red" });
		} finally {
			this.bulkChecking = false;
			this.$.find(".trc-detail-checked").prop("disabled", false);
			this.syncSelectAll();
		}
	}

	renderChart(currency) {
		this.chartInstance?.dispose();
		const rows = [...(this.data.rows || [])].sort((a, b) => Math.abs(b.difference) - Math.abs(a.difference)).slice(0, 15).reverse();
		const element = this.wrapper.querySelector("#trc-difference");
		if (!element || typeof echarts === "undefined") return;
		this.chartInstance = echarts.init(element);
		this.chartInstance.setOption({ tooltip: { trigger: "axis", axisPointer: { type: "shadow" } }, grid: { left: 180, right: 35, top: 20, bottom: 35 }, xAxis: { type: "value" }, yAxis: { type: "category", data: rows.map((row) => row.item_name || row.product_name || row.sku), axisLabel: { width: 160, overflow: "truncate" } }, series: [{ name: `差异 ${currency}`, type: "bar", data: rows.map((row) => ({ value: row.difference, itemStyle: { color: row.difference < 0 ? "#df5a70" : "#22a889", borderRadius: row.difference < 0 ? [6, 0, 0, 6] : [0, 6, 6, 0] } })) }] });
	}

	statusClass(value) { return { "都有数据": "both", "仅待结算": "pending", "仅结算": "settled", "仅履约保障": "protection" }[value] || "other"; }
	money(value, currency) { return `${this.esc(currency || "")} ${Number(value || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`; }
	num(value) { return Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 2 }); }
	esc(value) { return frappe.utils.escape_html(String(value ?? "")); }
}
