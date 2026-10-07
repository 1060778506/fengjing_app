const TEMU_AD_METHOD =
	"fengjing_app.fengjing_business.page.temu_managed_bookkee.temu_financial_analytics.get_ad_analysis";

frappe.pages["temu_ad_analysis"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: "Temu广告数据分析", single_column: true });
	frappe.require("/assets/fengjing_app/js/图表-echarts.js", () => {
		wrapper.temuAdAnalysis = new TemuAdAnalysisPage(wrapper, page);
	});
};

frappe.pages["temu_ad_analysis"].on_page_show = function (wrapper) {
	wrapper.temuAdAnalysis?.refresh();
};

class TemuAdAnalysisPage {
	constructor(wrapper, page) {
		this.wrapper = wrapper;
		this.page = page;
		this.pageNo = 1;
		this.data = {};
		this.charts = [];
		this.loaded = false;
		this.render();
		this.bind();
		this.load();
	}

	render() {
		$(this.wrapper).addClass("taa-page");
		this.$ = $(this.page.main).html(`
			<div class="taa-shell">
				<section class="taa-hero"><div><span>TEMU ADVERTISING</span><h2>广告投入、销售与付款分析</h2><p>连接店铺日报、商品报表、对账单和已支付流水。</p></div><nav><a href="/app/temu_finance_overvi">财务总览</a><a href="/app/temu_reconciliation">对账明细</a><a href="/app/temu_managed_bookkee">文件批次</a></nav></section>
				<section class="taa-filters">
					<label>批次<select data-filter="batch"><option value="">全部批次</option></select></label>
					<label>店铺（成本中心）<select data-filter="cost_center"><option value="">全部店铺</option></select></label>
					<label>币种<select data-filter="currency"><option value="">全部币种</option></select></label>
					<label>物料绑定<select data-filter="binding"><option value="">全部</option><option value="bound">已绑定</option><option value="unbound">未绑定</option></select></label>
					<label>开始日期<input type="date" data-filter="date_from"></label><label>结束日期<input type="date" data-filter="date_to"></label>
					<label class="taa-search">商品、商品ID或物料<input data-filter="search" placeholder="输入关键词"></label>
					<button class="btn btn-primary taa-query">查询</button><button class="btn btn-default taa-reset">重置</button>
				</section>
				<div class="taa-currency-tabs"></div>
				<section class="taa-kpis"></section>
				<section class="taa-grid">
					<article class="taa-card taa-wide"><header><div><span>经营趋势</span><h3>广告花费与申报价销售额</h3></div><small class="taa-currency-label"></small></header><div id="taa-trend" class="taa-chart"></div></article>
					<article class="taa-card"><header><div><span>效果漏斗</span><h3>曝光、点击与订单</h3></div></header><div id="taa-funnel" class="taa-chart"></div></article>
					<article class="taa-card"><header><div><span>支付流水</span><h3>广告资金流水构成</h3></div></header><div id="taa-flow" class="taa-chart"></div></article>
				</section>
				<section class="taa-card taa-products"><header><div><span>商品表现</span><h3>广告商品明细</h3></div><div class="taa-page-info"></div></header><div class="taa-table-wrap"><table><thead><tr><th>商品 / ERPNext物料</th><th>花费</th><th>销售额</th><th>ROAS</th><th>曝光 / 点击</th><th>订单 / 件数</th><th>转化率</th></tr></thead><tbody></tbody></table></div><footer><button class="btn btn-default taa-prev">上一页</button><button class="btn btn-default taa-next">下一页</button></footer></section>
				<section class="taa-counts"></section>
			</div>`);
	}

	bind() {
		this.$.on("click", ".taa-query", () => { this.pageNo = 1; this.load(); });
		this.$.on("keyup", "[data-filter=search]", (event) => { if (event.key === "Enter") { this.pageNo = 1; this.load(); } });
		this.$.on("click", ".taa-reset", () => { this.$.find("[data-filter]").val(""); this.pageNo = 1; this.load(); });
		this.$.on("click", ".taa-prev", () => { if (this.pageNo > 1) { this.pageNo -= 1; this.load(); } });
		this.$.on("click", ".taa-next", () => { if (this.pageNo < (this.data.pages || 1)) { this.pageNo += 1; this.load(); } });
		this.$.on("click", ".taa-currency-tabs button", (event) => { this.activeCurrency = $(event.currentTarget).data("currency"); this.renderData(); });
		this.$.on("change", "[data-filter=batch]", (event) => {
			const batch = (this.data.options?.batches || []).find((row) => row.value === event.target.value);
			if (!batch) return;
			this.$.find("[data-filter=cost_center]").val(batch.cost_center || "");
			this.$.find("[data-filter=date_from]").val(batch.date_from || "");
			this.$.find("[data-filter=date_to]").val(batch.date_to || "");
		});
		window.addEventListener("resize", frappe.utils.debounce(() => this.charts.forEach((chart) => chart.resize()), 120));
	}

