const TEMU_AD_METHOD =
	"fengjing_app.fengjing_business.page.temu_managed_bookkee.temu_financial_analytics.get_ad_analysis";
const TEMU_AD_ACCOUNT = "560106_fj - 平台广告费【销售费用】 - 丰境";
const TEMU_AD_SUMMARY = "广告费";

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
		this.availableMonths = [];
		this.selectedMonths = new Set();
		this.monthSelectionStarted = false;
		this.initialMonthApplied = false;
		this.drawerOpen = false;
		this.loadSerial = 0;
		this.loaded = false;
		this.render();
		this.bind();
		this.renderMonthButtons();
		this.load();
	}

	render() {
		$(this.wrapper).addClass("taa-page");
		this.$ = $(this.page.main).html(`
			<div class="taa-shell">
				<section class="taa-hero"><div><span>TEMU ADVERTISING</span><h2>广告投入、销售与付款分析</h2><p>连接店铺日报、商品报表、对账单和已支付流水。</p></div><nav><a href="/app/temu_finance_overvi">财务总览</a><a href="/app/temu_reconciliation">对账明细</a><a href="/app/temu_managed_bookkee">文件批次</a></nav></section>
				<section class="taa-filters">
					<div class="taa-months"><span>月份</span><div class="taa-month-buttons" role="group" aria-label="月份范围"></div></div>
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
				<section class="taa-card taa-products"><header><div><span>商品表现</span><h3>广告商品明细</h3></div><div class="taa-product-actions"><div class="taa-page-info"></div><button type="button" class="btn btn-default taa-summary-open" aria-expanded="false">商品汇总</button></div></header><div class="taa-table-wrap"><table><thead><tr><th>商品 / ERPNext物料</th><th>实际扣款（分摊）</th><th>销售额</th><th>ROAS</th><th>曝光 / 点击</th><th>订单 / 件数</th><th>转化率</th></tr></thead><tbody></tbody></table></div><footer><button class="btn btn-default taa-prev">上一页</button><button class="btn btn-default taa-next">下一页</button></footer><div class="taa-summary-overlay" hidden><div class="taa-summary-backdrop" data-close-summary></div><aside class="taa-summary-drawer" role="dialog" aria-modal="true" aria-label="商品汇总"><div class="taa-summary-heading"><h3>广告商品汇总</h3><button type="button" data-close-summary aria-label="关闭商品汇总">×</button></div><p>按商品净花费比例分摊平台钱包实际扣款；单个商品金额不是平台原始扣款记录。</p><div class="taa-summary-body"></div></aside></div></section>
				<section class="taa-counts"></section>
			</div>`);
	}

	bind() {
		this.$.on("click", "[data-month]", (event) => this.selectMonth(event.currentTarget.dataset.month));
		this.$.on("change", "[data-filter=date_from], [data-filter=date_to]", () => {
			this.selectedMonths.clear();
			this.monthSelectionStarted = false;
			this.renderMonthButtons();
		});
		this.$.on("click", ".taa-query", () => { this.pageNo = 1; this.load(); });
		this.$.on("keyup", "[data-filter=search]", (event) => { if (event.key === "Enter") { this.pageNo = 1; this.load(); } });
		this.$.on("click", ".taa-reset", () => { this.$.find("[data-filter]").val(""); this.availableMonths = []; this.selectedMonths.clear(); this.monthSelectionStarted = false; this.initialMonthApplied = false; this.renderMonthButtons(); this.pageNo = 1; this.load(); });
		this.$.on("click", ".taa-prev", () => { if (this.pageNo > 1) { this.pageNo -= 1; this.load(); } });
		this.$.on("click", ".taa-next", () => { if (this.pageNo < (this.data.pages || 1)) { this.pageNo += 1; this.load(); } });
		this.$.on("click", ".taa-currency-tabs button", (event) => { this.activeCurrency = $(event.currentTarget).data("currency"); this.renderData(); });
		this.$.on("click", ".taa-summary-open", () => this.toggleSummaryDrawer(!this.drawerOpen));
		this.$.on("click", ".taa-summary-table-wrap [data-copy-column]", (event) => this.copySummaryColumn(event.currentTarget));
		this.$.on("keydown", ".taa-summary-table-wrap [data-copy-column]", (event) => {
			if (event.key === "Enter" || event.key === " ") {
				event.preventDefault();
				this.copySummaryColumn(event.currentTarget);
			}
		});
		this.$.on("click", "[data-close-summary]", () => this.toggleSummaryDrawer(false));
		this.$.on("keydown", ".taa-summary-overlay", (event) => { if (event.key === "Escape") this.toggleSummaryDrawer(false); });
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
		window.addEventListener("resize", frappe.utils.debounce(() => this.charts.forEach((chart) => chart.resize()), 120));
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
		this.$.find(".taa-month-buttons").html(this.monthKeys().map((month) => {
			const [year, value] = month.split("-");
			return `<button type="button" data-month="${month}" class="taa-month-button${this.selectedMonths.has(month) ? " is-active" : ""}" aria-pressed="${this.selectedMonths.has(month)}">${year}年${Number(value)}月</button>`;
		}).join(""));
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

	selectMonth(month) {
		const months = this.monthKeys();
		if (!months.includes(month)) return;
		if (!this.monthSelectionStarted || !this.selectedMonths.size) {
			this.selectedMonths = new Set([month]);
			this.monthSelectionStarted = true;
		} else if (this.selectedMonths.has(month)) {
			if (this.selectedMonths.size > 1) this.selectedMonths.delete(month);
		} else this.selectedMonths.add(month);
		const indexes = months.map((value, index) => this.selectedMonths.has(value) ? index : -1).filter((index) => index >= 0);
		if (!indexes.length) return;
		this.selectedMonths = new Set(months.slice(Math.min(...indexes), Math.max(...indexes) + 1));
		const selected = [...this.selectedMonths].sort();
		this.$.find("[data-filter=date_from]").val(this.monthBounds(selected[0]).from);
		this.$.find("[data-filter=date_to]").val(this.monthBounds(selected[selected.length - 1]).to);
		this.renderMonthButtons();
		this.pageNo = 1;
		this.load();
	}

	filters() { const result = {}; this.$.find("[data-filter]").each((_, element) => { result[element.dataset.filter] = element.value || ""; }); return result; }

	async load() {
		const serial = ++this.loadSerial;
		this.$.addClass("is-loading");
		try {
			const response = await frappe.call({ method: TEMU_AD_METHOD, args: { filters: this.filters(), page: this.pageNo, page_size: 50 } });
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
			const currencies = Object.keys(this.data.currencies || {});
			if (!currencies.includes(this.activeCurrency)) this.activeCurrency = this.filters().currency || currencies[0] || "未知";
			this.renderData();
			this.loaded = true;
		} catch (error) {
			console.error(error); frappe.msgprint({ title: "Temu广告分析加载失败", message: error.message || "请查看错误日志。", indicator: "red" });
		} finally { if (serial === this.loadSerial) this.$.removeClass("is-loading"); }
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
		const paidAmount = Number(v.period_paid_amount || 0);
		const refundRedPacket = Number(v.period_refund_red_packet_amount || 0);
		const walletChange = -(paidAmount - refundRedPacket);
		const roas = Number(v.total_spend || 0) ? Number(v.declared_sales_amount || 0) / Number(v.total_spend || 1) : 0;
		const ctr = Number(v.impressions || 0) ? Number(v.clicks || 0) * 100 / Number(v.impressions || 1) : 0;
		const conversion = Number(v.clicks || 0) ? Number(v.suborder_count || 0) * 100 / Number(v.clicks || 1) : 0;
		const cards = [
			["广告花费", this.money(v.total_spend, currency), `净花费 ${this.money(v.net_total_spend, currency)}`],
			["申报价销售额", this.money(v.declared_sales_amount, currency), `${this.num(v.suborder_count)} 个子订单`],
			["ROAS", this.num(roas), "销售额 ÷ 广告花费"],
			["点击率", `${this.num(ctr)}%`, `${this.num(v.clicks)} / ${this.num(v.impressions)}`],
			["点击转化率", `${this.num(conversion)}%`, `${this.num(v.suborder_count)} 笔订单`],
			["平台钱包变动", this.money(walletChange, currency), `已付款 ${this.money(paidAmount, currency)} − 退单红包 ${this.money(refundRedPacket, currency)}`],
		];
		this.$.find(".taa-kpis").html(cards.map(([label, value, note], i) => `<article class="k${i}"><span>${label}</span><b>${value}</b><small>${note}</small></article>`).join(""));
		const currencies = Object.keys(this.data.currencies || {});
		this.$.find(".taa-currency-tabs").html(currencies.map((name) => `<button class="${name === currency ? "active" : ""}" data-currency="${this.esc(name)}">${this.esc(name)}</button>`).join(""));
		this.$.find(".taa-currency-label").text(currency);
		this.renderCharts(currency, v);
		this.renderProducts(currency);
		this.renderProductSummary(currency);
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
			return `<tr><td><div class="taa-product">${preview}<div class="taa-product-copy"><b>${this.esc(row.product_name || row.product_id)}</b><small>商品ID ${this.esc(row.product_id || "—")} · SPU ${this.esc(row.spu_id || "—")}</small>${item}</div></div></td><td>${row.actual_wallet_change == null ? "—" : this.money(row.actual_wallet_change, row.currency)}<small>原花费 ${this.money(row.spend, row.currency)}</small></td><td>${this.money(row.sales, row.currency)}</td><td><b>${this.num(row.roas)}</b></td><td>${this.num(row.impressions)}<small>${this.num(row.clicks)} 次点击</small></td><td>${this.num(row.orders)}<small>${this.num(row.units)} 件</small></td><td>${this.num(row.conversion_rate)}%</td></tr>`;
		}).join("") || `<tr><td colspan="7" class="taa-empty">当前条件没有广告商品数据</td></tr>`);
	}

	renderProductSummary(currency) {
		const groups = (this.data.product_summary || []).filter((row) =>
			(!currency || row.currency === currency) && Math.abs(Number(row.spend || 0)) >= 0.005
		);
		const rows = groups.flatMap((group) => {
			const items = Array.isArray(group.items) && group.items.length ? group.items : [null];
			const walletChange = group.allocation_available ? this.allocateSummaryAmount(group.actual_wallet_change, items.length) : [];
			const sales = this.allocateSummaryAmount(group.sales, items.length);
			return items.map((item, index) => ({ ...group, item, actual_wallet_change: walletChange[index] ?? null, sales: sales[index] }));
		});
		this.summaryRows = rows;
		const totalWalletChange = rows.reduce((total, row) => total + Number(row.actual_wallet_change || 0), 0);
		const unallocatedWalletChange = Number(this.data.unallocated_wallet_change?.[currency] || 0);
		const totalSales = rows.reduce((total, row) => total + Number(row.sales || 0), 0);
		const body = rows.map((row) => {
			const image = row.item_image ? `<img src="${this.esc(row.item_image)}" loading="lazy" alt="">` : "<i>AD</i>";
			const itemId = row.item?.item_code;
			const itemName = row.item?.item_name || (row.item ? "未命名物料" : "—");
			return `<tr><td><div class="taa-summary-product">${image}<div><b title="${this.esc(row.product_name || row.product_id)}">${this.esc(row.product_name || row.product_id || "未命名商品")}</b><small>${this.num(row.count)} 条记录</small></div></div></td><td>${this.esc(row.product_id || "—")}</td><td title="${this.esc(row.cost_center || "未指定成本中心")}">${this.esc(row.cost_center || "未指定成本中心")}</td><td class="taa-summary-item-id">${itemId ? `<a href="/app/item/${encodeURIComponent(itemId)}">${this.esc(itemId)}</a>` : "—"}</td><td title="${this.esc(TEMU_AD_ACCOUNT)}">${this.esc(TEMU_AD_ACCOUNT)}</td><td>${row.actual_wallet_change == null ? "—" : this.money(row.actual_wallet_change, row.currency)}</td><td>${this.esc(TEMU_AD_SUMMARY)}</td><td class="taa-summary-item-name" title="${this.esc(itemName)}">${this.esc(itemName)}</td><td>${this.money(row.sales, row.currency)}</td></tr>`;
		}).join("");
		this.$.find(".taa-summary-body").html(`<div class="taa-summary-totals"><article><b>${this.money(totalWalletChange, currency)}</b><span>实际扣款合计（分摊）</span></article>${unallocatedWalletChange ? `<article><b>${this.money(unallocatedWalletChange, currency)}</b><span>缺少商品净花费，无法分摊</span></article>` : ""}<article><b>${this.money(totalSales, currency)}</b><span>销售额合计</span></article><article><b>${this.num(rows.length)}</b><span>物料明细行</span></article></div><div class="taa-summary-table-wrap"><table><thead><tr><th>商品名称</th><th>商品ID</th><th data-copy-column="cost_center" tabindex="0" role="button" title="点击复制成本中心整列">成本中心 <span class="taa-copy-indicator" aria-hidden="true">⧉</span></th><th data-copy-column="item_code" tabindex="0" role="button" title="点击复制物料ID整列">物料ID <span class="taa-copy-indicator" aria-hidden="true">⧉</span></th><th data-copy-column="account" tabindex="0" role="button" title="点击复制科目整列">科目 <span class="taa-copy-indicator" aria-hidden="true">⧉</span></th><th data-copy-column="actual_wallet_change" tabindex="0" role="button" title="点击复制正数扣款整列">实际扣款（分摊） <span class="taa-copy-indicator" aria-hidden="true">⧉</span></th><th data-copy-column="summary" tabindex="0" role="button" title="点击复制摘要整列">摘要 <span class="taa-copy-indicator" aria-hidden="true">⧉</span></th><th>物料名称</th><th>销售额</th></tr></thead><tbody>${body || '<tr><td colspan="9" class="taa-empty">当前条件没有非零花费的广告商品</td></tr>'}</tbody></table></div>`);
	}

	allocateSummaryAmount(value, count) {
		const amount = Number(value || 0);
		const cents = Math.round(Math.abs(amount) * 100);
		const base = Math.floor(cents / count);
		const remainder = cents % count;
		const sign = amount < 0 ? -1 : 1;
		return Array.from({ length: count }, (_, index) => sign * (base + (index < remainder ? 1 : 0)) / 100);
	}

	async copySummaryColumn(heading) {
		const rows = this.summaryRows || [];
		if (!rows.length) return;
		const column = heading.dataset.copyColumn;
		const values = rows.map((row) => {
			if (column === "cost_center") return String(row.cost_center || "未指定成本中心");
			if (column === "account") return TEMU_AD_ACCOUNT;
			if (column === "item_code") return String(row.item?.item_code || "");
			if (column === "summary") return TEMU_AD_SUMMARY;
			return row.actual_wallet_change == null ? "" : Math.abs(Number(row.actual_wallet_change)).toFixed(2);
		});
		try {
			await navigator.clipboard.writeText(values.map((value) => value.replace(/[\r\n\t]/g, " ")).join(column === "summary" ? ";" : "\n"));
			const labels = { cost_center: "成本中心", account: "科目", item_code: "物料ID", actual_wallet_change: "实际扣款", summary: "摘要" };
			frappe.show_alert({ message: column === "summary" ? `已复制 ${values.length} 条摘要，以分号分隔` : `已复制 ${labels[column] || "数据"} ${values.length} 行`, indicator: "green" });
		} catch (error) {
			frappe.show_alert({ message: "复制失败，请检查浏览器剪贴板权限", indicator: "red" });
		}
	}

	toggleSummaryDrawer(open) {
		this.drawerOpen = open;
		this.$.find(".taa-products").toggleClass("is-summary-open", open);
		this.$.find(".taa-summary-overlay").prop("hidden", !open);
		this.$.find(".taa-summary-open").attr("aria-expanded", String(open));
		if (open) this.$.find(".taa-summary-drawer").attr("tabindex", "-1").trigger("focus");
	}

	renderCounts() {
		const labels = { store_daily: "店铺日报", products: "商品记录", reconciliation: "广告对账", payments: "付款流水", unbound_products: "未绑定商品" };
		this.$.find(".taa-counts").html(Object.entries(this.data.counts || {}).map(([key, value]) => `<span>${labels[key] || key}<b>${this.num(value)}</b></span>`).join(""));
	}

	money(value, currency) { return `${this.esc(currency || "")} ${Number(value || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`; }
	num(value) { return Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 2 }); }
	esc(value) { return frappe.utils.escape_html(String(value ?? "")); }
}
