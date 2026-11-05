const TEMU_ANALYTICS_METHOD =
	"fengjing_app.fengjing_business.page.temu_managed_bookkee.temu_financial_analytics";
const STORAGE_EPR_REMARK = "__storage_epr_fees__";
const STORAGE_EPR_REMARKS = new Set(["仓储综合服务费", "合规EPR环保费", "合规EPR物流包装环保费"]);

frappe.pages["temu_finance_overvi"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: "Temu财务总览", single_column: true });
	frappe.require("/assets/fengjing_app/js/图表-echarts.js", () => {
		wrapper.temuFinanceOverview = new TemuFinanceOverview(wrapper, page);
	});
};

frappe.pages["temu_finance_overvi"].on_page_show = function (wrapper) {
	wrapper.temuFinanceOverview?.refresh();
};

class TemuFinanceOverview {
	constructor(wrapper, page) {
		this.wrapper = wrapper;
		this.page = page;
		this.data = {};
		this.charts = [];
		this.loaded = false;
		this.availableMonths = [];
		this.selectedMonths = new Set();
		this.monthSelectionStarted = false;
		this.initialMonthApplied = false;
		this.loadSerial = 0;
		this.ledgerType = "";
		this.ledgerRemark = "";
		this.ledgerDrawerOpen = false;
		this.render();
		this.bind();
		this.renderMonthButtons();
		this.load();
	}

	render() {
		$(this.wrapper).addClass("tfo-page");
		this.$ = $(this.page.main).html(`
			<div class="tfo-shell">
				<section class="tfo-hero">
				<div><span>TEMU FINANCE</span><h2>资金、结算与账务表现</h2><p>从已保存的对账批次读取数据，金额始终按币种分别统计。</p></div>
					<nav><a href="/app/temu_reconciliation">财务对账明细</a><a href="/app/temu_ad_analysis">广告数据分析</a><a href="/app/temu_managed_bookkee">文件批次</a></nav>
				</section>
				<section class="tfo-filters">
					<div class="tfo-months"><span>月份</span><div class="tfo-month-buttons" role="group" aria-label="月份范围"></div></div>
					<label>批次<select data-filter="batch"><option value="">全部批次</option></select></label>
					<label>店铺（成本中心）<select data-filter="cost_center"><option value="">全部店铺</option></select></label>
					<label>区域<select data-filter="region"><option value="">全部区域</option></select></label>
					<label>币种<select data-filter="currency"><option value="">全部币种</option></select></label>
					<label>开始日期<input type="date" data-filter="date_from"></label>
					<label>结束日期<input type="date" data-filter="date_to"></label>
					<button class="btn btn-primary tfo-query">查询</button>
					<button class="btn btn-default tfo-reset">重置</button>
				</section>
				<div class="tfo-currency-tabs"></div>
				<section class="tfo-kpis"></section>
				<section class="tfo-grid">
					<article class="tfo-card tfo-wide"><header><div><span>趋势</span><h3>结算、账务与广告</h3></div><small class="tfo-currency-label"></small></header><div id="tfo-daily" class="tfo-chart"></div></article>
					<article class="tfo-card"><header><div><span>区域</span><h3>区域资金对比</h3></div></header><div id="tfo-region" class="tfo-chart"></div></article>
					<article class="tfo-card"><header><div><span>交易构成</span><h3>结算交易类型</h3></div></header><div id="tfo-types" class="tfo-chart"></div></article>
				</section>
				<section class="tfo-card tfo-ledgers">
					<header><div><span>财务明细</span><h3>账务资金表现</h3></div><div class="tfo-ledger-actions"><div class="tfo-ledger-count"></div><button type="button" class="btn btn-default tfo-ledger-summary-open" aria-expanded="false">账务汇总</button></div></header>
					<div class="tfo-ledger-filters"><div class="tfo-ledger-filter-row"><span>账务类型</span><div class="tfo-ledger-types" role="group" aria-label="账务类型"></div></div><div class="tfo-ledger-filter-row"><span>备注</span><div class="tfo-ledger-remarks" role="group" aria-label="备注"></div></div></div>
					<div class="tfo-ledger-region-note" hidden>财务明细没有区域数据，此卡片不受页面顶部的区域筛选影响。</div>
					<div class="tfo-table-wrap"><table><thead><tr><th>账务时间</th><th>账务类型</th><th>备注</th><th>收入</th><th>支出</th><th>收支金额</th></tr></thead><tbody></tbody></table></div>
					<div class="tfo-ledger-summary-overlay" hidden><div class="tfo-ledger-summary-backdrop" data-close-ledger-summary></div><aside class="tfo-ledger-summary-drawer" role="dialog" aria-label="账务资金汇总"><div class="tfo-ledger-summary-heading"><h3>账务资金汇总</h3><button type="button" data-close-ledger-summary aria-label="关闭账务资金汇总">×</button></div><div class="tfo-ledger-summary-body"></div></aside></div>
				</section>
				<section class="tfo-counts"></section>
			</div>`);
	}

