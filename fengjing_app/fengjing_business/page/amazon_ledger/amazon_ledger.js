frappe.pages["amazon-ledger"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: "Amazon库存分类账", single_column: true });
	frappe.require("/assets/fengjing_app/js/图表-echarts.js", () => {
		wrapper.amazonLedger = new AmazonInventoryLedger(wrapper, page);
	});
};

frappe.pages["amazon-ledger"].on_page_show = function (wrapper) {
	if (wrapper.amazonLedger?.loaded) wrapper.amazonLedger.load();
};

class AmazonInventoryLedger {
	constructor(wrapper, page) {
		this.wrapper = wrapper;
		this.page = page;
		this.data = {};
		this.pageNo = 1;
		this.pageSize = 50;
		this.openRows = new Set();
		this.detailCache = new Map();
		this.charts = [];
		this.loaded = false;
		this.render();
		this.bind();
		this.load();
	}

	render() {
		$(this.wrapper).addClass("amazon-ledger-page");
		this.$ = $(this.page.main).html(`
			<style>${this.styles()}</style>
			<div class="ail-shell">
				<section class="ail-hero"><div><span>FBA INVENTORY LEDGER</span><h2>Amazon库存分类账</h2><p>按日期查看期初、入库、出库、退货、调整和期末库存，并可展开核对原始事件。</p></div><nav><a href="/app/amazon-inventory">库存总览</a><a href="/app/amazon_configuration">Amazon配置中心</a></nav></section>
				<section class="ail-filters">
					<label>店铺<select data-filter="amazon_store"><option value="">全部店铺</option></select></label><label>国家<select data-filter="country"><option value="">全部国家</option></select></label>
					<label>配送位置<select data-filter="location_id"><option value="">全部位置</option></select></label><label>库存属性<select data-filter="disposition"><option value="">全部属性</option></select></label>
					<label>开始日期<input type="date" data-filter="date_from"></label><label>结束日期<input type="date" data-filter="date_to"></label>
					<label class="ail-search">SKU / ASIN / 物料<input type="search" data-filter="search" placeholder="输入后查询"></label>
					<button class="btn btn-primary ail-query">查询</button><button class="btn btn-default ail-reset">重置</button>
				</section>
				<section class="ail-kpis"></section>
				<section class="ail-grid"><article class="ail-card ail-wide"><header><div><span>每日变化</span><h3>库存流入、流出与期末结余</h3></div></header><div id="ail-daily" class="ail-chart"></div></article><article class="ail-card"><header><div><span>变动构成</span><h3>分类账数量汇总</h3></div></header><div id="ail-movements" class="ail-chart"></div></article></section>
				<section class="ail-card ail-events"><header><div><span>事件明细</span><h3>库存事件类型</h3></div><small>来自分类账明细报告</small></header><div class="ail-event-list"></div></section>
				<section class="ail-card ail-table-card"><header><div><span>库存分类账</span><h3>每日汇总</h3></div><div class="ail-page-info"></div></header><div class="ail-table-wrap"><table><thead><tr><th></th><th>日期</th><th>商品 / SKU</th><th>ERPNext物料</th><th>店铺 / 国家</th><th>位置 / 属性</th><th class="num">期初</th><th class="num">入库</th><th class="num">销售出库</th><th class="num">客户退货</th><th class="num">转入</th><th class="num">转出</th><th class="num">调整</th><th class="num">期末</th></tr></thead><tbody></tbody></table></div><footer><button class="btn btn-default ail-prev">上一页</button><span class="ail-pagination"></span><button class="btn btn-default ail-next">下一页</button></footer></section>
			</div>`);
	}

