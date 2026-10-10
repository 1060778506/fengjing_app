frappe.pages["amazon-inbound"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: "Amazon入库货件中心", single_column: true });
	wrapper.amazonInbound = new AmazonInboundCenter(wrapper, page);
};

frappe.pages["amazon-inbound"].on_page_show = function (wrapper) {
	if (wrapper.amazonInbound?.loaded) wrapper.amazonInbound.load();
};

class AmazonInboundCenter {
	constructor(wrapper, page) {
		this.wrapper = wrapper;
		this.page = page;
		this.rows = [];
		this.data = {};
		this.sort = { key: "updated_at", direction: -1 };
		this.render();
		this.bind();
		this.load();
	}

	render() {
		$(this.wrapper).addClass("amazon-inbound-page");
		this.$ = $(this.page.main).html(`
			<style>${this.styles()}</style>
			<div class="ain-shell">
				<section class="ain-hero"><div><span>AMAZON INBOUND</span><h2>Amazon入库货件中心</h2><p>统一查看 FBA、AWD 与 GWD 入库货件、目的仓、接收数量及差异。</p></div><nav><a href="/app/amazon-inventory">库存总览</a><a href="/app/amazon-ledger">库存分类账</a><a href="/app/amazon_configuration">Amazon配置中心</a></nav></section>
				<section class="ain-program-tabs">
					<button class="active" data-program="">全部</button><button data-program="FBA">FBA</button><button data-program="AWD">AWD</button><button data-program="GWD">GWD</button><button data-program="Unrecognized">未识别</button>
				</section>
				<section class="ain-filters">
					<label>店铺<select data-filter="amazon_store"><option value="">全部店铺</option></select></label>
					<label>货件状态<select data-filter="status"><option value="">全部状态</option></select></label>
					<label>目的仓<select data-filter="destination"><option value="">全部目的仓</option></select></label>
					<label>开始日期<input type="date" data-filter="date_from"></label><label>结束日期<input type="date" data-filter="date_to"></label>
					<label class="ain-search">货件 / 订单 / 追踪号<input type="search" data-filter="search" placeholder="输入关键词"></label>
					<button class="btn btn-primary ain-query">查询</button><button class="btn btn-default ain-reset">重置</button>
				</section>
				<section class="ain-overview" data-overview></section>
				<section class="ain-card ain-status-card"><header><div><span>货件状态</span><h3>当前筛选状态分布</h3></div></header><div class="ain-statuses" data-statuses></div></section>
				<section class="ain-card ain-table-card">
					<header><div><span>入库货件</span><h3>FBA / AWD / GWD 统一明细</h3></div><div class="ain-table-note"></div></header>
					<div class="ain-table-wrap"><table><thead><tr>
						<th data-sort="program">体系</th><th data-sort="shipment_id">货件 / 入库单</th><th data-sort="amazon_store">店铺</th><th data-sort="destination_code">目的仓</th><th data-sort="status">状态</th><th class="num" data-sort="item_count">SKU</th><th class="num" data-sort="expected_quantity">预计</th><th class="num" data-sort="shipped_quantity">已发</th><th class="num" data-sort="received_quantity">已收</th><th class="num" data-sort="in_transit_quantity">在途</th><th class="num" data-sort="quantity_difference">差异</th><th data-sort="updated_at">更新时间</th>
					</tr></thead><tbody></tbody></table></div>
				</section>
			</div>`);
	}

	bind() {
		this.$.on("click", ".ain-program-tabs button", (event) => {
			this.$.find(".ain-program-tabs button").removeClass("active");
			$(event.currentTarget).addClass("active");
			this.program = event.currentTarget.dataset.program || "";
			this.load();
		});
		this.$.on("click", ".ain-query", () => this.load());
		this.$.on("click", ".ain-reset", () => {
			this.$.find("[data-filter]").val("");
			this.$.find(".ain-program-tabs button").removeClass("active").first().addClass("active");
			this.program = "";
			this.load();
		});
		this.$.on("keydown", "[data-filter=search]", (event) => { if (event.key === "Enter") this.load(); });
		this.$.on("click", "th[data-sort]", (event) => {
			const key = event.currentTarget.dataset.sort;
			this.sort.direction = this.sort.key === key ? -this.sort.direction : 1;
			this.sort.key = key;
			this.renderTable();
		});
	}

