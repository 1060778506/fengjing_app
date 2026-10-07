const TEMU_RECONCILIATION_METHOD =
	"fengjing_app.fengjing_business.page.temu_managed_bookkee.temu_financial_analytics.get_reconciliation";

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
		this.data = {};
		this.chartInstance = null;
		this.loaded = false;
		this.render();
		this.bind();
		this.load();
	}

	render() {
		$(this.wrapper).addClass("trc-page");
		this.$ = $(this.page.main).html(`
			<div class="trc-shell">
				<section class="trc-hero"><div><span>TEMU RECONCILIATION</span><h2>待结算、结算与履约保障核对</h2><p>按批次、区域和SKU对照资金，并检查ERPNext物料绑定。</p></div><nav><a href="/app/temu_finance_overvi">财务总览</a><a href="/app/temu_ad_analysis">广告分析</a><a href="/app/temu_managed_bookkee">文件批次</a></nav></section>
				<section class="trc-filters">
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
				<section class="trc-card trc-chart-card"><header><div><span>差异观察</span><h3>当前页SKU资金差异</h3></div><small>已结算 + 履约保障 − 待结算</small></header><div id="trc-difference" class="trc-chart"></div></section>
				<section class="trc-card"><header><div><span>SKU汇总</span><h3>待结算与结算对照</h3></div><div class="trc-page-info"></div></header><div class="trc-table-wrap"><table class="trc-summary-table"><thead><tr><th>商品 / 对应物料</th><th>区域</th><th>状态</th><th>待结算</th><th>结算净额</th><th>履约保障</th><th>差异</th><th>交易</th></tr></thead><tbody></tbody></table></div><footer><button class="btn btn-default trc-prev">上一页</button><button class="btn btn-default trc-next">下一页</button></footer></section>
				<section class="trc-card"><header><div><span>交易明细</span><h3>最近结算交易</h3></div><small>最多显示当前筛选范围最近200条</small></header><div class="trc-table-wrap"><table class="trc-detail-table"><thead><tr><th>账务时间</th><th>订单</th><th>商品 / 物料</th><th>区域</th><th>交易类型</th><th>数量</th><th>金额</th></tr></thead><tbody></tbody></table></div></section>
			</div>`);
	}

	bind() {
		this.$.on("click", ".trc-query", () => { this.pageNo = 1; this.load(); });
		this.$.on("keyup", "[data-filter=search]", (event) => { if (event.key === "Enter") { this.pageNo = 1; this.load(); } });
		this.$.on("click", ".trc-reset", () => { this.$.find("[data-filter]").val(""); this.pageNo = 1; this.load(); });
		this.$.on("click", ".trc-prev", () => { if (this.pageNo > 1) { this.pageNo -= 1; this.load(); } });
		this.$.on("click", ".trc-next", () => { if (this.pageNo < (this.data.pages || 1)) { this.pageNo += 1; this.load(); } });
		this.$.on("change", "[data-filter=batch]", (event) => {
			const batch = (this.data.options?.batches || []).find((row) => row.value === event.target.value);
			if (batch) { this.$.find("[data-filter=date_from]").val(batch.date_from || ""); this.$.find("[data-filter=date_to]").val(batch.date_to || ""); }
		});
		window.addEventListener("resize", frappe.utils.debounce(() => this.chartInstance?.resize(), 120));
	}

	filters() { const result = {}; this.$.find("[data-filter]").each((_, element) => { result[element.dataset.filter] = element.value || ""; }); return result; }

	async load() {
		this.$.addClass("is-loading");
		try {
			const response = await frappe.call({ method: TEMU_RECONCILIATION_METHOD, args: { filters: this.filters(), page: this.pageNo, page_size: 50 } });
			this.data = response.message || {};
			this.pageNo = this.data.page || 1;
			this.fillOptions();
			this.renderData();
			this.loaded = true;
		} catch (error) {
			console.error(error); frappe.msgprint({ title: "Temu对账明细加载失败", message: error.message || "请查看错误日志。", indicator: "red" });
		} finally { this.$.removeClass("is-loading"); }
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
		this.renderDetails();
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

	renderDetails() {
		this.$.find(".trc-detail-table tbody").html((this.data.details || []).map((row) => {
			const item = row.is_bound ? `<a href="/app/item/${encodeURIComponent(row.item_code)}">${this.esc(row.item_code)}</a>` : `<em>未绑定</em>`;
			return `<tr><td>${this.esc(String(row.accounting_time || "").replace("T", " ").slice(0, 19))}</td><td>${this.esc(row.order_number || "—")}</td><td><b>${this.esc(row.product_name || row.sku)}</b><small>SKU ${this.esc(row.sku || "—")} · ${item}</small></td><td>${this.esc(row.region || "—")}</td><td>${this.esc(row.transaction_type || "—")}</td><td>${this.num(row.quantity)}</td><td class="${row.amount < 0 ? "negative" : "positive"}">${this.money(row.amount, row.currency)}</td></tr>`;
		}).join("") || `<tr><td colspan="7" class="trc-empty">暂无结算交易明细</td></tr>`);
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