	bind() {
		this.$.on("click", ".ail-query", () => { this.pageNo = 1; this.load(); });
		this.$.on("click", ".ail-reset", () => { this.$.find("[data-filter]").val(""); this.pageNo = 1; this.load(); });
		this.$.on("keydown", "[data-filter=search]", (event) => { if (event.key === "Enter") { this.pageNo = 1; this.load(); } });
		this.$.on("click", ".ail-prev", () => { if (this.pageNo > 1) { this.pageNo--; this.load(); } });
		this.$.on("click", ".ail-next", () => { if (this.pageNo < (this.data.pagination?.pages || 1)) { this.pageNo++; this.load(); } });
		this.$.on("click", ".ail-expand", (event) => this.toggleDetails(event.currentTarget.dataset.name));
		this.resizeHandler = frappe.utils.debounce(() => this.charts.forEach((chart) => chart?.resize()), 120);
		window.addEventListener("resize", this.resizeHandler);
	}

	filters() {
		const result = {};
		this.$.find("[data-filter]").each((_, element) => { result[element.dataset.filter] = element.value || ""; });
		return result;
	}

	async load() {
		if (this.loading) return;
		this.loading = true;
		this.$.addClass("is-loading");
		try {
			const response = await frappe.call({ method: "fengjing_app.fengjing_business.page.amazon_ledger.amazon_ledger.get_ledger_data", args: { filters: this.filters(), page: this.pageNo, page_size: this.pageSize } });
			this.data = response.message || {};
			this.openRows.clear(); this.detailCache.clear();
			this.fillOptions(); this.renderSummary(); this.renderCharts(); this.renderEvents(); this.renderTable();
			this.loaded = true;
			if (this.data.limited) frappe.show_alert({ message: "当前范围数据较多，请缩小日期范围。", indicator: "orange" }, 8);
		} catch (error) {
			console.error(error);
			frappe.msgprint({ title: "Amazon库存分类账加载失败", message: error.message || "请查看错误日志。", indicator: "red" });
		} finally { this.loading = false; this.$.removeClass("is-loading"); }
	}

	fillOptions() {
		const options = this.data.options || {};
		this.setOptions("amazon_store", options.stores || [], "全部店铺"); this.setOptions("country", options.countries || [], "全部国家");
		this.setOptions("location_id", options.locations || [], "全部位置"); this.setOptions("disposition", options.dispositions || [], "全部属性");
	}

	setOptions(name, values, emptyLabel) {
		const select = this.$.find(`[data-filter=${name}]`), current = select.val();
		select.html(`<option value="">${emptyLabel}</option>${values.map((value) => `<option value="${this.esc(value)}">${this.esc(this.translate(value))}</option>`).join("")}`); select.val(current);
	}

	renderSummary() {
		const s = this.data.summary || {};
		const cards = [["期末库存", s.latest_ending_balance, "各SKU与位置最新记录"], ["接收入库", s.receipts, "当前筛选日期范围"], ["客户出库", s.shipments, "当前筛选日期范围"], ["客户退货", s.returns, "当前筛选日期范围"], ["汇总记录", s.rows, `${this.num(s.sku_count)} 个店铺SKU`], ["事件明细", s.detail_rows, `未核对 ${this.num(s.unreconciled)}`]];
		this.$.find(".ail-kpis").html(cards.map(([label, value, note], index) => `<article class="tone-${index}"><span>${label}</span><b>${this.num(value)}</b><small>${note}</small></article>`).join(""));
	}

