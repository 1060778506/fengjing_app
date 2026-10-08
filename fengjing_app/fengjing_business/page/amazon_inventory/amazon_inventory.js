frappe.pages["amazon-inventory"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: "Amazon库存总览", single_column: true });
	frappe.require("/assets/fengjing_app/js/图表-echarts.js", () => {
		wrapper.amazonInventory = new AmazonInventoryOverview(wrapper, page);
	});
};

frappe.pages["amazon-inventory"].on_page_show = function (wrapper) {
	if (wrapper.amazonInventory?.loaded) wrapper.amazonInventory.load();
};

class AmazonInventoryOverview {
	constructor(wrapper, page) {
		this.wrapper = wrapper;
		this.page = page;
		this.data = {};
		this.rows = [];
		this.sort = { key: "total", direction: -1 };
		this.charts = [];
		this.loaded = false;
		this.render();
		this.bind();
		this.load();
	}

	render() {
		$(this.wrapper).addClass("amazon-inventory-page");
		this.$ = $(this.page.main).html(`
			<style>${this.styles()}</style>
			<div class="aiv-shell">
				<section class="aiv-hero">
					<div><span>AMAZON INVENTORY</span><h2>Amazon库存总览</h2><p>分别查看 FBA 站点库存与 AWD 账户级库存，库存数量不会跨体系重复相加。</p></div>
				<nav><a href="/app/amazon-ledger">库存分类账</a><a href="/app/amazon_configuration">Amazon配置中心</a></nav>
				</section>
				<section class="aiv-filters">
					<label>库存体系<select data-filter="platform"><option value="">FBA + AWD</option><option value="FBA">仅 FBA</option><option value="AWD">仅 AWD</option></select></label>
					<label>店铺<select data-filter="amazon_store"><option value="">全部店铺</option></select></label>
					<label>国家<select data-filter="country"><option value="">全部国家</option></select></label>
					<label>物料绑定<select data-filter="mapping"><option value="">全部</option><option value="bound">已绑定</option><option value="unbound">未绑定</option></select></label>
					<label>开始日期<input type="date" data-filter="date_from"></label>
					<label>结束日期<input type="date" data-filter="date_to"></label>
					<label class="aiv-search">SKU / ASIN / 物料<input type="search" data-filter="search" placeholder="输入后查询"></label>
					<button class="btn btn-primary aiv-query">查询</button><button class="btn btn-default aiv-reset">重置</button>
				</section>
				<section class="aiv-system-grid">
					<article class="aiv-system fba"><header><div><span>FBA</span><h3>站点库存</h3></div><small data-latest="fba">尚无快照</small></header><div class="aiv-kpis" data-kpis="fba"></div></article>
					<article class="aiv-system awd"><header><div><span>AWD</span><h3>账户级库存</h3></div><small data-latest="awd">尚无快照</small></header><div class="aiv-kpis" data-kpis="awd"></div></article>
				</section>
				<section class="aiv-card aiv-chart-card"><header><div><span>库存趋势</span><h3>可用、库存与在途</h3></div><small>每天保留各店铺最后一次快照</small></header><div id="aiv-trend" class="aiv-chart"></div></section>
				<section class="aiv-card aiv-table-card">
					<header><div><span>当前快照</span><h3>SKU库存明细</h3></div><div class="aiv-table-note"></div></header>
					<div class="aiv-notice">AWD API 返回卖家账户级库存；未指定店铺或国家时，相同 SKU 会自动去重，并显示提供该库存的配置数量。</div>
					<div class="aiv-table-wrap"><table><thead><tr>
						<th data-sort="platform">体系</th><th data-sort="seller_sku">商品 / SKU</th><th>ERPNext物料</th><th data-sort="country">店铺 / 国家</th><th data-sort="snapshot_at">快照时间</th>
						<th class="num" data-sort="total">库存总量</th><th class="num" data-sort="available">可用</th><th class="num" data-sort="reserved">预留</th><th class="num" data-sort="inbound">在途</th><th class="num" data-sort="unavailable">不可售</th><th class="num" data-sort="replenishment">补货至FBA</th>
					</tr></thead><tbody></tbody></table></div>
				</section>
			</div>`);
	}

