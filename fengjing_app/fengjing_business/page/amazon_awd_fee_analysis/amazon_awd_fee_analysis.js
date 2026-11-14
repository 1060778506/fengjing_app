const AWD_FEE_ANALYSIS_API =
    "fengjing_app.fengjing_business.page.amazon_awd_fee_analysis.amazon_awd_fee_analysis.get_analysis";

frappe.pages["amazon_awd_fee_analysis"].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: "亚马逊AWD费用分析",
        single_column: true,
    });
    wrapper.awdFeeAnalysis = new AmazonAWDFeeAnalysis(page);
};

frappe.pages["amazon_awd_fee_analysis"].on_page_show = function (wrapper) {
    wrapper.awdFeeAnalysis?.load();
};

class AmazonAWDFeeAnalysis {
    constructor(page) {
        this.root = $(page.main);
        this.state = { batch_name: "", kind: "all", search: "", page: 1 };
        this.requestId = 0;
        this.renderShell();
        this.bind();
        page.add_inner_button("刷新数据", () => this.load());
    }

    esc(value) { return frappe.utils.escape_html(String(value ?? "")); }
    money(value) {
        return `US$${Number(value || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    }
    qty(value) {
        return Number(value || 0).toLocaleString("en-US", { maximumFractionDigits: 6 });
    }

    renderShell() {
        this.root.html(`
            <div class="awa-shell">
                <section class="awa-hero">
                    <div class="awa-hero-copy"><span>AMAZON AWD · FEE INTELLIGENCE</span>
                        <h1>AWD费用分析</h1>
                        <p>按月份核对 AWD 货件费用与 SKU 仓储费。</p></div>
                    <div class="awa-hero-actions">
                        <a class="awa-hero-link" href="/app/amazon_awd_fee_import">管理导入批次 ↗</a>
                        <button type="button" class="awa-hero-refresh" data-refresh>刷新数据</button>
                    </div>
                </section>
                <section class="awa-filter-panel">
                    <div class="awa-filter-title"><span>筛选条件</span><small>选择批次后查看完整费用构成</small></div>
                        <div class="awa-toolbar">
                        <label>费用批次<select data-batch><option value="">正在加载…</option></select></label>
                        <label>搜索订单 / 货件 / SKU<input type="search" data-search placeholder="输入编号或关键词"></label>
                    </div>
                </section>
                <div class="awa-empty" data-empty hidden></div>
                <div data-content hidden>
                    <div class="awa-batch-meta"><div><span class="awa-status-dot"></span><span data-batch-meta></span></div>
                        <div class="awa-source-links">
                            <a data-open-batch href="#">批次主表 ↗</a>
                            <a data-processing-file href="#" target="_blank" rel="noopener">处理费原文件 ↗</a>
                            <a data-transportation-file href="#" target="_blank" rel="noopener">运输费原文件 ↗</a>
                            <a data-storage-file href="#" target="_blank" rel="noopener">仓储费原文件 ↗</a>
                        </div></div>
                    <section class="awa-kpis" data-kpis></section>
                    <section class="awa-panel awa-fee-panel">
                        <div class="awa-panel-head"><div><h2>费用构成</h2><small>当前收费月份的处理费、运输费和仓储费</small></div></div>
                        <div class="awa-fee-bars" data-fee-bars></div>
                        <div class="awa-tabs" data-tabs></div>
                        <div class="awa-filter-note" data-filter-note></div>
                    </section>
                    <section class="awa-panel awa-data-panel" data-shipment-panel>
                        <div class="awa-data-top">
                            <div><span class="awa-eyebrow">SHIPMENT FEES</span><h2>货件费用明细</h2>
                                <p>每个货件分别展示处理费、运输费与合计。</p></div>
                            <span class="awa-view-count" data-shipment-count></span>
                        </div>
                        <div class="awa-table-wrap" data-shipments></div>
                    </section>
                    <section class="awa-panel awa-data-panel" data-storage-panel hidden>
                        <div class="awa-data-top">
                            <div><span class="awa-eyebrow">STORAGE FEES</span><h2>仓储费明细</h2>
                                <p>按 ASIN、FNSKU、MSKU 和仓储区域查看月度费用；不归入货件。</p></div>
                            <span class="awa-view-count" data-storage-count></span>
                        </div>
                        <div class="awa-table-wrap" data-storage></div>
                    </section>
                </div>
            </div>`);
    }

    bind() {
        this.root.on("change", "[data-batch]", (event) => {
            this.state.batch_name = event.currentTarget.value;
            this.state.kind = "all";
            this.state.page = 1;
            this.load();
        });
        this.root.on("input", "[data-search]", frappe.utils.debounce((event) => {
            this.state.search = event.target.value.trim();
            this.state.page = 1;
            this.load();
        }, 300));
        this.root.on("click", "[data-kind]", (event) => {
            this.state.kind = event.currentTarget.dataset.kind;
            this.state.page = 1;
            this.load();
        });
        this.root.on("click", "[data-refresh]", () => this.load());
    }

    async load() {
        const requestId = ++this.requestId;
        this.root.find(".awa-shell").addClass("is-loading");
        try {
            const response = await frappe.call({
                method: AWD_FEE_ANALYSIS_API,
                args: this.state,
            });
            if (requestId !== this.requestId) return;
            this.renderData(response.message || {});
        } catch (error) {
            if (requestId !== this.requestId) return;
            frappe.msgprint({ title: "AWD费用加载失败", message: error.message || "请稍后重试。", indicator: "red" });
        } finally {
            if (requestId === this.requestId) this.root.find(".awa-shell").removeClass("is-loading");
        }
    }

    renderData(data) {
        this.renderBatchOptions(data.batches || [], data.batch?.name || "");
        if (!data.batch) {
            this.root.find("[data-content]").prop("hidden", true);
            this.root.find("[data-empty]").html('还没有 AWD 费用批次。<a href="/app/amazon_awd_fee_import">前往导入</a>').prop("hidden", false);
            return;
        }
        this.state.batch_name = data.batch.name;
        this.state.page = data.page || 1;
        this.root.find("[data-empty]").prop("hidden", true);
        this.root.find("[data-content]").prop("hidden", false);
        this.root.find("[data-batch-meta]").text(
            `${data.batch.period} · ${data.batch.status} · 卖家 ${data.batch.seller_customer_id || "未提供"} · ${data.batch.row_count || 0} 条源数据`
        );
        this.root.find("[data-open-batch]").attr("href", `/app/amazon-awd-fee-batch/${encodeURIComponent(data.batch.name)}`);
        for (const [slot, field] of [["processing", "processing_file"], ["transportation", "transportation_file"], ["storage", "storage_file"]]) {
            const url = data.batch[field];
            this.root.find(`[data-${slot}-file]`).attr("href", url || "#").prop("hidden", !url);
        }
        this.renderKpis(data);
        this.renderTabs(data);
        const summary = data.summary || {};
        this.root.find("[data-shipment-count]").text(`${summary.shipment_count || 0} 个货件`);
        this.root.find("[data-storage-count]").text(`${summary.storage_count || 0} 条仓储明细`);
        this.root.find("[data-shipment-panel]").prop("hidden", this.state.kind === "storage" || !summary.shipment_count);
        this.root.find("[data-storage-panel]").prop("hidden", this.state.kind !== "storage" && this.state.kind !== "all" || !summary.storage_count);
        this.renderTable("[data-shipments]", data.shipments, [
            ["类型", row => row.order_type], ["订单编号", row => row.order_id],
            ["货件追踪编码", row => row.tracking_id || "—"], ["货件编号", row => row.shipment_id || "—"],
            ["SKU", row => row.sku_count, true],
            ["处理箱数", row => this.qty(row.processing_boxes), true],
            ["运输箱数", row => this.qty(row.transportation_boxes), true],
            ["处理费", row => this.money(row.processing), true],
            ["运输费", row => this.money(row.transportation), true],
            ["合计", row => this.money(row.total), true],
        ]);
        this.renderTable("[data-storage]", data.storage, [
            ["ASIN", row => row.asin || "—"], ["FNSKU", row => row.fnsku || "—"],
            ["MSKU", row => row.msku || "—"], ["仓储区域", row => row.storage_region || "—"],
            ["仓储账单编号", row => row.billing_order_id || "—"],
            ["月平均容量", row => this.qty(row.average_used_capacity), true],
            ["计费体积", row => `${this.qty(row.billed_volume)} ${row.volume_unit || ""}`, true],
            ["费率", row => row.rate == null ? "—" : `${row.rate} ${row.rate_unit || ""}`, true],
            ["仓储费", row => this.money(row.total), true],
        ]);
    }

    renderBatchOptions(batches, selected) {
        const select = this.root.find("[data-batch]").empty();
        if (!batches.length) {
            select.append($("<option>").text("暂无批次"));
            return;
        }
        batches.forEach(row => {
            const option = $("<option>").val(row.name)
                .text(`${row.period} · ${row.status} · ${row.seller_customer_id || "未提供卖家"} · ${row.name} · ${this.money(row.total_amount)}`);
            select.append(option);
        });
        select.val(selected);
    }

    renderKpis(data) {
        const summary = data.summary || {};
        const cards = [
            ["当前筛选费用", this.money(summary.total), `批次总额 ${this.money(data.batch.total_amount)}`, "total"],
            ["处理费", this.money(summary.processing), `批次 ${this.money(data.batch.processing_total)}`, "processing"],
            ["运输费", this.money(summary.transportation), `批次 ${this.money(data.batch.transportation_total)}`, "transportation"],
            ["仓储费", this.money(summary.storage), `批次 ${this.money(data.batch.storage_total)}`, "storage"],
            ["涉及货件", this.qty(summary.shipment_count), `${summary.row_count || 0} 条源费用记录`, "shipments"],
        ];
        this.root.find("[data-kpis]").html(cards.map(([label, value, note, tone]) =>
            `<article class="awa-kpi awa-${tone}"><span>${this.esc(label)}</span><strong>${this.esc(value)}</strong><small>${this.esc(note)}</small></article>`
        ).join(""));
    }

    renderTabs(data) {
        const kinds = [
            ["all", "全部", data.batch.total_amount],
            ["inbound_processing", "入库处理费", data.kind_totals?.inbound_processing],
            ["outbound_processing", "出库处理费", data.kind_totals?.outbound_processing],
            ["other_processing", "其他处理费", data.kind_totals?.other_processing],
            ["transportation", "运输费", data.kind_totals?.transportation],
            ["storage", "仓储费", data.kind_totals?.storage],
        ];
        this.root.find("[data-tabs]").html(kinds.filter(([kind, , amount]) =>
            kind === "all" || Number(amount || 0) !== 0 || kind === this.state.kind
        ).map(([kind, label, amount]) =>
            `<button type="button" data-kind="${kind}" class="${kind === this.state.kind ? "active" : ""}"><span>${this.esc(label)}</span><b>${this.esc(this.money(amount))}</b></button>`
        ).join(""));
        const processing = Number(data.batch.processing_total || 0);
        const transportation = Number(data.batch.transportation_total || 0);
        const storage = Number(data.batch.storage_total || 0);
        const denominator = Math.abs(processing) + Math.abs(transportation) + Math.abs(storage);
        const shares = [processing, transportation, storage].map(value =>
            denominator ? Math.abs(value) / denominator * 100 : 0
        );
        this.root.find("[data-fee-bars]").html(`
            <div class="awa-fee-labels"><span><i class="awa-dot awa-dot-processing"></i>处理费 <b>${this.esc(this.money(processing))}</b></span>
                <span><i class="awa-dot awa-dot-transportation"></i>运输费 <b>${this.esc(this.money(transportation))}</b></span>
                <span><i class="awa-dot awa-dot-storage"></i>仓储费 <b>${this.esc(this.money(storage))}</b></span></div>
            <div class="awa-fee-track" role="img" aria-label="处理费、运输费与仓储费金额占比">
                <span style="width:${shares[0]}%"></span><span style="width:${shares[1]}%"></span>
                <span style="width:${shares[2]}%"></span></div>`);
        const reconciled = data.summary?.matches_batch_total;
        const note = this.state.kind === "all" && !this.state.search
            ? reconciled ? "明细金额合计与批次金额一致。" : "明细合计与批次金额不一致，请检查批次。"
            : "当前显示筛选后的金额；上方卡片同时列出完整批次金额。";
        this.root.find("[data-filter-note]").text(note).toggleClass("has-warning", this.state.kind === "all" && !this.state.search && !reconciled);
    }

    renderTable(selector, rows, columns) {
        const head = `<thead><tr>${columns.map(([label, , numeric]) => `<th class="${numeric ? "num" : ""}">${this.esc(label)}</th>`).join("")}</tr></thead>`;
        const body = rows?.length
            ? rows.map(row => `<tr>${columns.map(([, value, numeric]) => `<td class="${numeric ? "num" : ""}">${this.esc(value(row) ?? "—")}</td>`).join("")}</tr>`).join("")
            : `<tr><td colspan="${columns.length}" class="awa-no-data">当前筛选没有数据</td></tr>`;
        this.root.find(selector).html(`<table>${head}<tbody>${body}</tbody></table>`);
    }

}
