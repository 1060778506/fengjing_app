const FBA_SHIPMENT_ANALYSIS_API =
    "fengjing_app.fengjing_business.page.amazon_fba_shipment_analysis.amazon_fba_shipment_analysis.get_analysis";

frappe.pages["amazon_fba_shipment_analysis"].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper, title: "亚马逊FBA已配送货件分析", single_column: true,
    });
    wrapper.fbaShipmentAnalysis = new AmazonFBAShipmentAnalysis(page);
};

frappe.pages["amazon_fba_shipment_analysis"].on_page_show = function (wrapper) {
    wrapper.fbaShipmentAnalysis?.load();
};

class AmazonFBAShipmentAnalysis {
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

    renderShell() {
        this.root.html(`<div class="fsa-shell">
            <section class="fsa-hero">
                <div><span>AMAZON FBA · FULFILLED SHIPMENTS</span>
                    <h1>已配送货件分析</h1>
                    <p>按订单查看亚马逊实际发出的商品、数量和发货时间。</p></div>
                <div class="fsa-hero-links">
                    <a href="/app/amazon-fba-fulfilled-shipment">原始发货记录 ↗</a>
                    <a href="/app/amazon-ledger">库存分类账 ↗</a>
                </div>
            </section>
            <p class="fsa-notice"><strong>数据口径</strong>本页来自 Amazon-Fulfilled Shipments 报告。同一发货商品可能被多个店铺请求重复返回，汇总按发货 ID 和发货商品 ID 去重，原始记录保留。出现订单表示亚马逊报告了已完成发货；库存分类账的 Shipments 记录没有订单号，因此本页不标记某条库存流水或 ERPNext 库存凭证为“已扣”。</p>
            <section class="fsa-filters">
                <div class="fsa-month-heading"><strong>发货月份</strong><div class="fsa-months"></div></div>
                <label>报告请求店铺<select data-store><option value="">全部店铺</option></select></label>
                <label>查找订单 / SKU / 发货 ID<input type="search" data-search placeholder="输入订单号、SKU 或商品名称"></label>
            </section>
            <div class="fsa-loading" hidden>正在加载已配送货件…</div>
            <section class="fsa-kpis"></section>
            <section class="fsa-card fsa-products"><header><div><span>PRODUCT VIEW</span><h2>发货商品分布</h2></div><small>当前筛选范围，按数量排序</small></header><div class="fsa-product-list"></div></section>
            <section class="fsa-card fsa-records"><header><div><span>SHIPMENT RECORDS</span><h2>已配送货件明细</h2></div><small class="fsa-count"></small></header>
                <div class="fsa-table-wrap"><table><thead><tr>
                    <th>发货时间</th><th>亚马逊订单号</th><th>商品 / SKU / 物料</th>
                    <th>发货数量</th><th>发货 ID / 履约中心</th><th>报告来源</th><th>原始记录</th>
                </tr></thead><tbody></tbody></table></div><footer class="fsa-pages"></footer>
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
        this.root.find(".fsa-loading").prop("hidden", false);
        try {
            const response = await frappe.call({ method: FBA_SHIPMENT_ANALYSIS_API, args: {
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
                frappe.msgprint({ title: "已配送货件加载失败", message: error.message || "请检查后台日志", indicator: "red" });
            }
        } finally {
            if (requestId === this.requestId) this.root.find(".fsa-loading").prop("hidden", true);
        }
    }

    render() {
        this.root.find(".fsa-months").html([
            `<button type="button" data-month="all" class="${this.state.month === "all" ? "active" : ""}">全部月份</button>`,
            ...(this.data.available_months || []).map(month =>
                `<button type="button" data-month="${this.esc(month)}" class="${this.state.month === month ? "active" : ""}">${this.esc(month)}</button>`),
        ].join(""));
        this.root.find("[data-store]").html(`<option value="">全部店铺</option>${(this.data.stores || []).map(store =>
            `<option value="${this.esc(store)}">${this.esc(store)}</option>`).join("")}`).val(this.state.store);
        this.root.find("[data-search]").val(this.state.search);
        const summary = this.data.summary || {};
        const metrics = [
            ["已发货订单", summary.orders, "blue"],
            ["发货件数", summary.units, "green"],
            ["发货批次", summary.shipments, "purple"],
            ["商品 SKU", summary.skus, "orange"],
            ["唯一发货商品", summary.records, "slate"],
        ];
        this.root.find(".fsa-kpis").html(metrics.map(([label, value, color]) =>
            `<article class="fsa-kpi ${color}"><strong>${this.num(value)}</strong><span>${label}</span></article>`).join(""));
        this.renderProducts();
        this.renderRows();
    }

    product(row) {
        const title = row.item_name || row.product_name || row.seller_sku || "未提供商品名称";
        const subtitle = [row.seller_sku, row.item_id].filter(Boolean).join(" · ");
        return `<div class="fsa-product">
            <img src="${this.esc(row.item_image || "/assets/frappe/images/default-avatar.png")}" alt="">
            <div><strong title="${this.esc(title)}">${this.esc(title)}</strong>
                <small title="${this.esc(subtitle)}">${this.esc(subtitle)}</small></div>
        </div>`;
    }

    renderProducts() {
        const products = this.data.products || [];
        this.root.find(".fsa-product-list").html(products.map((row, index) =>
            `<div class="fsa-product-row"><b>${index + 1}</b>${this.product(row)}
                <span>${this.num(row.units)} 件</span><small>${this.num(row.records)} 条明细</small></div>`
        ).join("") || `<div class="fsa-empty">当前范围没有已配送货件。</div>`);
    }

    renderRows() {
        const rows = this.data.rows || [];
        this.root.find(".fsa-count").text(`去重后 ${this.num(this.data.pagination?.total)} 条 · 原始 ${this.num(this.data.summary?.raw_records)} 行 · 每页 30 条`);
        this.root.find(".fsa-records tbody").html(rows.map(row => {
            const recordUrl = `/app/amazon-fba-fulfilled-shipment/${encodeURIComponent(row.name)}`;
            const order = this.esc(row.order_id || row.merchant_order_id || "未提供订单号");
            return `<tr>
                <td class="fsa-time">${this.esc(this.date(row.shipment_at))}</td>
                <td><strong>${order}</strong>${row.merchant_order_id && row.order_id !== row.merchant_order_id ?
                    `<small>卖家订单号：${this.esc(row.merchant_order_id)}</small>` : ""}</td>
                <td>${this.product(row)}</td>
                <td class="fsa-qty">${this.num(row.quantity_shipped)}</td>
                <td><strong>${this.esc(row.shipment_id || "—")}</strong>
                    <small>${this.esc(row.fulfillment_center_id || "未提供履约中心")}</small></td>
                <td title="${this.esc(row.source_stores || "")}">${this.num(row.source_count)} 份来源报告</td>
                <td><a href="${recordUrl}">查看 ↗</a></td>
            </tr>`;
        }).join("") || `<tr><td colspan="7" class="fsa-empty">当前范围没有已配送货件。</td></tr>`);
        const pagination = this.data.pagination || {};
        this.root.find(".fsa-pages").html(`<button type="button" data-page="${Math.max(1, (pagination.page || 1) - 1)}" ${pagination.page <= 1 ? "disabled" : ""}>上一页</button>
            <span>第 ${this.num(pagination.page || 1)} / ${this.num(pagination.pages || 1)} 页</span>
            <button type="button" data-page="${Math.min(pagination.pages || 1, (pagination.page || 1) + 1)}" ${pagination.page >= pagination.pages ? "disabled" : ""}>下一页</button>`);
    }
}
