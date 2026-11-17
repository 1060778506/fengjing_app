const FBA_RETURN_ANALYSIS_API =
    "fengjing_app.fengjing_business.page.amazon_fba_return_analysis.amazon_fba_return_analysis.get_analysis";

frappe.pages["amazon_fba_return_analysis"].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper, title: "亚马逊FBA客户退货分析", single_column: true,
    });
    wrapper.fbaReturnAnalysis = new AmazonFBAReturnAnalysis(page);
};

frappe.pages["amazon_fba_return_analysis"].on_page_show = function (wrapper) {
    wrapper.fbaReturnAnalysis?.load();
};

class AmazonFBAReturnAnalysis {
    constructor(page) {
        this.root = $(page.main);
        this.state = { month: "", category: "all", search: "", page: 1 };
        this.requestId = 0;
        this.charts = [];
        this.data = null;
        this.renderShell();
        this.bind();
        page.add_inner_button("刷新数据", () => this.load());
    }

    esc(value) { return frappe.utils.escape_html(String(value ?? "")); }
    num(value) { return Number(value || 0).toLocaleString("zh-CN"); }
    money(value, currency) {
        return `${this.esc(currency || "")} ${Number(value || 0).toLocaleString("zh-CN", {
            minimumFractionDigits: 2, maximumFractionDigits: 2,
        })}`;
    }
    date(value) { return value ? String(value).replace("T", " ").slice(0, 19) : "—"; }
    label(value) {
        return ({
            SELLABLE: "可售", CUSTOMER_DAMAGED: "买家损坏", DEFECTIVE: "不可售 / 有缺陷",
            CARRIER_DAMAGED: "承运商损坏", WAREHOUSE_DAMAGED: "仓库损坏",
            IMMEDIATE_LIQUIDATION: "立即清算", IMMEDIATE_DISPOSAL: "立即处置",
            "Unit returned to inventory": "已退回库存", NOT_AS_DESCRIBED: "与描述不符",
            UNWANTED_ITEM: "不想要", ORDERED_WRONG_ITEM: "买错商品",
            QUALITY_UNACCEPTABLE: "质量不符合预期", DEFECTIVE_ITEM: "商品有缺陷",
        })[value] || value || "未提供";
    }

    renderShell() {
        this.root.html(`<div class="fra-shell">
            <section class="fra-hero">
                <div><span>AMAZON FBA · CUSTOMER RETURNS</span><h1>客户退货分析</h1>
                    <p>从实际收货记录出发，追踪商品状态与跨月资金事件。</p></div>
                <div class="fra-hero-links">
                    <a href="/app/amazon-fba-customer-return">原始退货记录 ↗</a>
                    <a href="/app/amazon-finance">财务交易中心 ↗</a>
                </div>
            </section>
            <div class="fra-notice"><strong>数据口径</strong><span>四个站点曾返回重复的退货报告，本页按退货实体去重。退货报告没有国家字段；页面显示的订单站点仅由关联财务交易推断。申请退货时间不在现有报告中，退货记录也不能单独证明可售库存已增加。</span></div>
            <section class="fra-filter-panel">
                <div class="fra-month-title"><strong>退货月份（美国太平洋时间）</strong><div class="fra-months"></div></div>
                <label class="fra-search">查找订单 / SKU / ASIN / 物料<input type="search" data-search placeholder="输入编号或商品名称"></label>
                <div class="fra-categories"></div>
            </section>
            <div class="fra-loading" hidden>正在整理 FBA 退货记录…</div>
            <section class="fra-kpis"></section>
            <section class="fra-charts">
                <article class="fra-card"><header><div><span>RETURN TREND</span><h2>每日退货数量</h2></div><small>按商品状态分别展示</small></header><div class="fra-chart" id="fra-trend"></div></article>
                <article class="fra-card"><header><div><span>RETURN OUTCOME</span><h2>处理去向</h2></div><small>按退货数量统计</small></header><div class="fra-chart" id="fra-outcome"></div></article>
            </section>
            <section class="fra-card fra-products"><header><div><span>PRODUCT VIEW</span><h2>退货商品分布</h2></div><small>仅展示当前筛选范围</small></header><div class="fra-product-list"></div></section>
            <section class="fra-card fra-order-section"><header><div><span>ORDER TIMELINE</span><h2>订单与跨月事件</h2></div><small class="fra-order-count"></small></header>
                <p class="fra-timeline-note">按本月收到的退货筛选订单；展开后展示该订单在其他月份的销售、退款和移除 / 清算交易。财务金额保留原交易日期，不计入上方退货数量。</p>
                <div class="fra-order-list"></div><footer class="fra-pages"></footer>
            </section>
        </div>`);
    }

