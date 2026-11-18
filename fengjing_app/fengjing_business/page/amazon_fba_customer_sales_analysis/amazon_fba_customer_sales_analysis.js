const FBA_CUSTOMER_SALES_ANALYSIS_API =
    "fengjing_app.fengjing_business.page.amazon_fba_customer_sales_analysis.amazon_fba_customer_sales_analysis.get_analysis";

frappe.pages["amazon_fba_customer_sales_analysis"].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper, title: "亚马逊FBA已完成订单销售分析", single_column: true,
    });
    wrapper.fbaCustomerSalesAnalysis = new AmazonFBACustomerSalesAnalysis(page);
};

frappe.pages["amazon_fba_customer_sales_analysis"].on_page_show = function (wrapper) {
    wrapper.fbaCustomerSalesAnalysis?.load();
};

class AmazonFBACustomerSalesAnalysis {
    constructor(page) {
        this.root = $(page.main);
        this.state = { month: "", store: "", search: "", page: 1 };
        this.requestId = 0;
        this.renderShell();
        this.bind();
        page.add_inner_button("刷新数据", () => this.load());
    }

    esc(value) { return frappe.utils.escape_html(String(value ?? "")); }
    num(value) { return Number(value || 0).toLocaleString("zh-CN"); }
    date(value) { return value ? String(value).replace("T", " ").slice(0, 19) : "—"; }
    amount(value, currency) {
        return `${this.esc(currency || "—")} ${Number(value || 0).toLocaleString("zh-CN", {
            minimumFractionDigits: 2, maximumFractionDigits: 2,
        })}`;
    }

    renderShell() {
        this.root.html(`<div class="fcs-shell">
            <section class="fcs-hero">
                <div><span>AMAZON FBA · CUSTOMER SHIPMENT SALES</span>
                    <h1>已完成订单销售分析</h1>
                    <p>按订单和 SKU 查看报告中的发货时间、数量及商品金额字段。</p></div>
                <div class="fcs-hero-links">
                    <a href="/app/amazon-fba-customer-shipment-sale">原始销售记录 ↗</a>
                    <a href="/app/amazon_fba_shipment_analysis">已配送货件分析 ↗</a>
                </div>
            </section>
            <p class="fcs-notice"><strong>数据口径</strong>本页读取 Amazon 已完成订单销售报告，发货时间和月份均按美国太平洋时间展示。记录按订单所属站点归属，匹配不到的列为“待分配”。这里的商品单价、配送金额和礼品包装金额是报告字段，不代表实际结算净额；库存是否扣减仍需核对库存分类账。</p>
            <section class="fcs-filters">
                <div class="fcs-month-heading"><strong>发货月份（美国太平洋）</strong><div class="fcs-months"></div></div>
                <label>订单所属店铺<select data-store><option value="">全部店铺</option></select></label>
                <label>查找订单 / SKU / ASIN<input type="search" data-search placeholder="输入订单号、SKU、FNSKU 或履约中心"></label>
            </section>
            <div class="fcs-loading" hidden>正在加载已完成订单销售…</div>
            <section class="fcs-kpis"></section>
            <section class="fcs-card fcs-products"><header><div><span>PRODUCT VIEW</span><h2>发货商品分布</h2></div><small>当前筛选范围，按数量排序</small></header><div class="fcs-product-list"></div></section>
            <section class="fcs-card fcs-records"><header><div><span>SALES REPORT ROWS</span><h2>已完成订单销售明细</h2></div><small class="fcs-count"></small></header>
                <div class="fcs-table-wrap"><table><thead><tr>
                    <th>发货时间（美国太平洋）</th><th>订单所属店铺</th><th>亚马逊订单号</th><th>商品 / SKU / 物料</th>
                    <th>数量</th><th>商品单价</th><th>配送金额</th><th>礼品包装</th><th>履约中心 / 目的地</th><th>原始记录</th>
                </tr></thead><tbody></tbody></table></div><footer class="fcs-pages"></footer>
            </section>
        </div>`);
    }