	filters() {
		const filters = { program: this.program || "" };
		this.$.find("[data-filter]").each((_, element) => { filters[element.dataset.filter] = element.value || ""; });
		return filters;
	}

	async load() {
		if (this.loading) return;
		this.loading = true;
		this.$.addClass("is-loading");
		try {
			const response = await frappe.call({
				method: "fengjing_app.fengjing_business.page.amazon_inbound.amazon_inbound.get_inbound_data",
				args: { filters: this.filters() },
			});
			this.data = response.message || {};
			this.rows = this.data.rows || [];
			this.fillOptions();
			this.renderOverview();
			this.renderStatuses();
			this.renderTable();
			this.loaded = true;
			if (this.data.limited) frappe.show_alert({ message: "结果超过2000条，表格显示最新2000条。", indicator: "orange" }, 8);
		} catch (error) {
			console.error(error);
			frappe.msgprint({ title: "入库货件加载失败", message: error.message || "请查看错误日志。", indicator: "red" });
		} finally {
			this.loading = false;
			this.$.removeClass("is-loading");
		}
	}

	fillOptions() {
		this.setOptions("amazon_store", this.data.options?.stores || [], "全部店铺");
		this.setOptions("status", this.data.options?.statuses || [], "全部状态");
		this.setOptions("destination", this.data.options?.destinations || [], "全部目的仓");
	}

	setOptions(name, values, emptyLabel) {
		const select = this.$.find(`[data-filter=${name}]`);
		const current = select.val();
		select.html(`<option value="">${emptyLabel}</option>${values.map((value) => `<option value="${this.esc(value)}">${this.esc(value)}</option>`).join("")}`);
		select.val(current);
	}

	renderOverview() {
		const summary = this.data.summary || {};
		const programs = summary.programs || {};
		const cards = [
			["全部货件", summary.shipments, `SKU ${this.num(summary.items)} · 已收 ${this.num(summary.received)}`, "all"],
			["FBA", programs.FBA?.shipments, `已发 ${this.num(programs.FBA?.shipped)} · 已收 ${this.num(programs.FBA?.received)}`, "fba"],
			["AWD", programs.AWD?.shipments, `预计 ${this.num(programs.AWD?.expected)} · 已收 ${this.num(programs.AWD?.received)}`, "awd"],
			["GWD", programs.GWD?.shipments, `预计 ${this.num(programs.GWD?.expected)} · 已收 ${this.num(programs.GWD?.received)}`, "gwd"],
			["在途数量", summary.in_transit, `总体差异 ${this.num(summary.difference)}`, "transit"],
			["未识别目的仓", programs.Unrecognized?.shipments, programs.Unrecognized?.shipments ? "需要补充目的仓映射" : "当前全部已识别", "unknown"],
		];
		this.$.find("[data-overview]").html(cards.map(([label, value, note, tone]) => `<article class="ain-kpi ${tone}"><span>${label}</span><b>${this.num(value)}</b><small>${note}</small></article>`).join(""));
	}

	renderStatuses() {
		const entries = Object.entries(this.data.summary?.status_counts || {}).sort((a, b) => b[1] - a[1]);
		this.$.find("[data-statuses]").html(entries.map(([status, count]) => `<button data-status-value="${this.esc(status)}"><span>${this.esc(status)}</span><b>${this.num(count)}</b></button>`).join("") || "<em>当前没有货件</em>");
		this.$.off("click.ainStatus").on("click.ainStatus", "[data-status-value]", (event) => {
			this.$.find("[data-filter=status]").val(event.currentTarget.dataset.statusValue);
			this.load();
		});
	}