    bind() {
        this.root.on("click", "[data-month]", event => {
            this.state.month = event.currentTarget.dataset.month;
            this.state.page = 1;
            this.load();
        });
        this.root.on("click", "[data-category]", event => {
            this.state.category = event.currentTarget.dataset.category;
            this.state.page = 1;
            this.load();
        });
        let timer;
        this.root.on("input", "[data-search]", event => {
            this.state.search = event.currentTarget.value;
            clearTimeout(timer);
            timer = setTimeout(() => { this.state.page = 1; this.load(); }, 350);
        });
        this.root.on("click", "[data-page]", event => {
            this.state.page = Number(event.currentTarget.dataset.page);
            this.load();
        });
        this.root.on("click", "[data-copy]", event => {
            frappe.utils.copy_to_clipboard(event.currentTarget.dataset.copy || "");
            frappe.show_alert({ message: "已复制", indicator: "green" });
        });
        $(window).off("resize.fba-return-analysis").on("resize.fba-return-analysis", () =>
            this.charts.forEach(chart => chart?.resize()));
    }

    async load() {
        const requestId = ++this.requestId;
        this.root.find(".fra-loading").prop("hidden", false);
        try {
            const response = await frappe.call({ method: FBA_RETURN_ANALYSIS_API, args: {
                month: this.state.month, category: this.state.category,
                search: this.state.search, page: this.state.page, page_size: 20,
            }});
            if (requestId !== this.requestId) return;
            this.data = response.message || {};
            this.state.month = this.data.selected_month || "";
            this.state.category = this.data.selected_category || "all";
            this.state.page = this.data.pagination?.page || 1;
            this.render();
        } catch (error) {
            if (requestId === this.requestId) {
                console.error(error);
                frappe.msgprint({ title: "退货分析加载失败", message: error.message || "请检查后台日志", indicator: "red" });
            }
        } finally {
            if (requestId === this.requestId) this.root.find(".fra-loading").prop("hidden", true);
        }
    }

    render() {
        this.renderFilters();
        this.renderKpis();
        this.renderProducts();
        this.renderOrders();
        this.renderCharts();
    }

    renderFilters() {
        const months = this.data.available_months || [];
        this.root.find(".fra-months").html([
            `<button type="button" data-month="all" class="${this.state.month === "all" ? "active" : ""}">全部月份</button>`,
            ...months.map(month => `<button type="button" data-month="${this.esc(month)}" class="${this.state.month === month ? "active" : ""}">${this.esc(month)}</button>`),
        ].join(""));
        this.root.find("[data-search]").val(this.state.search);
        const names = { all: "全部", sellable: "可售", unsellable: "不可售", unknown: "状态未判定", liquidation: "立即清算", disposal: "立即处置" };
        this.root.find(".fra-categories").html(Object.entries(names).map(([key, label]) =>
            `<button type="button" data-category="${key}" class="${this.state.category === key ? "active" : ""}">${label}<b>${this.num(this.data.categories?.[key])}</b></button>`).join(""));
    }

    renderKpis() {
        const summary = this.data.summary || {};
        const cards = [
            ["退货数量", summary.units, "实际收到的退货件数", "blue"],
            ["可售", summary.sellable, "状态为 SELLABLE", "green"],
            ["不可售", summary.unsellable, "不计入可售库存", "orange"],
            ...(summary.unknown ? [["状态未判定", summary.unknown, "源报告没有商品状态", "slate"]] : []),
            ["立即清算", summary.liquidation, "不代表清算款已到账", "purple"],
            ["立即处置", summary.disposal, "需核对后续处置结果", "red"],
            ["关联订单", summary.orders, `${this.num(summary.records)} 条退货记录`, "slate"],
        ];
        this.root.find(".fra-kpis").html(cards.map(([label, value, note, tone]) =>
            `<article class="fra-kpi ${tone}"><span>${label}</span><strong>${this.num(value)}</strong><small>${note}</small></article>`).join(""));
    }