	bind() {
		this.$.on("click", ".aiv-query", () => this.load());
		this.$.on("click", ".aiv-reset", () => { this.$.find("[data-filter]").val(""); this.load(); });
		this.$.on("keydown", "[data-filter=search]", (event) => { if (event.key === "Enter") this.load(); });
		this.$.on("click", "th[data-sort]", (event) => {
			const key = event.currentTarget.dataset.sort;
			this.sort.direction = this.sort.key === key ? -this.sort.direction : 1;
			this.sort.key = key;
			this.renderTable();
		});
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
			const response = await frappe.call({
				method: "fengjing_app.fengjing_business.page.amazon_inventory.amazon_inventory.get_inventory_data",
				args: { filters: this.filters() },
			});
			this.data = response.message || {};
			this.rows = this.data.rows || [];
			this.fillOptions();
			this.renderSummary();
			this.renderChart();
			this.renderTable();
			this.loaded = true;
			if (this.data.limited) frappe.show_alert({ message: "当前结果超过5000条，表格仅显示前5000条。", indicator: "orange" }, 8);
		} catch (error) {
			console.error(error);
			frappe.msgprint({ title: "Amazon库存总览加载失败", message: error.message || "请查看错误日志。", indicator: "red" });
		} finally {
			this.loading = false;
			this.$.removeClass("is-loading");
		}
	}

	fillOptions() {
		this.setOptions("amazon_store", this.data.options?.stores || [], "全部店铺");
		this.setOptions("country", this.data.options?.countries || [], "全部国家");
	}

	setOptions(name, values, emptyLabel) {
		const select = this.$.find(`[data-filter=${name}]`);
		const current = select.val();
		select.html(`<option value="">${emptyLabel}</option>${values.map((value) => `<option value="${this.esc(value)}">${this.esc(this.translate(value))}</option>`).join("")}`);
		select.val(current);
	}

	renderSummary() {
		for (const platform of ["fba", "awd"]) {
			const data = this.data.summary?.[platform] || {};
			const entries = platform === "fba"
				? [["SKU记录", data.sku_count], ["库存总量", data.total], ["可售", data.available], ["预留", data.reserved], ["在途", data.inbound], ["不可售", data.unavailable]]
				: [["SKU记录", data.sku_count], ["在库", data.total], ["可分拨", data.available], ["预留", data.reserved], ["入库中", data.inbound], ["补货至FBA", data.replenishment]];
			this.$.find(`[data-kpis=${platform}]`).html(entries.map(([label, value]) => `<div><span>${label}</span><b>${this.num(value)}</b></div>`).join(""));
			const latest = this.data.latest?.[platform];
			this.$.find(`[data-latest=${platform}]`).text(latest ? `最新 ${this.date(latest)}` : "尚无快照");
		}
	}

	renderChart() {
		this.charts.forEach((chart) => chart?.dispose());
		this.charts = [];
		const rows = this.data.trend || [];
		const dates = [...new Set(rows.map((row) => row.date))].sort();
		const lookup = new Map(rows.map((row) => [`${row.platform}|${row.date}`, row]));
		const value = (platform, date, key) => lookup.get(`${platform}|${date}`)?.[key] || 0;
		const element = this.wrapper.querySelector("#aiv-trend");
		if (!element || typeof echarts === "undefined") return;
		const chart = echarts.init(element);
		chart.setOption({
			tooltip: { trigger: "axis" }, legend: { top: 4 }, grid: { left: 58, right: 28, top: 48, bottom: 42 },
			xAxis: { type: "category", data: dates }, yAxis: { type: "value" }, dataZoom: [{ type: "inside" }],
			series: [
				{ name: "FBA库存", type: "line", smooth: true, showSymbol: dates.length < 20, data: dates.map((date) => value("FBA", date, "total")), lineStyle: { width: 3, color: "#2563eb" }, itemStyle: { color: "#2563eb" } },
				{ name: "FBA可售", type: "line", smooth: true, showSymbol: dates.length < 20, data: dates.map((date) => value("FBA", date, "available")), lineStyle: { width: 2, color: "#16a34a" }, itemStyle: { color: "#16a34a" } },
				{ name: "AWD在库", type: "line", smooth: true, showSymbol: dates.length < 20, data: dates.map((date) => value("AWD", date, "total")), lineStyle: { width: 3, color: "#7c3aed" }, itemStyle: { color: "#7c3aed" } },
				{ name: "AWD可分拨", type: "line", smooth: true, showSymbol: dates.length < 20, data: dates.map((date) => value("AWD", date, "available")), lineStyle: { width: 2, color: "#ea580c" }, itemStyle: { color: "#ea580c" } },
			],
		});
		this.charts.push(chart);
	}

	renderTable() {
		const key = this.sort.key;
		const direction = this.sort.direction;
		const rows = [...this.rows].sort((a, b) => {
			const av = a[key] ?? "", bv = b[key] ?? "";
			if (typeof av === "number" || typeof bv === "number") return (Number(av) - Number(bv)) * direction;
			return String(av).localeCompare(String(bv), "zh-CN") * direction;
		});
		this.$.find(".aiv-table-note").html(`共 <b>${this.num(this.data.row_count)}</b> 条${this.data.awd_deduplicated ? " · AWD已按SKU去重" : ""}`);
		this.$.find(".aiv-table-card tbody").html(rows.map((row) => {
			const image = row.item_image ? `<img src="${this.esc(row.item_image)}" loading="lazy">` : `<i>${row.platform}</i>`;
			const identity = [row.asin, row.fnsku].filter(Boolean).join(" · ") || "无ASIN/FNSKU";
			const item = row.corresponding_item
				? `<a href="/app/item/${encodeURIComponent(row.corresponding_item)}">${this.esc(row.corresponding_item)}</a><small>${this.esc(row.item_name || "")}</small>`
				: `<em>未绑定ERPNext物料</em>`;
			const storeNote = row.platform === "AWD" && row.source_store_count > 1 ? `${row.source_store_count}个配置返回相同库存` : this.translate(row.country || "");
			return `<tr><td><span class="aiv-badge ${row.platform.toLowerCase()}">${row.platform}</span></td><td><div class="aiv-product">${image}<span><b>${this.esc(row.product_name || row.seller_sku || "未命名商品")}</b><small>SKU ${this.esc(row.seller_sku || "—")}</small><small>${this.esc(identity)}</small></span></div></td><td>${item}</td><td><b>${this.esc(row.amazon_store || "—")}</b><small>${this.esc(storeNote)}</small></td><td>${this.date(row.snapshot_at)}</td><td class="num strong">${this.num(row.total)}</td><td class="num positive">${this.num(row.available)}</td><td class="num">${this.num(row.reserved)}</td><td class="num">${this.num(row.inbound)}</td><td class="num ${row.unavailable ? "negative" : ""}">${this.num(row.unavailable)}</td><td class="num">${this.num(row.replenishment)}</td></tr>`;
		}).join("") || `<tr><td colspan="11" class="aiv-empty">当前筛选范围没有库存快照</td></tr>`);
	}

	translate(value) { return ({ "United States": "美国", Canada: "加拿大", Mexico: "墨西哥", Brazil: "巴西" })[value] || value; }
	num(value) { return Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 2 }); }
	date(value) { return value ? String(value).replace("T", " ").replace(/\.\d+$/, "").slice(0, 19) : "—"; }
	esc(value) { return frappe.utils.escape_html(String(value ?? "")); }

	styles() { return `
		.amazon-inventory-page .layout-main-section{background:#f5f7fb}.aiv-shell{padding:4px 4px 28px;color:#172033}.aiv-hero{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:24px 26px;border:1px solid #dce5f2;border-radius:16px;background:linear-gradient(120deg,#102a56,#185a9d);color:#fff;box-shadow:0 12px 28px rgba(30,67,122,.16)}.aiv-hero span,.aiv-card header span,.aiv-system header span{font-size:10px;font-weight:800;letter-spacing:.13em}.aiv-hero span{color:#8de8ff}.aiv-hero h2{margin:5px 0 4px;color:#fff;font-size:25px}.aiv-hero p{margin:0;color:#d7e7fb}.aiv-hero nav{display:flex;gap:8px;flex-wrap:wrap}.aiv-hero a{padding:8px 12px;border:1px solid rgba(255,255,255,.3);border-radius:9px;color:#fff;background:rgba(255,255,255,.1);text-decoration:none}.aiv-filters{display:flex;align-items:flex-end;gap:9px;flex-wrap:wrap;margin:14px 0;padding:13px;border:1px solid #dde5f0;border-radius:13px;background:#fff}.aiv-filters label{display:flex;flex-direction:column;gap:4px;color:#617089;font-size:11px}.aiv-filters input,.aiv-filters select{height:32px;min-width:132px;border:1px solid #ced8e6;border-radius:8px;background:#fff;padding:0 9px;color:#25324a}.aiv-filters .aiv-search{flex:1;min-width:190px}.aiv-filters .aiv-search input{width:100%}.aiv-system-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.aiv-system,.aiv-card{border:1px solid #dce4ef;border-radius:15px;background:#fff;box-shadow:0 7px 20px rgba(39,60,96,.06)}.aiv-system{padding:17px}.aiv-system header,.aiv-card>header{display:flex;align-items:center;justify-content:space-between;gap:12px}.aiv-system header h3,.aiv-card header h3{margin:3px 0 0;font-size:17px;color:#1e293b}.aiv-system header small,.aiv-card header small{color:#7b8799}.aiv-system.fba header span{color:#2563eb}.aiv-system.awd header span{color:#7c3aed}.aiv-kpis{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-top:14px}.aiv-kpis div{padding:10px;border-radius:10px;background:#f6f8fc}.aiv-kpis span,.aiv-kpis b{display:block}.aiv-kpis span{color:#738097;font-size:10px}.aiv-kpis b{margin-top:4px;color:#172033;font-size:18px}.aiv-card{margin-top:14px;padding:17px}.aiv-chart{height:310px}.aiv-chart-card>header{margin-bottom:8px}.aiv-table-card{padding:0;overflow:hidden}.aiv-table-card>header{padding:17px 18px;border-bottom:1px solid #e5ebf3}.aiv-table-note{color:#64748b}.aiv-notice{padding:9px 18px;border-bottom:1px solid #e7edf5;background:#f2f6fd;color:#52627a;font-size:11px}.aiv-table-wrap{max-height:72vh;overflow:auto}.aiv-table-wrap table{width:100%;min-width:1400px;border-collapse:collapse}.aiv-table-wrap th{position:sticky;z-index:2;top:0;padding:11px 10px;border-bottom:1px solid #dbe3ee;background:#f7f9fc;color:#536176;font-size:11px;white-space:nowrap;text-align:left}.aiv-table-wrap th[data-sort]{cursor:pointer}.aiv-table-wrap th[data-sort]:hover{color:#2563eb}.aiv-table-wrap td{padding:10px;border-bottom:1px solid #edf1f6;vertical-align:middle;color:#334155;font-size:12px}.aiv-table-wrap tbody tr:hover{background:#f8fbff}.aiv-table-wrap td small,.aiv-table-wrap td b{display:block}.aiv-table-wrap td small{margin-top:3px;color:#8490a2}.aiv-table-wrap td a{font-weight:700}.aiv-table-wrap td em{color:#b45309;font-style:normal}.aiv-product{display:flex;align-items:center;gap:9px;min-width:270px}.aiv-product img,.aiv-product i{display:grid;place-items:center;width:42px;height:42px;flex:0 0 42px;border-radius:9px;background:#edf3ff;object-fit:cover;color:#315da8;font-size:10px;font-style:normal;font-weight:800}.aiv-product img:hover{position:relative;z-index:5;transform:scale(2.8);box-shadow:0 8px 30px rgba(15,23,42,.25)}.aiv-badge{display:inline-flex;padding:4px 8px;border-radius:7px;font-size:10px;font-weight:800}.aiv-badge.fba{background:#e8f0ff;color:#1d5ec6}.aiv-badge.awd{background:#f0eaff;color:#7037c5}.aiv-table-wrap .num{text-align:right;font-variant-numeric:tabular-nums}.aiv-table-wrap .strong{font-weight:800}.positive{color:#07855b!important}.negative{color:#d33f49!important}.aiv-empty{height:130px;text-align:center!important;color:#8793a4!important}.amazon-inventory-page.is-loading .aiv-shell{opacity:.55;pointer-events:none}@media(max-width:1100px){.aiv-system-grid{grid-template-columns:1fr}.aiv-kpis{grid-template-columns:repeat(3,1fr)}}@media(max-width:700px){.aiv-hero{align-items:flex-start;flex-direction:column}.aiv-kpis{grid-template-columns:repeat(2,1fr)}.aiv-filters label,.aiv-filters input,.aiv-filters select{width:100%}.aiv-filters label{flex:1 1 45%}}
	`; }
}