	renderTable() {
		const key = this.sort.key;
		const direction = this.sort.direction;
		const rows = [...this.rows].sort((a, b) => {
			const av = a[key] ?? "", bv = b[key] ?? "";
			if (typeof av === "number" || typeof bv === "number") return (Number(av) - Number(bv)) * direction;
			return String(av).localeCompare(String(bv), "zh-CN") * direction;
		});
		this.$.find(".ain-table-note").html(`共 <b>${this.num(this.data.row_count)}</b> 个货件`);
		this.$.find("tbody").html(rows.map((row) => {
			const url = `/app/${row.route}/${encodeURIComponent(row.name)}`;
			const destination = row.destination_warehouse || row.destination_code || "未提供";
			const destinationNote = [row.destination_code !== destination ? row.destination_code : "", row.destination_label].filter(Boolean).join(" · ");
			const statusClass = this.statusClass(row.status);
			return `<tr class="${row.last_error ? "has-error" : ""}">
				<td><span class="ain-badge ${String(row.program || "unknown").toLowerCase()}">${this.esc(row.program || "未识别")}</span></td>
				<td><a class="ain-shipment" href="${url}">${this.esc(row.shipment_id || row.name)}</a><small>${this.esc(row.shipment_name || row.parent_reference || "")}</small></td>
				<td><b>${this.esc(row.amazon_store || "—")}</b><small>${this.esc(row.marketplace_id || "")}</small></td>
				<td><b>${this.esc(destination)}</b><small>${this.esc(destinationNote)}</small></td>
				<td><span class="ain-status ${statusClass}">${this.esc(row.status || "未知")}</span><small>${this.esc(row.sync_status || "")}</small></td>
				<td class="num">${this.num(row.item_count)}</td><td class="num">${this.num(row.expected_quantity)}</td><td class="num">${row.shipped_quantity == null ? "—" : this.num(row.shipped_quantity)}</td><td class="num positive">${this.num(row.received_quantity)}</td><td class="num">${this.num(row.in_transit_quantity)}</td><td class="num ${Number(row.quantity_difference) ? "warning" : ""}">${this.num(row.quantity_difference)}</td><td>${this.date(row.updated_at)}</td>
			</tr>`;
		}).join("") || `<tr><td colspan="12" class="ain-empty">当前筛选范围没有入库货件</td></tr>`);
	}

	statusClass(status) {
		const value = String(status || "").toUpperCase();
		if (["CLOSED", "COMPLETED", "RECEIVED", "DELIVERED"].some((key) => value.includes(key))) return "success";
		if (["CANCEL", "ERROR", "FAILED", "DELETED"].some((key) => value.includes(key))) return "danger";
		if (["SHIPPED", "IN_TRANSIT", "IN-TRANSIT"].some((key) => value.includes(key))) return "progress";
		return "neutral";
	}

