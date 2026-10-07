const TEMU_ANALYTICS_METHOD =
	"fengjing_app.fengjing_business.page.temu_managed_bookkee.temu_financial_analytics";

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
		this.render();
		this.bind();
		this.load();
	}

	render() {
		$(this.wrapper).addClass("tfo-page");
		this.$ = $(this.page.main).html(`
			<div class="tfo-shell">
				<section class="tfo-hero">
					<div><span>TEMU FINANCE</span><h2>资金、结算与商品表现</h2><p>从已保存的对账批次读取数据，金额始终按币种分别统计。</p></div>
					<nav><a href="/app/temu_reconciliation">财务对账明细</a><a href="/app/temu_ad_analysis">广告数据分析</a><a href="/app/temu_managed_bookkee">文件批次</a></nav>
				</section>
				<section class="tfo-filters">
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
				<section class="tfo-card tfo-products"><header><div><span>商品</span><h3>商品资金表现</h3></div><div class="tfo-map-rate"></div></header><div class="tfo-table-wrap"><table><thead><tr><th>商品 / ERPNext物料</th><th>区域</th><th>待结算</th><th>已结算净额</th><th>数量</th></tr></thead><tbody></tbody></table></div></section>
				<section class="tfo-counts"></section>
			</div>`);
	}

	bind() {
		this.$.on("click", ".tfo-query", () => this.load());
		this.$.on("click", ".tfo-reset", () => {
			this.$.find("[data-filter]").val("");
			this.load();
		});
		this.$.on("change", "[data-filter=batch]", (event) => {
			const batch = (this.data.options?.batches || []).find((row) => row.value === event.target.value);
			if (!batch) return;
			this.$.find("[data-filter=cost_center]").val(batch.cost_center || "");
			this.$.find("[data-filter=date_from]").val(batch.date_from || "");
			this.$.find("[data-filter=date_to]").val(batch.date_to || "");
		});
		this.$.on("click", ".tfo-currency-tabs button", (event) => {
			this.activeCurrency = $(event.currentTarget).data("currency");
			this.renderData();
		});
		this.resizeHandler = frappe.utils.debounce(() => this.charts.forEach((chart) => chart.resize()), 120);
		window.addEventListener("resize", this.resizeHandler);
	}

	filters() {
		const result = {};
		this.$.find("[data-filter]").each((_, element) => { result[element.dataset.filter] = element.value || ""; });
		return result;
	}

	async load() {
		this.$.addClass("is-loading");
		try {
			const response = await frappe.call({ method: `${TEMU_ANALYTICS_METHOD}.get_overview`, args: { filters: this.filters() } });
			this.data = response.message || {};
			this.fillOptions();
			const currencies = Object.keys(this.data.currencies || {});
			if (!currencies.includes(this.activeCurrency)) this.activeCurrency = this.filters().currency || currencies[0] || "未知";
			this.renderData();
			this.loaded = true;
		} catch (error) {
			console.error(error);
			frappe.msgprint({ title: "Temu财务总览加载失败", message: error.message || "请查看错误日志。", indicator: "red" });
		} finally {
			this.$.removeClass("is-loading");
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
		this.renderProducts(currency);
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
			tooltip: { trigger: "axis" }, legend: { top: 4 }, grid: { left: 55, right: 20, top: 48, bottom: 38 },
			xAxis: { type: "category", data: regions.map((row) => row.region) }, yAxis: { type: "value" },
			series: [
				{ name: "待结算", type: "bar", data: regions.map((row) => row.pending || 0), itemStyle: { color: "#8b9cff" } },
				{ name: "已结算", type: "bar", data: regions.map((row) => row.settled || 0), itemStyle: { color: "#22b8a7" } },
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

	renderProducts(currency) {
		const rows = (this.data.products || []).filter((row) => row.currency === currency).slice(0, 20);
		this.$.find(".tfo-map-rate").html(`物料绑定率 <b>${this.num(this.data.mapping?.rate)}%</b> · ${this.num(this.data.mapping?.bound)}/${this.num(this.data.mapping?.total)}`);
		this.$.find(".tfo-products tbody").html(rows.map((row) => {
			const image = row.item_image ? `<img src="${this.esc(row.item_image)}" loading="lazy">` : `<i>${row.is_bound ? "物" : "?"}</i>`;
			const item = row.is_bound ? `<a href="/app/item/${encodeURIComponent(row.item_code)}">${this.esc(row.item_code)}</a><small>${this.esc(row.item_name)}</small>` : `<em>未绑定ERPNext物料</em>`;
			return `<tr><td><div class="tfo-product">${image}<span><b>${this.esc(row.product_name || row.sku)}</b><small>SKU ${this.esc(row.sku)} · ${this.esc(row.sku_code || "无货号")}</small>${item}</span></div></td><td>${this.esc(row.region || "未分类")}</td><td>${this.money(row.pending, currency)}</td><td class="${row.settled >= 0 ? "positive" : "negative"}">${this.money(row.settled, currency)}</td><td>${this.num(row.quantity)}</td></tr>`;
		}).join("") || `<tr><td colspan="5" class="tfo-empty">当前筛选范围没有商品数据</td></tr>`);
	}

	renderCounts() {
		const labels = { pending: "待结算", transactions: "结算交易", protections: "履约保障", ledgers: "财务流水", ad_store: "广告日报", ad_reconciliation: "广告对账", ad_payments: "广告付款" };
		this.$.find(".tfo-counts").html(Object.entries(this.data.counts || {}).map(([key, value]) => `<span>${labels[key] || key}<b>${this.num(value)}</b></span>`).join(""));
	}

	money(value, currency) { return `${this.esc(currency || "")} ${Number(value || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`; }
	num(value) { return Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 2 }); }
	esc(value) { return frappe.utils.escape_html(String(value ?? "")); }
}
