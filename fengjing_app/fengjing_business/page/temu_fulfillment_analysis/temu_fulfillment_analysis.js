const TEMU_FULFILLMENT_ANALYSIS_API =
	"fengjing_app.fengjing_business.page.temu_fulfillment_analysis.temu_fulfillment_analysis.get_analysis";

frappe.pages["temu_fulfillment_analysis"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: "Temu履约服务账单分析", single_column: true });
	$(wrapper).addClass("tfa-page");
	wrapper.temuFulfillmentAnalysis = new TemuFulfillmentAnalysis(page);
};

frappe.pages["temu_fulfillment_analysis"].on_page_show = function (wrapper) {
	wrapper.temuFulfillmentAnalysis?.load();
};

class TemuFulfillmentAnalysis {
	constructor(page) {
		this.root = $(page.main);
		this.state = {
			cost_center: "", payment_month: "", fee_type: "", search: "",
			bill_file: "", payment_file: "", bill_page: 1, payment_page: 1,
		};
		this.requestId = 0;
		this.renderShell();
		this.bindEvents();
		page.add_inner_button("刷新数据", () => this.load());
	}

	escape(value) { return frappe.utils.escape_html(String(value ?? "")); }
	money(totals) {
		if (!totals?.length) return "CNY 0.00";
		return totals.map((item) => `${this.escape(item.currency)} ${Number(item.amount).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`).join(" / ");
	}
	empty() { return '<div class="tfa-empty">当前筛选没有数据</div>'; }

	renderShell() {
		this.root.html(`
			<div class="tfa-shell">
				<header class="tfa-hero">
					<div><span>TEMU FULFILLMENT</span><h2>履约服务账单分析</h2><p>分别查看账单详情和每份履约服务缴费记录。</p></div>
					<a href="/app/temu_managed_bookkee">查看导入批次</a>
				</header>
				<div class="tfa-note">左侧账单详情按出账日筛选，使用每个店铺最新的累计文件；右侧缴费记录按缴费发起日筛选，列出对应月份的每份文件。跨月费用会使两侧金额不同，不要将两侧金额相加。</div>
				<div class="tfa-filters">
					<label>店铺<select data-filter="cost_center"><option value="">全部店铺</option></select></label>
					<label>核对月份<select data-filter="payment_month"><option value="all">全部月份</option></select></label>
					<label>费用类型<select data-filter="fee_type"><option value="">全部类型</option></select></label>
					<label>搜索运单号 / SKU / 文件<input type="search" data-filter="search" placeholder="输入关键词"></label>
				</div>
				<div class="tfa-columns">
					<section class="tfa-panel" data-kind="bill">
						<div class="tfa-panel-head"><div><h3>履约服务账单》账单详情</h3><small>按出账日 · 每个店铺最新的累计文件</small></div></div>
						<div class="tfa-source-total"></div><div class="tfa-type-summary"></div>
						<h4>账单文件 <span class="tfa-file-count"></span></h4><div class="tfa-files"></div>
						<div class="tfa-detail-head"></div><div class="tfa-table-wrap tfa-detail"></div><div class="tfa-pager"></div>
					</section>
					<section class="tfa-panel" data-kind="payment">
						<div class="tfa-panel-head"><div><h3>履约服务账单》履约服务缴费记录</h3><small>按缴费发起日 · 重复记录只计算一次</small></div></div>
						<div class="tfa-source-total"></div><div class="tfa-type-summary"></div>
						<h4>缴费文件 <span class="tfa-file-count"></span></h4><div class="tfa-files"></div>
						<div class="tfa-detail-head"></div><div class="tfa-table-wrap tfa-detail"></div><div class="tfa-pager"></div>
					</section>
				</div>
			</div>
		`);
	}

	bindEvents() {
		this.root.on("change", "select[data-filter]", (event) => {
			const field = event.currentTarget.dataset.filter;
			this.state[field] = event.currentTarget.value;
			if (field === "cost_center") {
				this.state.payment_month = "";
				this.state.fee_type = "";
				this.state.bill_file = "";
				this.state.payment_file = "";
			}
			if (field === "payment_month") {
				this.state.bill_file = "";
				this.state.payment_file = "";
			}
			this.state.bill_page = 1;
			this.state.payment_page = 1;
			this.load();
		});
		this.root.on("input", 'input[data-filter="search"]', frappe.utils.debounce((event) => {
			this.state.search = event.target.value.trim();
			this.state.bill_page = 1;
			this.state.payment_page = 1;
			this.load();
		}, 320));
		this.root.on("click", "button[data-file-kind]", (event) => {
			const field = `${event.currentTarget.dataset.fileKind}_file`;
			this.state[field] = event.currentTarget.dataset.fileId || "";
			this.state[`${event.currentTarget.dataset.fileKind}_page`] = 1;
			this.load();
		});
		this.root.on("click", "button[data-page-kind]", (event) => {
			this.state[`${event.currentTarget.dataset.pageKind}_page`] = Number(event.currentTarget.dataset.page);
			this.load();
		});
	}