	renderCharts() {
		this.charts.forEach((chart) => chart?.dispose()); this.charts = [];
		const daily = this.data.daily || [], dates = daily.map((row) => row.date);
		this.chart("ail-daily", { tooltip: { trigger: "axis" }, legend: { top: 4 }, grid: { left: 58, right: 28, top: 48, bottom: 42 }, xAxis: { type: "category", data: dates }, yAxis: { type: "value" }, dataZoom: [{ type: "inside" }], series: [
			{ name: "接收入库", type: "bar", data: daily.map((row) => row.receipts), itemStyle: { color: "#3b82f6", borderRadius: [5, 5, 0, 0] } },
			{ name: "销售出库", type: "bar", data: daily.map((row) => row.shipments), itemStyle: { color: "#f59e0b", borderRadius: [5, 5, 0, 0] } },
			{ name: "客户退货", type: "bar", data: daily.map((row) => row.returns), itemStyle: { color: "#10b981", borderRadius: [5, 5, 0, 0] } },
			{ name: "期末库存", type: "line", smooth: true, showSymbol: dates.length < 20, data: daily.map((row) => row.ending), lineStyle: { width: 3, color: "#7c3aed" }, itemStyle: { color: "#7c3aed" } },
		] });
		const movements = (this.data.movement_totals || []).filter((row) => row.quantity);
		this.chart("ail-movements", { tooltip: { trigger: "item", formatter: (p) => `${this.esc(p.name)}：${this.num(p.value)}` }, series: [{ type: "pie", radius: ["38%", "68%"], center: ["50%", "47%"], itemStyle: { borderColor: "#fff", borderWidth: 3, borderRadius: 6 }, label: { formatter: "{b}\n{c}" }, data: movements.map((row) => ({ name: row.label, value: Math.abs(row.quantity), signed: row.quantity })) }] });
	}

	chart(id, option) { const element = this.wrapper.querySelector(`#${id}`); if (!element || typeof echarts === "undefined") return; const chart = echarts.init(element); chart.setOption(option); this.charts.push(chart); }

	renderEvents() {
		const rows = this.data.event_totals || [], max = Math.max(...rows.map((row) => Math.abs(row.quantity)), 1);
		this.$.find(".ail-event-list").html(rows.slice(0, 16).map((row) => `<div><span><b>${this.esc(this.translate(row.event_type))}</b><small>${this.num(row.count)} 条</small></span><em><i style="width:${Math.max(3, Math.abs(row.quantity) * 100 / max)}%"></i></em><strong>${this.num(row.quantity)}</strong></div>`).join("") || `<div class="ail-empty">分类账明细尚未产生数据</div>`);
	}

	renderTable() {
		const p = this.data.pagination || { page: 1, pages: 1, total: 0 }, rows = this.data.rows || [];
		this.$.find(".ail-page-info").html(`共 <b>${this.num(p.total)}</b> 条 · 第 ${p.page}/${p.pages} 页`); this.$.find(".ail-pagination").text(`第 ${p.page} / ${p.pages} 页`);
		this.$.find(".ail-prev").prop("disabled", p.page <= 1); this.$.find(".ail-next").prop("disabled", p.page >= p.pages);
		this.$.find(".ail-table-card tbody").html(rows.map((row) => this.summaryRow(row)).join("") || `<tr><td colspan="14" class="ail-empty">库存分类账尚未产生数据；历史报告完成后会自动显示在这里。</td></tr>`);
	}

	summaryRow(row) {
		const image = row.item_image ? `<img src="${this.esc(row.item_image)}" loading="lazy">` : `<i>FBA</i>`;
		const item = row.corresponding_item ? `<a href="/app/item/${encodeURIComponent(row.corresponding_item)}">${this.esc(row.corresponding_item)}</a><small>${this.esc(row.item_name || "")}</small>` : `<em>未绑定ERPNext物料</em>`;
		const adjustment = Number(row.found_quantity || 0) - Number(row.lost_quantity || 0) - Number(row.damaged_quantity || 0) - Number(row.disposed_quantity || 0) + Number(row.other_events_quantity || 0);
		return `<tr data-summary="${this.esc(row.name)}"><td><button class="ail-expand" data-name="${this.esc(row.name)}" title="查看对应事件">›</button></td><td><b>${this.esc(row.period_date || "—")}</b><small>${this.esc(this.translate(row.time_aggregation || ""))}</small></td><td><div class="ail-product">${image}<span><b>${this.esc(row.product_name || row.seller_sku || "未命名商品")}</b><small>SKU ${this.esc(row.seller_sku || "—")}</small><small>${this.esc([row.asin, row.fnsku].filter(Boolean).join(" · ") || "无ASIN/FNSKU")}</small></span></div></td><td>${item}</td><td><b>${this.esc(row.amazon_store || "—")}</b><small>${this.esc(this.translate(row.country || ""))}</small></td><td><b>${this.esc(row.location_id || "全部位置")}</b><small>${this.esc(this.translate(row.disposition || "未分类"))}</small></td><td class="num">${this.num(row.starting_warehouse_balance)}</td><td class="num positive">${this.num(row.receipts)}</td><td class="num">${this.num(row.customer_shipments)}</td><td class="num positive">${this.num(row.customer_returns)}</td><td class="num">${this.num(row.warehouse_transfer_in)}</td><td class="num">${this.num(row.warehouse_transfer_out)}</td><td class="num ${adjustment < 0 ? "negative" : adjustment > 0 ? "positive" : ""}">${this.num(adjustment)}</td><td class="num strong">${this.num(row.ending_warehouse_balance)}</td></tr>`;
	}