	filters() { const result = {}; this.$.find("[data-filter]").each((_, element) => { result[element.dataset.filter] = element.value || ""; }); return result; }

	async load() {
		this.$.addClass("is-loading");
		try {
			const response = await frappe.call({ method: TEMU_AD_METHOD, args: { filters: this.filters(), page: this.pageNo, page_size: 50 } });
			this.data = response.message || {};
			this.pageNo = this.data.page || 1;
			this.fillOptions();
			const currencies = Object.keys(this.data.currencies || {});
			if (!currencies.includes(this.activeCurrency)) this.activeCurrency = this.filters().currency || currencies[0] || "未知";
			this.renderData();
			this.loaded = true;
		} catch (error) {
			console.error(error); frappe.msgprint({ title: "Temu广告分析加载失败", message: error.message || "请查看错误日志。", indicator: "red" });
		} finally { this.$.removeClass("is-loading"); }
	}

	refresh() { if (this.loaded) this.load(); }

	fillOptions() {
		const o = this.data.options || {};
		this.setOptions("batch", o.batches || [], "全部批次");
		this.setOptions("cost_center", o.cost_centers || [], "全部店铺");
		this.setOptions("currency", o.currencies || [], "全部币种");
	}

	setOptions(name, values, empty) {
		const select = this.$.find(`[data-filter=${name}]`), old = select.val();
		select.html(`<option value="">${empty}</option>` + values.map((value) => { const item = typeof value === "object" ? value : { value, label: value }; return `<option value="${this.esc(item.value)}">${this.esc(item.label)}</option>`; }).join(""));
		select.val(old);
	}

	renderData() {
		const currency = this.activeCurrency;
		const v = this.data.currencies?.[currency] || {};
		const roas = Number(v.total_spend || 0) ? Number(v.declared_sales_amount || 0) / Number(v.total_spend || 1) : 0;
		const ctr = Number(v.impressions || 0) ? Number(v.clicks || 0) * 100 / Number(v.impressions || 1) : 0;
		const conversion = Number(v.clicks || 0) ? Number(v.suborder_count || 0) * 100 / Number(v.clicks || 1) : 0;
		const cards = [
			["广告花费", this.money(v.total_spend, currency), `净花费 ${this.money(v.net_total_spend, currency)}`],
			["申报价销售额", this.money(v.declared_sales_amount, currency), `${this.num(v.suborder_count)} 个子订单`],
			["ROAS", this.num(roas), "销售额 ÷ 广告花费"],
			["点击率", `${this.num(ctr)}%`, `${this.num(v.clicks)} / ${this.num(v.impressions)}`],
			["点击转化率", `${this.num(conversion)}%`, `${this.num(v.suborder_count)} 笔订单`],
			["已付款金额", this.money(v.period_paid_amount, currency), `流水 ${this.money(v.payment_flow, currency)}`],
		];
		this.$.find(".taa-kpis").html(cards.map(([label, value, note], i) => `<article class="k${i}"><span>${label}</span><b>${value}</b><small>${note}</small></article>`).join(""));
		const currencies = Object.keys(this.data.currencies || {});
		this.$.find(".taa-currency-tabs").html(currencies.map((name) => `<button class="${name === currency ? "active" : ""}" data-currency="${this.esc(name)}">${this.esc(name)}</button>`).join(""));
		this.$.find(".taa-currency-label").text(currency);
		this.renderCharts(currency, v);
		this.renderProducts(currency);
		this.renderCounts();
	}