	async load() {
		const requestId = ++this.requestId;
		this.root.find(".tfa-shell").addClass("is-loading");
		try {
			const response = await frappe.call({ method: TEMU_FULFILLMENT_ANALYSIS_API, args: this.state });
			if (requestId === this.requestId) this.render(response.message || {});
		} catch (error) {
			if (requestId === this.requestId) {
				console.error(error);
				frappe.msgprint({ title: "履约服务账单读取失败", message: error?.message || "请检查导入批次和访问权限。", indicator: "red" });
			}
		} finally {
			if (requestId === this.requestId) this.root.find(".tfa-shell").removeClass("is-loading");
		}
	}

	selectOptions(field, values, firstLabel, firstValue = "") {
		const select = this.root.find(`select[data-filter="${field}"]`);
		select.html(`<option value="${this.escape(firstValue)}">${this.escape(firstLabel)}</option>`
			+ (values || []).map((value) => `<option value="${this.escape(value)}">${this.escape(field === "payment_month" ? `${value.slice(0, 4)}年${value.slice(5)}月` : value)}</option>`).join(""));
		select.val(this.state[field]);
		if (select.val() === null) { this.state[field] = firstValue; select.val(firstValue); }
	}

	render(data) {
		this.state.payment_month = data.selected_month || "all";
		this.selectOptions("cost_center", data.stores, "全部店铺");
		this.selectOptions("payment_month", data.months, "全部月份", "all");
		this.selectOptions("fee_type", data.fee_types, "全部类型");
		for (const kind of ["bill", "payment"]) this.renderSource(kind, data[kind] || {}, data.files || []);
	}

	renderSource(kind, source, allFiles) {
		const panel = this.root.find(`.tfa-panel[data-kind="${kind}"]`);
		const files = allFiles.filter((file) => file.kind === kind);
		panel.find(".tfa-source-total").html(`<b>${this.money(source.totals)}</b><span>${Number(source.count || 0).toLocaleString("zh-CN")} 笔费用</span>`);
		panel.find(".tfa-type-summary").html((source.by_type || []).map((type) =>
			`<span>${this.escape(type.name)} <b>${this.money(type.totals)}</b> · ${type.count} 笔</span>`).join("") || this.empty());
		panel.find(".tfa-file-count").text(`${files.length} 份`);
		this.renderFiles(panel, kind, files);
		this.renderDetails(panel, kind, source.detail || {});
	}

	renderFiles(panel, kind, files) {
		const selected = this.state[`${kind}_file`];
		const allLabel = kind === "bill" ? "全部账单文件" : "全部缴费文件";
		const cards = [`<button type="button" data-file-kind="${kind}" data-file-id="" class="tfa-file ${!selected ? "is-active" : ""}"><b>${allLabel}</b><small>查看此侧全部已导入记录</small></button>`];
		for (const file of files) {
			const from = kind === "bill" ? file.billed_from : file.payment_from;
			const to = kind === "bill" ? file.billed_to : file.payment_to;
			const dateLabel = kind === "bill" ? "出账" : "缴费发起";
			const range = from && to
				? `${dateLabel} ${this.escape(from)}${from === to ? "" : ` 至 ${this.escape(to)}`}`
				: `${dateLabel}日期未提供`;
			cards.push(`<button type="button" data-file-kind="${kind}" data-file-id="${this.escape(file.id)}" class="tfa-file ${selected === file.id ? "is-active" : ""}" title="${this.escape(file.name)}"><b>${this.escape(file.name)}</b><small>${range}</small><small>${file.effective_count} 笔 · ${this.money(file.effective_totals)}</small></button>`);
		}
		panel.find(".tfa-files").html(cards.join(""));
	}

	renderDetails(panel, kind, detail) {
		const rows = detail.rows || [];
		const page = detail.page || 1;
		const pages = detail.page_count || 1;
		panel.find(".tfa-detail-head").text(`费用明细 · ${Number(detail.count || 0).toLocaleString("zh-CN")} 笔`);
		panel.find(".tfa-detail").html(rows.length ? `<table><thead><tr><th>出账 / 缴费时间</th><th>费用类型</th><th>运单号 / SKU</th><th>金额</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${this.escape(row.billed_at || "—")}<small>缴费 ${this.escape(row.payment_initiated_at || "—")}</small></td><td>${this.escape(row.fee_type || "—")}<small>${this.escape(row.sheet || "")}</small></td><td>${this.escape(row.reference || "—")}<small title="${this.escape(row.source_file || "")}">${this.escape(row.source_file || "")}</small></td><td class="tfa-amount">${this.money([{currency: row.currency, amount: row.amount}])}</td></tr>`).join("")}</tbody></table>` : this.empty());
		panel.find(".tfa-pager").html(`<button type="button" class="btn btn-default btn-sm" data-page-kind="${kind}" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>上一页</button><span>${page} / ${pages}</span><button type="button" class="btn btn-default btn-sm" data-page-kind="${kind}" data-page="${page + 1}" ${page >= pages ? "disabled" : ""}>下一页</button>`);
	}
}