    async renderCharts() {
        const data = this.data;
        try {
            if (typeof echarts === "undefined") {
                if (!this.chartPromise) this.chartPromise = new Promise((resolve, reject) =>
                    frappe.require("/assets/fengjing_app/js/图表-echarts.js", resolve, reject));
                await this.chartPromise;
            }
            if (data !== this.data) return;
            this.charts.forEach(chart => chart?.dispose());
            this.charts = [];
            const daily = data.daily || [];
            const trend = echarts.init(this.root.find("#fra-trend")[0]);
            trend.setOption({
                color: ["#21a881", "#e9a45b", "#9aabbb"],
                tooltip: { trigger: "axis" }, legend: { top: 6, data: ["可售", "不可售", ...(data.summary?.unknown ? ["状态未判定"] : [])] },
                grid: { left: 46, right: 18, top: 45, bottom: 48 },
                xAxis: { type: "category", data: daily.map(row => row.day.slice(5)), axisLabel: { color: "#75869c" } },
                yAxis: { type: "value", minInterval: 1, axisLabel: { color: "#75869c" } },
                dataZoom: [{ type: "inside" }, ...(daily.length > 16 ? [{ type: "slider", height: 12, bottom: 5 }] : [])],
                series: [
                    { name: "可售", type: "bar", stack: "退货", barMaxWidth: 22, data: daily.map(row => row.sellable) },
                    { name: "不可售", type: "bar", stack: "退货", barMaxWidth: 22, data: daily.map(row => row.unsellable) },
                    ...(data.summary?.unknown ? [{ name: "状态未判定", type: "bar", stack: "退货", barMaxWidth: 22, data: daily.map(row => row.unknown) }] : []),
                ],
            });
            const outcome = echarts.init(this.root.find("#fra-outcome")[0]);
            const statuses = data.statuses || [];
            outcome.setOption({
                color: ["#21a881", "#e9a45b", "#8869d4", "#e56a75", "#7c9fca"],
                tooltip: { trigger: "item", formatter: params => `${this.esc(params.name)}：${this.num(params.value)} 件` },
                legend: { type: "scroll", orient: "vertical", right: 2, top: "middle", textStyle: { width: 125, overflow: "truncate" } },
                series: [{ type: "pie", radius: ["42%", "69%"], center: ["35%", "49%"],
                    label: { show: false }, data: statuses.map(row => ({ name: this.label(row.status), value: row.quantity })) }],
            });
            this.charts.push(trend, outcome);
        } catch (error) {
            console.error(error);
            this.root.find("#fra-trend, #fra-outcome").html("图表资源暂时无法加载，退货明细仍可查看。");
        }
    }

    renderProducts() {
        const products = (this.data.products || []).slice(0, 12);
        const maximum = Math.max(1, ...products.map(row => row.quantity));
        this.root.find(".fra-product-list").html(products.map((row, index) => {
            const width = Math.max(4, row.quantity / maximum * 100);
            const green = row.quantity ? row.sellable / row.quantity * 100 : 0;
            const orange = row.quantity ? row.unsellable / row.quantity * 100 : 0;
            return `<div class="fra-product">
                <span class="fra-rank">${index + 1}</span>
                <img src="${this.esc(row.image || "/assets/frappe/images/default-avatar.png")}" alt="">
                <div class="fra-product-main"><b title="${this.esc(row.item_name || row.product_name || row.sku)}">${this.esc(row.item_name || row.product_name || row.sku)}</b>
                    <small>${this.esc(row.sku)}${row.asin ? ` · ${this.esc(row.asin)}` : ""}${row.item_id ? ` · ${this.esc(row.item_id)}` : ""}</small>
                    <div class="fra-product-track"><div style="width:${width}%"><i class="sellable" style="width:${green}%"></i><i class="unsellable" style="width:${orange}%"></i></div></div></div>
                <strong>${this.num(row.quantity)} 件</strong><small>${this.num(row.returns)} 条</small></div>`;
        }).join("") || `<div class="fra-empty">这个范围没有退货商品。</div>`);
    }