	renderCharts(currency, totals) {
		this.charts.forEach((chart) => chart.dispose()); this.charts = [];
		const daily = (this.data.daily || []).filter((row) => row.currency === currency);
		this.chart("taa-trend", { tooltip: { trigger: "axis" }, legend: { top: 4 }, grid: { left: 68, right: 48, top: 48, bottom: 42 }, xAxis: { type: "category", data: daily.map((row) => row.date) }, yAxis: [{ type: "value", name: "金额" }, { type: "value", name: "ROAS" }], dataZoom: [{ type: "inside" }], series: [{ name: "广告花费", type: "bar", data: daily.map((row) => row.total_spend || 0), itemStyle: { color: "#ff815c", borderRadius: [6, 6, 0, 0] } }, { name: "申报价销售额", type: "line", smooth: true, showSymbol: false, data: daily.map((row) => row.declared_sales_amount || 0), lineStyle: { width: 3, color: "#5159c9" } }, { name: "每日ROAS", type: "line", yAxisIndex: 1, smooth: true, showSymbol: false, data: daily.map((row) => Number(row.total_spend || 0) ? Number(row.declared_sales_amount || 0) / Number(row.total_spend) : 0), lineStyle: { width: 2, color: "#16a28f" } }] });
		const funnel = [{ name: "曝光", value: totals.impressions || 0 }, { name: "点击", value: totals.clicks || 0 }, { name: "加购", value: totals.add_to_cart_count || 0 }, { name: "订单", value: totals.suborder_count || 0 }];
		this.chart("taa-funnel", { tooltip: { trigger: "item" }, series: [{ type: "funnel", left: "12%", top: 25, bottom: 20, width: "76%", minSize: "18%", maxSize: "100%", sort: "descending", gap: 3, label: { formatter: "{b}  {c}" }, itemStyle: { borderColor: "#fff", borderWidth: 2 }, data: funnel }] });
		const flows = (this.data.flows || []).filter((row) => row.currency === currency);
		this.chart("taa-flow", { tooltip: { trigger: "item", formatter: (p) => `${this.esc(p.name)}<br>${this.money(p.data.signed, currency)} · ${p.data.count}条` }, legend: { type: "scroll", bottom: 3 }, series: [{ type: "pie", radius: ["38%", "67%"], center: ["50%", "43%"], itemStyle: { borderColor: "#fff", borderWidth: 3, borderRadius: 7 }, data: flows.map((row) => ({ name: row.name, value: Math.abs(row.amount), signed: row.amount, count: row.count })) }] });
	}

	chart(id, option) { const element = this.wrapper.querySelector(`#${id}`); if (!element || typeof echarts === "undefined") return; const chart = echarts.init(element); chart.setOption(option); this.charts.push(chart); }

	renderProducts(currency) {
		const rows = (this.data.products || []).filter((row) => !currency || row.currency === currency);
		this.$.find(".taa-page-info").text(`共 ${this.num(this.data.total)} 个商品 · 第 ${this.data.page || 1}/${this.data.pages || 1} 页`);
		this.$.find(".taa-prev").prop("disabled", (this.data.page || 1) <= 1); this.$.find(".taa-next").prop("disabled", (this.data.page || 1) >= (this.data.pages || 1));
		this.$.find(".taa-products tbody").html(rows.map((row) => {
			const items = Array.isArray(row.items) ? row.items : [];
			const item = items.length
				? `<div class="taa-linked-items"><strong>${row.is_multi_item ? `关联 ${items.length} 个ERPNext物料` : "关联ERPNext物料"}<small>${this.esc(row.mapping_basis || "SPU")}</small></strong>${items.map((value) => `<a href="/app/item/${encodeURIComponent(value.item_code)}"><b>${this.esc(value.item_code)}</b><span>${this.esc(value.item_name || "未命名物料")}</span></a>`).join("")}</div>`
				: `<em>未绑定ERPNext物料</em>`;
			const preview = row.item_image ? `<img src="${this.esc(row.item_image)}" loading="lazy">` : `<i>${items.length > 1 ? items.length : "AD"}</i>`;
			return `<tr><td><div class="taa-product">${preview}<div class="taa-product-copy"><b>${this.esc(row.product_name || row.product_id)}</b><small>商品ID ${this.esc(row.product_id || "—")} · SPU ${this.esc(row.spu_id || "—")}</small>${item}</div></div></td><td>${this.money(row.spend, row.currency)}</td><td>${this.money(row.sales, row.currency)}</td><td><b>${this.num(row.roas)}</b></td><td>${this.num(row.impressions)}<small>${this.num(row.clicks)} 次点击</small></td><td>${this.num(row.orders)}<small>${this.num(row.units)} 件</small></td><td>${this.num(row.conversion_rate)}%</td></tr>`;
		}).join("") || `<tr><td colspan="7" class="taa-empty">当前条件没有广告商品数据</td></tr>`);
	}

	renderCounts() {
		const labels = { store_daily: "店铺日报", products: "商品记录", reconciliation: "广告对账", payments: "付款流水", unbound_products: "未绑定商品" };
		this.$.find(".taa-counts").html(Object.entries(this.data.counts || {}).map(([key, value]) => `<span>${labels[key] || key}<b>${this.num(value)}</b></span>`).join(""));
	}

	money(value, currency) { return `${this.esc(currency || "")} ${Number(value || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`; }
	num(value) { return Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 2 }); }
	esc(value) { return frappe.utils.escape_html(String(value ?? "")); }
}