	bind() {
		this.$.on("click", "[data-month]", (event) => this.selectMonth(event.currentTarget.dataset.month));
		this.$.on("change", "[data-filter=date_from], [data-filter=date_to]", () => {
			this.selectedMonths.clear();
			this.monthSelectionStarted = false;
			this.renderMonthButtons();
		});
		this.$.on("click", ".tfo-query", () => this.load());
		this.$.on("click", ".tfo-reset", () => {
			this.$.find("[data-filter]").val("");
			this.availableMonths = [];
			this.selectedMonths.clear();
			this.monthSelectionStarted = false;
			this.initialMonthApplied = false;
			this.ledgerType = "";
			this.ledgerRemark = "";
			this.renderMonthButtons();
			this.load();
		});
		this.$.on("change", "[data-filter=batch]", (event) => {
			const batch = (this.data.options?.batches || []).find((row) => row.value === event.target.value);
			if (!batch) return;
			this.$.find("[data-filter=cost_center]").val(batch.cost_center || "");
			this.$.find("[data-filter=date_from]").val(batch.date_from || "");
			this.$.find("[data-filter=date_to]").val(batch.date_to || "");
			this.selectedMonths.clear();
			this.monthSelectionStarted = false;
			this.renderMonthButtons();
		});
		this.$.on("click", "[data-ledger-type]", (event) => {
			this.ledgerType = event.currentTarget.dataset.ledgerType;
			this.ledgerRemark = "";
			this.renderLedger(this.activeCurrency);
		});
		this.$.on("click", "[data-ledger-remark]", (event) => {
			this.ledgerRemark = event.currentTarget.dataset.ledgerRemark;
			this.renderLedger(this.activeCurrency);
		});
		this.$.on("click", ".tfo-ledger-summary-open", () => this.toggleLedgerSummary(!this.ledgerDrawerOpen));
		this.$.on("click", "[data-close-ledger-summary]", () => this.toggleLedgerSummary(false));
		this.$.on("click", ".tfo-ledger-summary-table [data-copy-ledger-column]", (event) => this.copyLedgerSummaryColumn(event.currentTarget));
		this.$.on("click", ".tfo-ledger-summary-table [data-copy-ledger-account]", (event) => this.copyLedgerAccount(event.currentTarget));
		this.$.on("click", ".tfo-ledger-summary-table [data-copy-ledger-cost-center]", (event) => this.copyLedgerCostCenter(event.currentTarget));
		this.$.on("keydown", ".tfo-ledger-summary-table [data-copy-ledger-column]", (event) => {
			if (event.key === "Enter" || event.key === " ") {
				event.preventDefault();
				this.copyLedgerSummaryColumn(event.currentTarget);
			}
		});
		this.$.on("keydown", ".tfo-ledger-summary-table [data-copy-ledger-account]", (event) => {
			if (event.key === "Enter" || event.key === " ") {
				event.preventDefault();
				this.copyLedgerAccount(event.currentTarget);
			}
		});
		this.$.on("keydown", ".tfo-ledger-summary-table [data-copy-ledger-cost-center]", (event) => {
			if (event.key === "Enter" || event.key === " ") {
				event.preventDefault();
				this.copyLedgerCostCenter(event.currentTarget);
			}
		});
		this.keyHandler = (event) => { if (event.key === "Escape" && this.ledgerDrawerOpen) this.toggleLedgerSummary(false); };
		document.addEventListener("keydown", this.keyHandler);
		this.$.on("click", ".tfo-currency-tabs button", (event) => {
			this.activeCurrency = $(event.currentTarget).data("currency");
			this.renderData();
		});
		this.resizeHandler = frappe.utils.debounce(() => {
			this.charts.forEach((chart) => chart.resize());
			if (this.ledgerDrawerOpen) this.positionLedgerSummary();
		}, 120);
		window.addEventListener("resize", this.resizeHandler);
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

	monthBounds(month) {
		const [year, value] = month.split("-").map(Number);
		const prefix = `${year}-${String(value).padStart(2, "0")}`;
		return { from: `${prefix}-01`, to: `${prefix}-${String(new Date(year, value, 0).getDate()).padStart(2, "0")}` };
	}

	renderMonthButtons() {
		this.$.find(".tfo-month-buttons").html(this.monthKeys().map((month) => {
			const [year, value] = month.split("-");
			return `<button type="button" data-month="${month}" class="tfo-month-button${this.selectedMonths.has(month) ? " is-active" : ""}" aria-pressed="${this.selectedMonths.has(month)}">${year}年${Number(value)}月</button>`;
		}).join(""));
	}

	selectLatestMonth() {
		const latest = this.availableMonths[this.availableMonths.length - 1];
		this.selectedMonths = latest ? new Set([latest]) : new Set();
		this.monthSelectionStarted = false;
		if (latest) {
			this.$.find("[data-filter=date_from]").val(this.monthBounds(latest).from);
			this.$.find("[data-filter=date_to]").val(this.monthBounds(latest).to);
		}
		this.renderMonthButtons();
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
		this.load();
	}

	filters() {
		const result = {};
		this.$.find("[data-filter]").each((_, element) => { result[element.dataset.filter] = element.value || ""; });
		return result;
	}

	async load() {
		const serial = ++this.loadSerial;
		this.$.addClass("is-loading");
		try {
			const response = await frappe.call({ method: `${TEMU_ANALYTICS_METHOD}.get_overview`, args: { filters: this.filters() } });
			if (serial !== this.loadSerial) return;
			this.data = response.message || {};
			this.availableMonths = this.data.options?.available_months || [];
			if (!this.initialMonthApplied) {
				this.initialMonthApplied = true;
				if (this.availableMonths.length && !this.filters().date_from && !this.filters().date_to) {
					this.selectLatestMonth();
					this.load();
					return;
				}
			}
			this.renderMonthButtons();
			this.fillOptions();
			const currencies = Object.keys(this.data.currencies || {});
			if (!currencies.includes(this.activeCurrency)) this.activeCurrency = this.filters().currency || currencies[0] || "未知";
			this.renderData();
			this.loaded = true;
		} catch (error) {
			if (serial !== this.loadSerial) return;
			console.error(error);
			frappe.msgprint({ title: "Temu财务总览加载失败", message: error.message || "请查看错误日志。", indicator: "red" });
		} finally {
			if (serial === this.loadSerial) this.$.removeClass("is-loading");
		}
	}

	refresh() { if (this.loaded) this.load(); }

	fillOptions() {
		const options = this.data.options || {};
		this.setOptions("batch", options.batches || [], "全部批次");
		this.setOptions("cost_center", options.cost_centers || [], "全部店铺");
		this.setOptions("region", options.regions || [], "全部区域");
		this.setOptions("currency", options.currencies || [], "全部币种");
	}

	setOptions(name, values, emptyLabel) {
		const select = this.$.find(`[data-filter=${name}]`);
		const old = select.val();
		select.html(`<option value="">${emptyLabel}</option>` + values.map((value) => {
			const item = typeof value === "object" ? value : { value, label: value };
			return `<option value="${this.esc(item.value)}">${this.esc(item.label)}</option>`;
		}).join(""));
		select.val(old);
	}

	renderData() {
		const currency = this.activeCurrency;
		const values = this.data.currencies?.[currency] || {};
		const cards = [
			["结算净额", values.settlement, "结算明细中的全部交易"],
			["待结算销售额", values.pending, "当前待处理文件"],
			["账务余额变化", values.ledger, "财务明细收支净额"],
			["广告花费", values.ad_spend, "每日店铺广告花费"],
			["广告销售额", values.ad_sales, `广告订单 ${this.num(values.ad_orders)} 笔`],
			["履约保障", values.protection, "赔付与履约保障金额"],
		];
		this.$.find(".tfo-kpis").html(cards.map(([label, value, note], index) => `<article class="tone-${index}"><span>${label}</span><b>${this.money(value, currency)}</b><small>${note}</small></article>`).join(""));
		const currencies = Object.keys(this.data.currencies || {});
		this.$.find(".tfo-currency-tabs").html(currencies.map((name) => `<button class="${name === currency ? "active" : ""}" data-currency="${this.esc(name)}">${this.esc(name)}</button>`).join(""));
		this.$.find(".tfo-currency-label").text(currency);
		this.renderCharts(currency);
		this.renderLedger(currency);
		this.renderCounts();
	}

	renderCharts(currency) {
		this.charts.forEach((chart) => chart.dispose());
		this.charts = [];
		const daily = (this.data.daily || []).filter((row) => row.currency === currency);
		const dates = daily.map((row) => row.date);
		this.chart("tfo-daily", {
			tooltip: { trigger: "axis" }, legend: { top: 4 }, grid: { left: 65, right: 35, top: 48, bottom: 42 },
			xAxis: { type: "category", data: dates }, yAxis: { type: "value" }, dataZoom: [{ type: "inside" }],
			series: [
				{ name: "结算净额", type: "bar", data: daily.map((row) => row.settlement || 0), itemStyle: { color: "#5b6cff", borderRadius: [6, 6, 0, 0] } },
				{ name: "账务余额", type: "line", smooth: true, showSymbol: false, data: daily.map((row) => row.ledger || 0), lineStyle: { width: 3, color: "#15a59a" } },
				{ name: "广告花费", type: "line", smooth: true, showSymbol: false, data: daily.map((row) => row.ad_spend || 0), lineStyle: { width: 2, color: "#f59e0b" } },
			],
		});
		const regions = (this.data.regions || []).filter((row) => row.currency === currency);
		this.chart("tfo-region", {
			tooltip: { trigger: "axis", valueFormatter: (value) => this.money(value, currency) }, legend: { top: 4 }, grid: { left: 70, right: 20, top: 48, bottom: 38 },
			xAxis: { type: "category", data: regions.map((row) => row.region) }, yAxis: { type: "value", axisLabel: { formatter: (value) => this.num(value) } },
			series: [
				{ name: "待结算", type: "bar", data: regions.map((row) => this.roundMoney(row.pending)), itemStyle: { color: "#8b9cff" } },
				{ name: "已结算", type: "bar", data: regions.map((row) => this.roundMoney(row.settled)), itemStyle: { color: "#22b8a7" } },
			],
		});
		const types = (this.data.transaction_types || []).filter((row) => row.currency === currency).sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount));
		this.chart("tfo-types", {
			tooltip: { trigger: "item", formatter: (p) => `${this.esc(p.name)}<br>${this.money(p.data.signed, currency)} · ${p.data.count}条` },
			legend: { type: "scroll", bottom: 2 },
			series: [{ type: "pie", radius: ["38%", "67%"], center: ["50%", "43%"], itemStyle: { borderColor: "#fff", borderWidth: 3, borderRadius: 7 }, data: types.map((row) => ({ name: row.name, value: Math.abs(row.amount), signed: row.amount, count: row.count })) }],
		});
	}

	chart(id, option) {
		const element = this.wrapper.querySelector(`#${id}`);
		if (!element || typeof echarts === "undefined") return null;
		const chart = echarts.init(element);
		chart.setOption(option);
		this.charts.push(chart);
		return chart;
	}

	renderLedger(currency) {
		this.renderLedgerFilters(currency);
		const rows = (this.data.ledger_details || []).filter((row) => row.currency === currency
			&& (!this.ledgerType || row.accounting_type === this.ledgerType)
			&& this.matchesLedgerRemark(row.remark_group));
		this.$.find(".tfo-ledger-count").text(`${this.num(rows.length)} 笔财务明细`);
		this.$.find(".tfo-ledger-region-note").prop("hidden", !this.filters().region);
		this.$.find(".tfo-ledgers tbody").html(rows.map((row) => `
			<tr><td>${this.esc(row.accounting_time || "—")}</td><td>${this.esc(row.accounting_type)}</td><td>${this.esc(row.remarks || "无备注")}</td>
			<td class="positive">${this.money(Math.max(Number(row.amount || 0), 0), currency)}</td>
			<td class="negative">${this.money(Math.min(Number(row.amount || 0), 0), currency)}</td>
			<td class="${row.amount < 0 ? "negative" : "positive"}">${this.money(row.amount, currency)}</td>
			</tr>`).join("") || `<tr><td colspan="6" class="tfo-empty">当前筛选范围没有财务明细</td></tr>`);
		const summaryRows = (this.data.ledger_groups || []).filter((row) => row.currency === currency
			&& (!this.ledgerType || row.accounting_type === this.ledgerType)
			&& this.matchesLedgerRemark(row.remarks));
		this.renderLedgerSummary(summaryRows, currency);
	}

	matchesLedgerRemark(remark) {
		if (!this.ledgerRemark) return true;
		return this.ledgerRemark === STORAGE_EPR_REMARK
			? STORAGE_EPR_REMARKS.has(remark)
			: remark === this.ledgerRemark;
	}

	renderLedgerFilters(currency) {
		const button = (value, label, selected, attribute) => `<button type="button" class="tfo-ledger-filter-button${selected === value ? " is-active" : ""}" ${attribute}="${this.esc(value)}" aria-pressed="${selected === value}" title="${this.esc(label)}">${this.esc(label)}</button>`;
		const rows = (this.data.ledger_groups || []).filter((row) => row.currency === currency);
		const types = [...new Set(rows.map((row) => row.accounting_type))].sort((a, b) => a.localeCompare(b, "zh-CN"));
		if (this.ledgerType && !types.includes(this.ledgerType)) this.ledgerType = "";
		this.$.find(".tfo-ledger-types").html(button("", "全部类型", this.ledgerType, "data-ledger-type")
			+ types.map((type) => button(type, type, this.ledgerType, "data-ledger-type")).join(""));
		const remarks = [...new Set(rows.filter((row) => !this.ledgerType || row.accounting_type === this.ledgerType)
			.map((row) => STORAGE_EPR_REMARKS.has(row.remarks) ? STORAGE_EPR_REMARK : row.remarks))]
			.sort((a, b) => (a === STORAGE_EPR_REMARK ? "仓储与EPR费用" : a)
				.localeCompare(b === STORAGE_EPR_REMARK ? "仓储与EPR费用" : b, "zh-CN"));
		if (this.ledgerRemark && !remarks.includes(this.ledgerRemark)) this.ledgerRemark = "";
		this.$.find(".tfo-ledger-remarks").html(button("", "全部备注", this.ledgerRemark, "data-ledger-remark")
			+ remarks.map((remark) => button(remark, remark === STORAGE_EPR_REMARK ? "仓储与EPR费用" : remark, this.ledgerRemark, "data-ledger-remark")).join(""));
	}

	renderLedgerSummary(rows, currency) {
		const totals = { income: 0, expense: 0, amount: 0, count: 0 };
		const types = new Map();
		for (const row of rows) {
			for (const key of Object.keys(totals)) totals[key] += Number(row[key] || 0);
			if (!types.has(row.accounting_type)) types.set(row.accounting_type, { income: 0, expense: 0, amount: 0, count: 0, remarks: [] });
			const group = types.get(row.accounting_type);
			for (const key of Object.keys(totals)) group[key] += Number(row[key] || 0);
			group.remarks.push(row);
		}
		this.ledgerSummaryDisplayRows = [...types.values()].flatMap((group) => group.remarks);
		this.$.find(".tfo-ledger-summary-body").html(`
			<div class="tfo-ledger-summary-stats"><b>${this.num(types.size)} 类账务 · ${this.num(rows.length)} 类备注</b><span>${this.num(totals.count)} 笔财务明细</span></div>
			<div class="tfo-ledger-summary-totals"><b>收支净额 ${this.money(totals.amount, currency)}</b></div>
			<div class="tfo-ledger-summary-list">${[...types].map(([type, group]) => `
				<article><div class="tfo-ledger-summary-title"><b>${this.esc(type)}</b><span>${this.num(group.count)} 笔 · 净额 ${this.money(group.amount, currency)}</span></div>
				<table class="tfo-ledger-summary-table"><colgroup><col class="tfo-ledger-summary-cost-center-col"><col class="tfo-ledger-summary-account-col"><col class="tfo-ledger-summary-amount-col"><col class="tfo-ledger-summary-remark-col"><col class="tfo-ledger-summary-count-col"></colgroup>
				<thead><tr><th scope="col" data-copy-ledger-column="cost_center" role="button" tabindex="0" title="点击复制成本中心整列">成本中心 ⧉</th><th scope="col" data-copy-ledger-column="account" role="button" tabindex="0" title="点击复制科目整列">科目 ⧉</th><th scope="col" data-copy-ledger-column="amount" role="button" tabindex="0" title="点击复制借方金额整列">金额（借） ⧉</th><th scope="col" data-copy-ledger-column="remarks" role="button" tabindex="0" title="点击复制以分号分隔的摘要">摘要 ⧉</th><th scope="col">笔数</th></tr></thead>
				<tbody>${group.remarks.map((row) => {
					const account = this.suggestLedgerAccount(row);
					return `<tr><td data-copy-ledger-cost-center="${this.esc(row.cost_center || "未指定成本中心")}" role="button" tabindex="0" title="点击复制成本中心：${this.esc(row.cost_center || "未指定成本中心")}">${this.esc(row.cost_center || "未指定成本中心")}</td><td data-copy-ledger-account="${this.esc(account.label)}" role="button" tabindex="0" title="${this.esc(account.title)} 点击复制科目">${this.esc(account.label)}</td><td>${this.money(this.ledgerDebitAmount(row), currency)}</td><td title="${this.esc(row.remarks)}">${this.esc(row.remarks)}</td><td>${this.num(row.count)}</td></tr>`;
				}).join("")}</tbody></table>
				</article>`).join("") || `<div class="tfo-empty">当前条件下没有财务明细</div>`}</div>
		`);
		if (this.ledgerDrawerOpen) this.positionLedgerSummary();
	}

	suggestLedgerAccount(row) {
		if (row.accounting_type === "支出") {
			const accounts = {
				"推广服务费": ["560106_fj - 平台广告费【销售费用】", "平台广告费；实际付款还需核对平台钱包科目。"],
				"仓储综合服务费": ["540102_fj - 国内物流【主营业务成本】 - 丰境", "按指定的国内物流科目显示；入账前请核对已分摊和未分摊的部分。"],
				"合规EPR环保费": ["560109_fj - 平台合规费【销售费用】 - 丰境", "平台合规费。"],
				"合规EPR物流包装环保费": ["560109_fj - 平台合规费【销售费用】 - 丰境", "平台合规费。"],
				"消费者及履约保障-售后问题": ["560108_fj - 平台售后费【销售费用】", "平台售后费。"],
			};
			const account = accounts[row.remarks];
			if (account) return { label: account[0], title: account[1] };
		}
		if (row.accounting_type === "结算") return {
			label: "101201_fj - temu_全托管_平台(可提现)【其他货币资金】",
			title: "结算转入平台钱包的借方科目；应收账款是对应贷方，请核对销售发票。",
		};
		if (row.accounting_type === "提现") return {
			label: "按实际到账银行科目",
			title: "提现需根据实际收款银行账户选择科目。",
		};
		return { label: "待确认", title: "源账务文件没有科目字段，请按实际业务确定。" };
	}

	ledgerDebitAmount(row) {
		return Math.abs(Number(row.amount || 0));
	}

	async copyLedgerCostCenter(cell) {
		const costCenter = cell.dataset.copyLedgerCostCenter;
		if (!costCenter) return;
		try {
			await navigator.clipboard.writeText(costCenter);
			frappe.show_alert({ message: "已复制成本中心", indicator: "green" });
		} catch (error) {
			frappe.show_alert({ message: "复制失败，请检查浏览器剪贴板权限", indicator: "red" });
		}
	}

	async copyLedgerAccount(cell) {
		const account = cell.dataset.copyLedgerAccount;
		if (!account) return;
		try {
			await navigator.clipboard.writeText(account);
			frappe.show_alert({ message: "已复制科目", indicator: "green" });
		} catch (error) {
			frappe.show_alert({ message: "复制失败，请检查浏览器剪贴板权限", indicator: "red" });
		}
	}

	async copyLedgerSummaryColumn(heading) {
		const rows = this.ledgerSummaryDisplayRows || [];
		if (!rows.length) return;
		const column = heading.dataset.copyLedgerColumn;
		if (column !== "cost_center" && column !== "account" && column !== "remarks" && column !== "amount") return;
		const content = rows.map((row) => column === "cost_center"
			? String(row.cost_center || "未指定成本中心")
			: column === "account"
				? this.suggestLedgerAccount(row).label
				: column === "remarks"
					? String(row.remarks || "无备注").replace(/[\r\n\t]+/g, " ")
					: this.ledgerDebitAmount(row).toFixed(2)).join(column === "remarks" ? ";" : "\n");
		try {
			await navigator.clipboard.writeText(content);
			frappe.show_alert({ message: column === "remarks" ? `已复制 ${rows.length} 条摘要，以分号分隔` : `已复制${column === "cost_center" ? "成本中心" : column === "account" ? "科目" : "借方金额"} ${rows.length} 行`, indicator: "green" });
		} catch (error) {
			frappe.show_alert({ message: "复制失败，请检查浏览器剪贴板权限", indicator: "red" });
		}
	}

	toggleLedgerSummary(open) {
		this.ledgerDrawerOpen = open;
		this.$.find(".tfo-ledger-summary-overlay").prop("hidden", !open);
		this.$.find(".tfo-ledger-summary-open").attr("aria-expanded", String(open));
		if (open) this.positionLedgerSummary();
		else this.$.find(".tfo-ledgers").css("min-height", "");
	}

	positionLedgerSummary() {
		const card = this.$.find(".tfo-ledgers")[0];
		const heading = this.$.find(".tfo-ledgers thead")[0];
		const drawer = this.$.find(".tfo-ledger-summary-drawer")[0];
		if (!card || !heading || !drawer) return;
		const top = Math.max(0, heading.getBoundingClientRect().bottom - card.getBoundingClientRect().top);
		this.$.find(".tfo-ledger-summary-overlay").css("top", `${Math.round(top)}px`);
		card.style.minHeight = `${Math.ceil(top + drawer.scrollHeight + 16)}px`;
	}

	renderCounts() {
		const labels = { pending: "待结算", transactions: "结算交易", protections: "履约保障", ledgers: "财务流水", ad_store: "广告日报", ad_reconciliation: "广告对账", ad_payments: "广告付款" };
		this.$.find(".tfo-counts").html(Object.entries(this.data.counts || {}).map(([key, value]) => `<span>${labels[key] || key}<b>${this.num(value)}</b></span>`).join(""));
	}

	money(value, currency) { return `${this.esc(currency || "")} ${Number(value || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`; }
	roundMoney(value) { return Number(Number(value || 0).toFixed(2)); }
	num(value) { return Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 2 }); }
	esc(value) { return frappe.utils.escape_html(String(value ?? "")); }
}