	async toggleDetails(name) {
		const row = this.$.find(`tr[data-summary="${CSS.escape(name)}"]`), button = row.find(".ail-expand");
		if (this.openRows.has(name)) { this.openRows.delete(name); button.removeClass("open"); row.next(`tr[data-details="${CSS.escape(name)}"]`).remove(); return; }
		this.openRows.add(name); button.addClass("open"); row.after(`<tr data-details="${this.esc(name)}"><td colspan="14"><div class="ail-detail-box">正在读取对应库存事件…</div></td></tr>`);
		try {
			let data = this.detailCache.get(name);
			if (!data) { const response = await frappe.call({ method: "fengjing_app.fengjing_business.page.amazon_ledger.amazon_ledger.get_summary_details", args: { name } }); data = response.message || {}; this.detailCache.set(name, data); }
			if (!this.openRows.has(name)) return;
			this.$.find(`tr[data-details="${CSS.escape(name)}"] .ail-detail-box`).html(this.detailTable(data));
		} catch (error) { this.$.find(`tr[data-details="${CSS.escape(name)}"] .ail-detail-box`).html(`<div class="ail-empty">明细读取失败：${this.esc(error.message || "未知错误")}</div>`); }
	}

	detailTable(data) {
		const rows = data.rows || [];
		return `<header><b>对应事件明细</b><span>${this.esc(data.period?.date_from || "")} 至 ${this.esc(data.period?.date_to || "")} · ${this.num(rows.length)} 条${data.limited ? "（仅显示前2000条）" : ""}</span></header><div class="ail-detail-scroll"><table><thead><tr><th>发生时间</th><th>事件类型</th><th>参考编号</th><th>配送中心</th><th>属性 / 原因</th><th class="num">数量</th><th class="num">已核对</th><th class="num">未核对</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${this.date(row.event_at)}</td><td><b>${this.esc(this.translate(row.event_type || "UNKNOWN"))}</b></td><td>${this.esc(row.reference_id || "—")}</td><td>${this.esc(row.fulfillment_center || "—")}</td><td>${this.esc(this.translate(row.disposition || ""))}<small>${this.esc(row.reason || "")}</small></td><td class="num ${row.quantity < 0 ? "negative" : "positive"}">${this.num(row.quantity)}</td><td class="num">${this.num(row.reconciled_quantity)}</td><td class="num ${row.unreconciled_quantity ? "negative" : ""}">${this.num(row.unreconciled_quantity)}</td></tr>`).join("") || `<tr><td colspan="8" class="ail-empty">该汇总记录没有匹配的明细事件</td></tr>`}</tbody></table></div>`;
	}

	translate(value) { return ({ "United States": "美国", Canada: "加拿大", Mexico: "墨西哥", Brazil: "巴西", DAILY: "每日", WEEKLY: "每周", MONTHLY: "每月", SELLABLE: "可售", UNSELLABLE: "不可售", CUSTOMER_SHIPMENT: "客户出库", CUSTOMER_RETURN: "客户退货", RECEIPT: "接收入库", VENDOR_RETURN: "供应商退货", ADJUSTMENT: "库存调整", TRANSFER: "仓库调拨" })[value] || value; }
	num(value) { return Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 2 }); }
	date(value) { return value ? String(value).replace("T", " ").replace(/\.\d+$/, "").slice(0, 19) : "—"; }
	esc(value) { return frappe.utils.escape_html(String(value ?? "")); }

	styles() { return `
		.amazon-ledger-page .layout-main-section{background:#f5f7fb}.ail-shell{padding:4px 4px 28px;color:#172033}.ail-hero{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:24px 26px;border:1px solid #dce5f2;border-radius:16px;background:linear-gradient(120deg,#17365d,#237a87);color:#fff;box-shadow:0 12px 28px rgba(30,67,122,.16)}.ail-hero span,.ail-card header span{font-size:10px;font-weight:800;letter-spacing:.13em}.ail-hero span{color:#9ff4e8}.ail-hero h2{margin:5px 0 4px;color:#fff;font-size:25px}.ail-hero p{margin:0;color:#dcecf0}.ail-hero nav{display:flex;gap:8px;flex-wrap:wrap}.ail-hero a{padding:8px 12px;border:1px solid rgba(255,255,255,.3);border-radius:9px;color:#fff;background:rgba(255,255,255,.1);text-decoration:none}.ail-filters{display:flex;align-items:flex-end;gap:9px;flex-wrap:wrap;margin:14px 0;padding:13px;border:1px solid #dde5f0;border-radius:13px;background:#fff}.ail-filters label{display:flex;flex-direction:column;gap:4px;color:#617089;font-size:11px}.ail-filters input,.ail-filters select{height:32px;min-width:132px;border:1px solid #ced8e6;border-radius:8px;background:#fff;padding:0 9px;color:#25324a}.ail-filters .ail-search{flex:1;min-width:190px}.ail-filters .ail-search input{width:100%}.ail-kpis{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}.ail-kpis article{padding:14px;border:1px solid #dce5ef;border-radius:13px;background:#fff;box-shadow:0 6px 18px rgba(40,60,96,.05)}.ail-kpis span,.ail-kpis b,.ail-kpis small{display:block}.ail-kpis span{color:#68768c;font-size:11px}.ail-kpis b{margin:6px 0 3px;color:#18304f;font-size:23px}.ail-kpis small{color:#909bad;font-size:10px}.ail-kpis .tone-0{border-top:3px solid #7c3aed}.ail-kpis .tone-1{border-top:3px solid #2563eb}.ail-kpis .tone-2{border-top:3px solid #f59e0b}.ail-kpis .tone-3{border-top:3px solid #10b981}.ail-grid{display:grid;grid-template-columns:2fr 1fr;gap:14px;margin-top:14px}.ail-card{border:1px solid #dce4ef;border-radius:15px;background:#fff;box-shadow:0 7px 20px rgba(39,60,96,.06)}.ail-grid .ail-card,.ail-events{padding:17px}.ail-card>header{display:flex;align-items:center;justify-content:space-between;gap:12px}.ail-card header span{color:#147f86}.ail-card header h3{margin:3px 0 0;color:#1e293b;font-size:17px}.ail-card header small{color:#7b8799}.ail-chart{height:310px}.ail-events{margin-top:14px}.ail-event-list{display:grid;grid-template-columns:repeat(2,1fr);gap:7px 20px;margin-top:13px}.ail-event-list>div{display:grid;grid-template-columns:minmax(130px,1fr) 2fr 65px;align-items:center;gap:9px}.ail-event-list span b,.ail-event-list span small{display:block}.ail-event-list span small{color:#8792a4;font-size:10px}.ail-event-list em{height:7px;border-radius:7px;background:#edf1f6;overflow:hidden}.ail-event-list em i{display:block;height:100%;border-radius:7px;background:linear-gradient(90deg,#26a69a,#4f70d9)}.ail-event-list strong{text-align:right;color:#31445f}.ail-table-card{margin-top:14px;overflow:hidden}.ail-table-card>header{padding:17px 18px;border-bottom:1px solid #e5ebf3}.ail-page-info{color:#64748b}.ail-table-wrap{max-height:72vh;overflow:auto}.ail-table-wrap>table{width:100%;min-width:1660px;border-collapse:collapse}.ail-table-wrap>table>thead th{position:sticky;z-index:2;top:0;padding:11px 9px;border-bottom:1px solid #dbe3ee;background:#f7f9fc;color:#536176;font-size:11px;white-space:nowrap;text-align:left}.ail-table-wrap>table>tbody>tr>td{padding:9px;border-bottom:1px solid #edf1f6;vertical-align:middle;color:#334155;font-size:12px}.ail-table-wrap>table>tbody>tr:not([data-details]):hover{background:#f8fbff}.ail-table-wrap td small,.ail-table-wrap td b{display:block}.ail-table-wrap td small{margin-top:3px;color:#8490a2}.ail-table-wrap td a{font-weight:700}.ail-table-wrap td em{color:#b45309;font-style:normal}.ail-table-wrap .num{text-align:right;font-variant-numeric:tabular-nums}.ail-table-wrap .strong{font-weight:800}.ail-product{display:flex;align-items:center;gap:9px;min-width:250px}.ail-product img,.ail-product i{display:grid;place-items:center;width:42px;height:42px;flex:0 0 42px;border-radius:9px;background:#edf3ff;object-fit:cover;color:#315da8;font-size:10px;font-style:normal;font-weight:800}.ail-product img:hover{position:relative;z-index:5;transform:scale(2.8);box-shadow:0 8px 30px rgba(15,23,42,.25)}.ail-expand{display:grid;place-items:center;width:25px;height:25px;border:1px solid #c9d8ea;border-radius:7px;background:#f2f7fd;color:#2761aa;font-size:21px;line-height:1;transition:.15s}.ail-expand.open{transform:rotate(90deg);background:#2761aa;color:#fff}.ail-detail-box{margin:4px 24px 12px;padding:13px;border:1px solid #cfe0f1;border-radius:12px;background:#f7faff}.ail-detail-box>header{display:flex;justify-content:space-between;margin-bottom:9px}.ail-detail-box>header span{color:#718096}.ail-detail-scroll{max-height:340px;overflow:auto}.ail-detail-scroll table{width:100%;min-width:1020px;border-collapse:collapse;background:#fff}.ail-detail-scroll th,.ail-detail-scroll td{padding:8px;border-bottom:1px solid #e5edf6;font-size:11px;text-align:left}.ail-detail-scroll th{position:sticky;top:0;background:#edf4fb}.ail-detail-scroll .num{text-align:right}.ail-table-card>footer{display:flex;justify-content:center;align-items:center;gap:12px;padding:12px;border-top:1px solid #e6ebf2;background:#fbfcfe}.ail-pagination{color:#64748b}.positive{color:#07855b!important}.negative{color:#d33f49!important}.ail-empty{grid-column:1/-1;height:100px;text-align:center!important;color:#8793a4!important}.amazon-ledger-page.is-loading .ail-shell{opacity:.55;pointer-events:none}@media(max-width:1100px){.ail-kpis{grid-template-columns:repeat(3,1fr)}.ail-grid{grid-template-columns:1fr}}@media(max-width:700px){.ail-hero{align-items:flex-start;flex-direction:column}.ail-kpis{grid-template-columns:repeat(2,1fr)}.ail-event-list{grid-template-columns:1fr}.ail-filters label,.ail-filters input,.ail-filters select{width:100%}.ail-filters label{flex:1 1 45%}}
	`; }
}
