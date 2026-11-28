(() => {
	const API = "fengjing_app.fengjing_business.tiktok_analysis";
	const VIEWS = {
		orders: {
			title: "订单分析", description: "按下单日期查看订单、销售额和商品销量。",
			doctype: "tiktok-order-record", table: "订单明细", placeholder: "订单号、SKU 或商品名称",
			columns: ["下单时间", "订单", "商品 / SKU", "件数", "商品金额", "买家支付", "状态", "原始记录"],
		},
		aftersales: {
			title: "售后分析", description: "按售后申请日期查看退款状态、原因及对应原订单。",
			doctype: "tiktok-aftersales-record", table: "退货退款明细", placeholder: "售后编号、订单号或原因",
			columns: ["申请时间", "售后编号", "原订单", "类型 / 原因", "退款金额", "状态", "原始记录"],
		},
		payouts: {
			title: "财务交易中心", description: "从打款追到账单，再查看每笔订单收入、费用和调整。",
			doctype: "tiktok-payout-record", table: "打款记录", placeholder: "打款编号或状态",
			columns: ["打款时间", "打款编号", "打款金额", "状态", "原始记录"],
		},
		statements: {
			title: "财务交易中心", description: "按结算日期查看账单、关联打款及交易明细。",
			doctype: "tiktok-statement-record", table: "结算账单", placeholder: "账单编号、打款编号或状态",
			columns: ["结算时间", "结算账单", "打款编号", "结算金额", "状态", "原始记录"],
		},
		transactions: {
			title: "财务交易中心", description: "交易按所属账单归月，订单收入、运输费用、平台费用与调整分别列示。",
			doctype: "tiktok-statement-transaction-record", table: "账单交易明细", placeholder: "交易编号、账单编号或订单号",
			columns: ["账单日期", "交易 / 订单", "账单", "收入", "运输费用", "费用及税费", "调整", "结算金额", "原始记录"],
		},
		performance: {
			title: "店铺表现", description: "查看 TikTok 报告的每日 GMV、订单、访客和转化。",
			doctype: "tiktok-shop-performance-record", table: "每日店铺表现", placeholder: "",
			columns: ["日期", "GMV", "订单量", "浏览量", "访客数", "转化率", "原始记录"],
		},
	};
	const FINANCE = { payouts: "打款记录", statements: "结算账单", transactions: "账单交易明细" };
	const STATUSES = {
		UNPAID: "待支付", ON_HOLD: "暂缓处理", AWAITING_SHIPMENT: "待发货",
		AWAITING_COLLECTION: "待揽收", PARTIALLY_SHIPPING: "部分已发货", IN_TRANSIT: "运输中",
		DELIVERED: "已送达", COMPLETED: "已完成", CANCELLED: "已取消", CANCELED: "已取消",
		PAID: "已付款", PAYMENT_SUCCEEDED: "打款成功", PENDING: "待处理",
		PAYMENT_PROCESSING: "打款处理中", PROCESSING: "处理中", FAILED: "失败",
		RETURN_AND_REFUND: "退货退款", REFUND: "仅退款", REPLACEMENT: "换货", EXCHANGE: "换货",
		RETURN_OR_REFUND_REQUEST_PENDING: "待审核", AWAITING_BUYER_SHIP: "待买家寄回",
		BUYER_SHIPPED_ITEM: "买家已寄出", RETURN_OR_REFUND_REQUEST_SUCCESS: "售后已批准",
		RETURN_OR_REFUND_REQUEST_COMPLETE: "退款已完成", RETURN_OR_REFUND_REQUEST_REJECT: "已拒绝",
		RETURN_OR_REFUND_REQUEST_CANCEL: "已取消",
		ORDER: "订单", ADJUSTMENT: "调整", RESERVE: "预留款", REBATE: "返利",
	};
	const ICONS = { orders: "bag-check", aftersales: "arrow-return-left", payouts: "bank", statements: "receipt", transactions: "list-ul", performance: "bar-chart" };
	const esc = (value) => frappe.utils.escape_html(String(value ?? ""));
	const number = (value) => value == null || value === "" ? "—" : Number(value).toLocaleString("zh-CN");
	const money = (value, currency) => {
		if (value == null || value === "") return "—";
		return `${currency ? esc(currency) + " " : ""}${Number(value).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
	};
	const moneyGroups = (amounts) => Object.entries(amounts || {}).map(([currency, value]) => money(value, currency)).join("<br>") || "—";
	const link = (view, name) => `/app/${VIEWS[view].doctype}${name ? "/" + encodeURIComponent(name) : ""}`;

	class TikTokAnalysis {
		constructor(page, view) {
			this.view = view;
			this.page = 1;
			this.month = "";
			this.store = "";
			this.search = "";
			this.sequence = 0;
			this.chartResize = new ResizeObserver(() => this.chart?.resize());
			this.root = $('<div class="tka-shell"></div>').appendTo(page.main);
			this.chartResize.observe(this.root[0]);
			this.renderShell();
			this.bind();
			page.set_primary_action("刷新数据", () => this.load(), "refresh");
		}

		renderShell() {
			this.root.html(`
				<section class="tka-hero">
					<div class="tka-hero-main">
						<span class="tka-hero-icon"><i class="bi" data-icon></i></span>
						<div><h2 data-title></h2><p data-description></p></div>
					</div>
					<a data-record-list>原始记录 <i class="bi bi-box-arrow-up-right" aria-hidden="true"></i></a>
				</section>
				<section class="tka-filter">
					<div class="tka-finance-tabs" role="group" aria-label="财务类型" hidden></div>
					<div class="tka-month-row"><strong>月份</strong><div class="tka-months"></div><input type="month" data-month-input aria-label="选择月份"></div>
					<div class="tka-filter-row">
						<label>店铺 <select data-store aria-label="TikTok 店铺"></select></label>
						<label data-search-box>搜索 <input type="search" data-search></label>
					</div>
				</section>
				<div class="tka-feedback" role="status" hidden></div>
				<div class="tka-results">
					<section class="tka-kpis" aria-label="汇总"></section>
					<div class="tka-widgets">
						<section class="tka-card tka-chart-card"><header><h3>每日趋势</h3><select class="tka-chart-metric" aria-label="趋势指标" hidden></select></header><div class="tka-chart"></div></section>
						<section class="tka-card tka-groups-card" hidden><header><h3></h3></header><div class="tka-groups"></div></section>
					</div>
					<section class="tka-card tka-table-card">
						<header><h3 data-table-title></h3><span data-count></span></header>
						<div class="tka-records"></div><footer class="tka-pagination"></footer>
					</section>
				</div>`);
			this.renderTitle();
		}

		renderTitle() {
			const config = VIEWS[this.view];
			this.root.find("[data-title]").text(config.title);
			this.root.find("[data-icon]").attr({ class: `bi bi-${ICONS[this.view]}`, "aria-hidden": "true" });
			this.root.find("[data-description]").text(config.description);
			this.root.find("[data-record-list]").attr("href", link(this.view));
			this.root.find("[data-search-box]").prop("hidden", !config.placeholder);
			this.root.find("[data-search]").attr("placeholder", config.placeholder);
			this.root.find("[data-table-title]").text(config.table);
			if (FINANCE[this.view]) {
				this.root.find(".tka-finance-tabs").prop("hidden", false).html(Object.entries(FINANCE).map(([key, label]) =>
					`<button type="button" data-view="${key}" aria-pressed="${this.view === key}">${label}</button>`
				).join(""));
			}
		}

		bind() {
			this.root.on("click", "[data-view]", (event) => {
				const view = event.currentTarget.dataset.view;
				if (this.view === view) return;
				this.view = view;
				this.search = "";
				this.root.find("[data-search]").val("");
				this.renderTitle();
				this.filter();
			});
			this.root.on("click", "[data-month]", (event) => {
				this.month = event.currentTarget.dataset.month;
				this.filter();
			});
			this.root.on("change", "[data-month-input]", (event) => {
				if (event.currentTarget.value) {
					this.month = event.currentTarget.value;
					this.filter();
				}
			});
			this.root.on("change", "[data-store]", (event) => {
				this.store = event.currentTarget.value;
				this.month = "";
				this.filter();
			});
			this.root.on("input", "[data-search]", (event) => {
				this.search = event.currentTarget.value;
				clearTimeout(this.searchTimer);
				this.searchTimer = setTimeout(() => this.filter(), 320);
			});
			this.root.on("click", "[data-page]", (event) => {
				this.page = Number(event.currentTarget.dataset.page);
				this.load();
			});
			this.root.on("change", ".tka-chart-metric", () => this.renderChart());
			this.root.on("click", "[data-child-next]", (event) => {
				const button = event.currentTarget;
				this.loadChildren(button.closest(".tka-drill"), Number(button.dataset.childNext));
			});
			this.root.on("click", "[data-expand]", (event) => {
				const trigger = event.currentTarget;
				const child = trigger.closest("tr").nextElementSibling;
				child.hidden = !child.hidden;
				trigger.setAttribute("aria-expanded", String(!child.hidden));
				if (!child.hidden && !child.dataset.loaded) this.loadChildren(child, 1);
			});
		}

		filter() {
			this.page = 1;
			return this.load();
		}

		async load() {
			const sequence = ++this.sequence;
			const view = this.view;
			const feedback = this.root.find(".tka-feedback");
			feedback.prop("hidden", false).text("正在读取已同步数据…");
			this.root.find(".tka-results").attr("aria-busy", "true");
			try {
				const response = await frappe.call({
					method: API + ".get_analysis", type: "GET",
					args: { view, store: this.store, month: this.month, search: this.search, page: this.page },
				});
				if (sequence !== this.sequence) return;
				this.data = response.message;
				this.month = this.data.month || "";
				this.page = this.data.page;
				this.render();
				feedback.prop("hidden", true);
			} catch {
				if (sequence !== this.sequence) return;
				feedback.text("读取数据失败，可点击页面右上角重新加载。");
			} finally {
				if (sequence === this.sequence) this.root.find(".tka-results").attr("aria-busy", "false");
			}
		}

		render() {
			const data = this.data;
			this.store = data.store?.name || "";
			this.root.find(".tka-months").html((data.months || []).map(month =>
				`<button type="button" data-month="${esc(month)}" aria-pressed="${this.month === month}">${esc(month)}</button>`
			).join(""));
			this.root.find("[data-month-input]").val(this.month);
			this.root.find("[data-store]").html((data.stores || []).map(store =>
				`<option value="${esc(store.name)}">${esc(store.store_name || store.name)}</option>`
			).join("") || '<option value="">暂无店铺</option>').val(this.store);
			this.renderKpis();
			this.renderGroups();
			this.renderChart();
			this.root.find(".tka-records").html(this.table(this.view, data.rows));
			this.root.find("[data-count]").text(`${number(data.total)} 条${data.store?.shop_timezone ? " · " + data.store.shop_timezone : ""}`);
			this.root.find(".tka-pagination").prop("hidden", data.pages <= 1).html(`
				<button type="button" data-page="${data.page - 1}" ${data.page <= 1 ? "disabled" : ""}>上一页</button>
				<span>${data.page} / ${data.pages}</span>
				<button type="button" data-page="${data.page + 1}" ${data.page >= data.pages ? "disabled" : ""}>下一页</button>`);
		}

		renderKpis() {
			const metric = this.data.summary;
			const count = number(metric.count ?? 0);
			const metrics = {
				orders: [["订单数", count], ["已支付订单", number(metric.paid ?? 0)], ["已支付件数", number(metric.units ?? 0)],
					["商品金额", moneyGroups(metric.product_amounts)], ["买家支付", moneyGroups(metric.order_amounts)]],
				aftersales: [["售后申请", count], ["退款已完成", number(metric.completed ?? 0)], ["已完成退款金额", moneyGroups(metric.amounts)]],
				payouts: [["打款记录", count], ["打款金额", moneyGroups(metric.amounts)]],
				statements: [["账单数量", count], ["结算金额", moneyGroups(metric.amounts)]],
				transactions: [["账单交易", count], ["结算净额", moneyGroups(metric.amounts)]],
				performance: [["店铺 GMV", moneyGroups(metric.amounts)], ["订单量", number(metric.orders ?? 0)],
					["浏览量", number(metric.views)], ["已同步天数", count]],
			};
			this.root.find(".tka-kpis").html(metrics[this.view].map(([label, value]) =>
				`<article class="tka-kpi"><span>${label}</span><strong>${value}</strong></article>`
			).join(""));
		}

		image(url) {
			if (typeof url !== "string" || !url.startsWith("https://")) return "";
			return `<img src="${esc(url)}" alt="" width="34" height="34" loading="lazy" referrerpolicy="no-referrer">`;
		}

		renderGroups() {
			const isItems = this.view === "orders";
			const list = this.data.groups || [];
			const card = this.root.find(".tka-groups-card");
			card.prop("hidden", !["orders", "aftersales"].includes(this.view));
			this.root.find(".tka-widgets").toggleClass("has-groups", ["orders", "aftersales"].includes(this.view));
			card.find("h3").text(isItems ? "SKU 销量" : "售后申请原因");
			card.find(".tka-groups").html(list.map(row => `<article class="tka-group">
				${isItems ? this.image(row.image) : ""}<div><b>${esc(isItems ? row.product : row.reason)}</b>
				${isItems && row.sku ? `<small>${esc(row.sku)}</small>` : ""}</div>
				<strong>${number(isItems ? row.units : row.count)} ${isItems ? "件" : "笔"}</strong>
			</article>`).join("") || this.empty());
		}

		disposeChart() {
			this.chart?.dispose();
			this.chart = null;
		}

		renderChart() {
			this.disposeChart();
			const chart = this.root.find(".tka-chart");
			const trend = this.data.trend || {};
			const metrics = [{ label: trend.label || "笔数", points: trend.points || [] }, ...(trend.series || [])];
			const control = this.root.find(".tka-chart-metric");
			const index = Math.min(Number(control.val()) || 0, metrics.length - 1);
			control.html(metrics.map((metric, i) => `<option value="${i}">${esc(metric.label)}</option>`).join(""))
				.val(String(index)).prop("hidden", metrics.length < 2);
			const metric = metrics[index];
			if (!metric.points.length || !window.echarts) {
				chart.html(this.empty());
				return;
			}
			chart.empty();
			const text = getComputedStyle(chart[0]).getPropertyValue("--tka-muted").trim();
			this.chart = echarts.init(chart[0]);
			this.chart.setOption({
				textStyle: { color: text },
				grid: { left: 24, right: 20, top: 24, bottom: 19, containLabel: true },
				tooltip: { trigger: "axis", renderMode: "richText", confine: true },
				xAxis: { type: "category", data: metric.points.map(point => point.day), axisLabel: { hideOverlap: true, color: text } },
				yAxis: { type: "value", minInterval: metric.money ? 0.01 : 1, axisLabel: { color: text },
					splitLine: { lineStyle: { color: getComputedStyle(chart[0]).getPropertyValue("--tka-line").trim() } } },
				series: [{ name: metric.label, type: "line", data: metric.points.map(point => point.value), smooth: false,
					showSymbol: metric.points.length < 3, lineStyle: { color: "#138b88", width: 3 },
					itemStyle: { color: "#138b88" }, areaStyle: { color: "rgba(13,139,136,.12)" } }],
			});
		}

		empty() {
			return '<div class="tka-empty">当前筛选范围暂无已同步数据。</div>';
		}

		status(code) {
			return code ? `<span class="tka-status" title="${esc(code)}">${esc(STATUSES[code] || code)}</span>` : "—";
		}

		cells(view, row) {
			const fallback = (value) => esc(value || "—");
			const items = (row.items || []).map(item =>
				`<div class="tka-item">${this.image(item.image)}<div><b>${fallback(item.product)}</b><small>${fallback(item.sku)} · ${number(item.units)} 件</small></div></div>`
			).join("") || "—";
			const values = {
				orders: [fallback(row.date), fallback(row.source_order_id), items, number(row.units),
					money(row.product_amount, row.currency), money(row.amount, row.currency), this.status(row.status)],
				aftersales: [fallback(row.date), fallback(row.aftersales_id),
					row.order_record ? `<a href="${link("orders", row.order_record)}">${fallback(row.source_order_id)}</a>` : fallback(row.source_order_id),
					`${this.status(row.type)}<small>${fallback(row.reason)}</small>`,
					money(row.amount, row.currency), this.status(row.status)],
				payouts: [fallback(row.date), this.drill(row.payment_id), money(row.amount, row.currency), this.status(row.status)],
				statements: [fallback(row.date), this.drill(row.statement_id), fallback(row.payment_id),
					money(row.amount, row.currency), this.status(row.status)],
				transactions: [fallback(row.date), `<b>${fallback(row.transaction_id)}</b><small>${fallback(row.source_order_id || row.adjustment_id)} · ${this.status(row.transaction_type)}</small>`,
					fallback(row.statement_id), money(row.revenue_amount, row.currency), money(row.shipping_cost_amount, row.currency),
					money(row.fee_tax_amount, row.currency), money(row.adjustment_amount, row.currency), money(row.amount, row.currency)],
				performance: [fallback(row.date), money(row.amount, row.currency), number(row.order_count), number(row.views),
					number(row.visitors), fallback(row.conversion)],
			}[view];
			return [...values, `<a href="${link(view, row.name)}">查看</a>`];
		}

		drill(label) {
			return `<button type="button" data-expand aria-expanded="false"><i class="bi bi-chevron-right" aria-hidden="true"></i> ${esc(label || "明细")}</button>`;
		}

		table(view, rows) {
			if (!rows?.length) return this.empty();
			const columns = VIEWS[view].columns;
			const expandable = view === "payouts" || view === "statements";
			return `<div class="tka-table-wrap"><table>
				<thead><tr>${columns.map(label => `<th scope="col">${label}</th>`).join("")}</tr></thead>
				<tbody>${rows.map(row => `<tr>${this.cells(view, row).map(value => `<td>${value}</td>`).join("")}</tr>
					${expandable ? `<tr class="tka-drill" data-parent-view="${view}" data-parent="${esc(row.name)}" hidden>
						<td colspan="${columns.length}"><div class="tka-child-body"></div></td></tr>` : ""}`).join("")}</tbody>
			</table></div>`;
		}

		async loadChildren(details, page) {
			if (details.dataset.loading) return;
			details.dataset.loading = "1";
			const content = $(details).find(".tka-child-body").first();
			content.html('<div class="tka-empty">正在读取明细…</div>');
			try {
				const response = await frappe.call({
					method: API + ".get_finance_details", type: "GET",
					args: { parent_type: details.dataset.parentView, parent: details.dataset.parent, page },
				});
				const result = response.message;
				details.dataset.loaded = "1";
				content.html(`<h4>${VIEWS[result.view].table}</h4>${this.table(result.view, result.rows)}
					${result.page > 1 || result.more ? `<div class="tka-pagination">
						<button type="button" data-child-next="${result.page - 1}" ${result.page <= 1 ? "disabled" : ""}>上一页</button>
						<span>第 ${result.page} 页</span>
						<button type="button" data-child-next="${result.page + 1}" ${!result.more ? "disabled" : ""}>下一页</button>
					</div>` : ""}`);
			} catch {
				content.html(`<div class="tka-empty">明细读取失败。<button type="button" data-child-next="${page}">重试</button></div>`);
			} finally {
				delete details.dataset.loading;
			}
		}
	}

	frappe.ui.TikTokAnalysis = TikTokAnalysis;
})();