    renderOrders() {
        const pagination = this.data.pagination || {};
        const orders = this.data.orders || [];
        this.root.find(".fra-order-count").text(`${this.num(pagination.total)} 个订单 · 第 ${pagination.page || 1}/${pagination.pages || 1} 页`);
        this.root.find(".fra-order-list").html(orders.map(order => this.orderCard(order)).join("") || `<div class="fra-empty">当前筛选范围没有退货记录。</div>`);
        this.root.find(".fra-pages").html(`<button type="button" data-page="${(pagination.page || 1) - 1}" ${(pagination.page || 1) <= 1 ? "disabled" : ""}>上一页</button><span>第 ${pagination.page || 1} / ${pagination.pages || 1} 页</span><button type="button" data-page="${(pagination.page || 1) + 1}" ${(pagination.page || 1) >= (pagination.pages || 1) ? "disabled" : ""}>下一页</button>`);
    }

    orderCard(order) {
        const location = order.marketplace_id ? `${this.esc(order.marketplace_id)} · 来自财务订单` : "站点待核实";
        return `<details class="fra-order" open><summary><div><span class="fra-order-icon">退</span><div><strong>${this.esc(order.order_id || "未提供订单号")}</strong><small>${location}</small></div></div><span>${this.num(order.quantity)} 件退货 · ${this.num(order.timeline.length)} 个关联事件</span><span class="fra-chevron">⌄</span></summary>
            <div class="fra-order-body">
                <div class="fra-return-items">${order.returns.map(row => `<div class="fra-return-item">
                    <img src="${this.esc(row.item_image || "/assets/frappe/images/default-avatar.png")}" alt="">
                    <div><b>${this.esc(row.item_name || row.product_name || row.seller_sku || "未命名商品")}</b>
                        <small>SKU ${this.esc(row.seller_sku || "—")} · FNSKU ${this.esc(row.fnsku || "—")} · ${this.esc(row.fulfillment_center_id || "未知运营中心")}</small>
                        <small>${this.date(row.report_date)} ${this.esc(row.report_timezone || "")} · ${this.esc(this.label(row.reason))}</small></div>
                    <span class="fra-condition ${!row.detailed_disposition ? "unknown" : row.detailed_disposition === "SELLABLE" ? "sellable" : "unsellable"}">${this.esc(this.label(row.detailed_disposition))}</span>
                    <span class="fra-return-qty">${this.num(row.quantity)} 件</span>
                    <a href="/app/amazon-fba-customer-return/${encodeURIComponent(row.name)}">记录 ↗</a>
                </div>`).join("")}</div>
                <div class="fra-timeline"><h3>订单时间线</h3>${order.timeline.map(event => this.timelineEvent(event)).join("") || `<p>暂无事件</p>`}</div>
            </div></details>`;
    }

    timelineEvent(event) {
        const outside = this.state.month !== "all" && event.month !== this.state.month;
        const isReturn = event.kind === "return";
        const title = isReturn ? `${this.esc(event.label)} · ${this.num(event.quantity)} 件 · ${this.esc(this.label(event.disposition))}` : this.esc(event.label);
        const detail = isReturn ? `${this.esc(this.label(event.status))} · ${this.esc(this.label(event.reason))}${event.sku ? ` · ${this.esc(event.sku)}` : ""}` :
            `${this.esc(event.description || "")}${event.sku ? ` · ${this.esc(event.sku)}` : ""}${event.release_count ? ` · ${this.num(event.release_count)} 条资金释放` : ""}`;
        const amount = isReturn ? "" : event.amount == null ? `<span class="fra-event-amount muted">${this.esc(event.amount_note)}</span>` : `<span class="fra-event-amount ${event.amount < 0 ? "negative" : "positive"}">${this.money(event.amount, event.currency)}<small>${this.esc(event.amount_note)}</small></span>`;
        const path = isReturn ? "amazon-fba-customer-return" : "amazon-financial-transaction";
        return `<div class="fra-event ${isReturn ? "physical" : "financial"}"><i></i><time>${this.date(event.date)}<small>${this.esc(event.timezone || "")}</small></time>
            <div><b>${title}</b><small>${detail}</small></div>${outside ? `<span class="fra-other-month">${this.esc(event.month || "其他月份")}</span>` : ""}
            ${amount}<a href="/app/${path}/${encodeURIComponent(event.source)}">原记录 ↗</a></div>`;
    }
}