	num(value) { return Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 2 }); }
	date(value) { return value ? String(value).replace("T", " ").replace(/\.\d+$/, "").slice(0, 19) : "—"; }
	esc(value) { return frappe.utils.escape_html(String(value ?? "")); }

	styles() { return `
		.amazon-inbound-page .layout-main-section{background:#f5f7fb}.ain-shell{padding:4px 4px 28px;color:#172033}.ain-hero{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:24px 26px;border:1px solid #dce5f2;border-radius:16px;background:linear-gradient(120deg,#17345f,#1e6b8e);color:#fff;box-shadow:0 12px 28px rgba(30,67,122,.15)}.ain-hero span,.ain-card header span{font-size:10px;font-weight:800;letter-spacing:.13em}.ain-hero span{color:#8de8ff}.ain-hero h2{margin:5px 0 4px;color:#fff;font-size:25px}.ain-hero p{margin:0;color:#d7e7fb}.ain-hero nav{display:flex;gap:8px;flex-wrap:wrap}.ain-hero a{padding:8px 12px;border:1px solid rgba(255,255,255,.3);border-radius:9px;color:#fff;background:rgba(255,255,255,.1);text-decoration:none}.ain-program-tabs{display:flex;gap:8px;margin:14px 0 9px}.ain-program-tabs button{padding:8px 18px;border:1px solid #ced9e8;border-radius:9px;background:#fff;color:#52627a;font-weight:700}.ain-program-tabs button.active{border-color:#2563eb;background:#2563eb;color:#fff}.ain-filters{display:flex;align-items:flex-end;gap:9px;flex-wrap:wrap;padding:13px;border:1px solid #dde5f0;border-radius:13px;background:#fff}.ain-filters label{display:flex;flex-direction:column;gap:4px;color:#617089;font-size:11px}.ain-filters input,.ain-filters select{height:32px;min-width:130px;border:1px solid #ced8e6;border-radius:8px;background:#fff;padding:0 9px;color:#25324a}.ain-filters .ain-search{flex:1;min-width:190px}.ain-filters .ain-search input{width:100%}.ain-overview{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-top:14px}.ain-kpi,.ain-card{border:1px solid #dce4ef;border-radius:14px;background:#fff;box-shadow:0 7px 20px rgba(39,60,96,.06)}.ain-kpi{padding:15px}.ain-kpi span,.ain-kpi b,.ain-kpi small{display:block}.ain-kpi span{color:#68758a;font-size:11px}.ain-kpi b{margin:5px 0;color:#172033;font-size:24px}.ain-kpi small{color:#7d899b;line-height:1.4}.ain-kpi.fba{border-top:3px solid #2563eb}.ain-kpi.awd{border-top:3px solid #7c3aed}.ain-kpi.gwd{border-top:3px solid #0f9f78}.ain-kpi.unknown{border-top:3px solid #dc4c58}.ain-card{margin-top:14px}.ain-card>header{display:flex;align-items:center;justify-content:space-between;padding:16px 18px;border-bottom:1px solid #e5ebf3}.ain-card header span{color:#52719f}.ain-card header h3{margin:3px 0 0;color:#1e293b;font-size:17px}.ain-statuses{display:flex;gap:8px;flex-wrap:wrap;padding:12px 18px}.ain-statuses button{display:flex;align-items:center;gap:9px;padding:7px 11px;border:1px solid #dce4ef;border-radius:9px;background:#f8fafc;color:#536176}.ain-statuses button b{color:#172033}.ain-table-card{overflow:hidden}.ain-table-note{color:#64748b}.ain-table-wrap{max-height:72vh;overflow:auto}.ain-table-wrap table{width:100%;min-width:1560px;border-collapse:collapse}.ain-table-wrap th{position:sticky;z-index:2;top:0;padding:11px 10px;border-bottom:1px solid #dbe3ee;background:#f7f9fc;color:#536176;font-size:11px;white-space:nowrap;text-align:left;cursor:pointer}.ain-table-wrap td{padding:10px;border-bottom:1px solid #edf1f6;vertical-align:middle;color:#334155;font-size:12px}.ain-table-wrap tbody tr:hover{background:#f8fbff}.ain-table-wrap td small,.ain-table-wrap td b{display:block}.ain-table-wrap td small{margin-top:3px;color:#8490a2}.ain-shipment{font-weight:800}.ain-badge,.ain-status{display:inline-flex;padding:4px 8px;border-radius:7px;font-size:10px;font-weight:800}.ain-badge.fba{background:#e8f0ff;color:#1d5ec6}.ain-badge.awd{background:#f0eaff;color:#7037c5}.ain-badge.gwd{background:#e5f8f1;color:#087c60}.ain-badge.unrecognized{background:#ffebed;color:#bc3541}.ain-status.success{background:#e6f7ef;color:#087b57}.ain-status.progress{background:#e8f1ff;color:#1d5fc6}.ain-status.danger{background:#ffeaec;color:#bd3440}.ain-status.neutral{background:#eef1f5;color:#596679}.ain-table-wrap .num{text-align:right;font-variant-numeric:tabular-nums}.positive{color:#07855b!important}.warning{color:#c46b09!important;font-weight:800}.has-error{background:#fff8f8}.ain-empty{height:130px;text-align:center!important;color:#8793a4!important}.amazon-inbound-page.is-loading .ain-shell{opacity:.55;pointer-events:none}@media(max-width:1200px){.ain-overview{grid-template-columns:repeat(3,1fr)}}@media(max-width:760px){.ain-hero{align-items:flex-start;flex-direction:column}.ain-overview{grid-template-columns:repeat(2,1fr)}.ain-filters label,.ain-filters input,.ain-filters select{width:100%}.ain-program-tabs{overflow:auto}}
	`; }
}