    bind() {
        this.root.on("click", "[data-month]", event => {
            this.state.month = event.currentTarget.dataset.month;
            this.state.page = 1;
            this.load();
        });
        this.root.on("change", "[data-store]", event => {
            this.state.store = event.currentTarget.value;
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
    }

    async load() {
        const requestId = ++this.requestId;
        this.root.find(".fcs-loading").prop("hidden", false);
        try {
            const response = await frappe.call({ method: FBA_CUSTOMER_SALES_ANALYSIS_API, args: {
                month: this.state.month, store: this.state.store, search: this.state.search,
                page: this.state.page, page_size: 30,
            }});
            if (requestId !== this.requestId) return;
            this.data = response.message || {};
            this.state.month = this.data.selected_month || "all";
            this.state.store = this.data.selected_store || "";
            this.state.page = this.data.pagination?.page || 1;
            this.render();
        } catch (error) {
            if (requestId === this.requestId) {
                console.error(error);
                frappe.msgprint({ title: "已完成订单销售加载失败", message: error.message || "请检查后台日志", indicator: "red" });
            }
        } finally {
            if (requestId === this.requestId) this.root.find(".fcs-loading").prop("hidden", true);
        }
    }

    render() {
        this.root.find(".fcs-months").html([
            `<button type="button" data-month="all" class="${this.state.month === "all" ? "active" : ""}">全部月份</button>`,
            ...(this.data.available_months || []).map(month =>
                `<button type="button" data-month="${this.esc(month)}" class="${this.state.month === month ? "active" : ""}">${this.esc(month)}</button>`),
        ].join(""));
        this.root.find("[data-store]").html(`<option value="">全部店铺</option>${(this.data.stores || []).map(store =>
            `<option value="${this.esc(store)}">${this.esc(store)}</option>`).join("")}
            <option value="pending">待分配</option>`).val(this.state.store);
        this.root.find("[data-search]").val(this.state.search);
        const summary = this.data.summary || {};
        const metrics = [
            ["已发货订单", summary.orders, "blue"],
            ["发货件数", summary.units, "green"],
            ["商品 SKU", summary.skus, "purple"],
            ["报告明细", summary.records, "orange"],
            ["待分配", summary.pending, "slate"],
        ];
        this.root.find(".fcs-kpis").html(metrics.map(([label, value, color]) =>
            `<article class="fcs-kpi ${color}"><strong>${this.num(value)}</strong><span>${label}</span></article>`).join(""));
        this.renderProducts();
        this.renderRows();
    }

    product(row) {
        const title = row.item_name || row.seller_sku || row.asin || "未映射物料";
        const subtitle = [row.seller_sku, row.item_id, row.asin].filter(Boolean).join(" · ");
        const item = row.item_id
            ? `<a href="/app/item/${encodeURIComponent(row.item_id)}" title="打开物料">${this.esc(title)}</a>`
            : `<strong title="${this.esc(title)}">${this.esc(title)}</strong>`;
        return `<div class="fcs-product">
            <img src="${this.esc(row.item_image || "/assets/frappe/images/default-avatar.png")}" alt="">
            <div>${item}<small title="${this.esc(subtitle)}">${this.esc(subtitle)}</small></div>
        </div>`;
    }

    renderProducts() {
        const products = this.data.products || [];
        this.root.find(".fcs-product-list").html(products.map((row, index) =>
            `<div class="fcs-product-row"><b>${index + 1}</b>${this.product(row)}
                <span>${this.num(row.units)} 件</span><small>${this.num(row.records)} 条明细</small></div>`
        ).join("") || `<div class="fcs-empty">当前范围没有已完成订单销售记录。</div>`);
    }

    renderRows() {
        const rows = this.data.rows || [];
        this.root.find(".fcs-count").text(`${this.num(this.data.pagination?.total)} 条 · 每页 30 条`);
        this.root.find(".fcs-records tbody").html(rows.map(row => {
            const recordUrl = `/app/amazon-fba-customer-shipment-sale/${encodeURIComponent(row.name)}`;
            const destination = [row.ship_city, row.ship_state].filter(Boolean).join(" · ");
            return `<tr>
                <td class="fcs-time">${this.esc(this.date(row.report_shipment_at))}<small>${this.esc(row.report_timezone || "")}</small></td>
                <td><strong>${this.esc(row.amazon_store || "待分配")}</strong>
                    <small>${this.esc(row.marketplace_id || "站点未匹配")}</small></td>
                <td><strong>${this.esc(row.order_id || "未提供订单号")}</strong></td>
                <td>${this.product(row)}<small class="fcs-fnsku">FNSKU：${this.esc(row.fnsku || "—")}</small></td>
                <td class="fcs-qty">${this.num(row.quantity)}</td>
                <td class="fcs-money">${this.amount(row.item_price_per_unit, row.currency)}</td>
                <td class="fcs-money">${this.amount(row.shipping_price, row.currency)}</td>
                <td class="fcs-money">${this.amount(row.gift_wrap_price, row.currency)}</td>
                <td><strong>${this.esc(row.fulfillment_center_id || "—")}</strong>
                    <small>${this.esc(destination || "目的地未提供")}</small></td>
                <td><a href="${recordUrl}">查看 ↗</a></td>
            </tr>`;
        }).join("") || `<tr><td colspan="10" class="fcs-empty">当前范围没有已完成订单销售记录。</td></tr>`);
        const pagination = this.data.pagination || {};
        this.root.find(".fcs-pages").html(`<button type="button" data-page="${Math.max(1, (pagination.page || 1) - 1)}" ${pagination.page <= 1 ? "disabled" : ""}>上一页</button>
            <span>第 ${this.num(pagination.page || 1)} / ${this.num(pagination.pages || 1)} 页</span>
            <button type="button" data-page="${Math.min(pagination.pages || 1, (pagination.page || 1) + 1)}" ${pagination.page >= pagination.pages ? "disabled" : ""}>下一页</button>`);
    }
}
