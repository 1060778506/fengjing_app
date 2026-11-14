const AWD_FEE_METHOD =
    "fengjing_app.fengjing_business.page.amazon_awd_fee_import.amazon_awd_fee_import";

frappe.pages["amazon_awd_fee_import"].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: "AWD费用批次导入",
        single_column: true,
    });
    wrapper.awdFeeImport = new AmazonAWDFeeImport(page);
};

frappe.pages["amazon_awd_fee_import"].on_page_show = function (wrapper) {
    wrapper.awdFeeImport?.loadBatches();
};

class AmazonAWDFeeImport {
    constructor(page) {
        this.page = page;
        this.files = { processing: null, transportation: null, storage: null };
        this.selectedName = null;
        this.currentBatch = null;
        this.explicitNew = false;
        this.render();
        this.bind();
        this.loadBatches();
    }

    render() {
        const month = frappe.datetime.get_today().slice(0, 7);
        this.$root = $(this.page.main).html(`
            <div class="awd-fee-shell">
                <section class="awd-fee-hero">
                    <span>AMAZON AWD</span>
                    <h2>AWD费用批次</h2>
                    <p>按收费月份保存处理费、运输费与仓储费报告。费用明细随文件重新生成。</p>
                    <a href="/app/amazon-awd-fee-batch">查看全部批次</a>
                </section>
                <div class="awd-fee-layout">
                    <aside class="awd-fee-card awd-fee-sidebar">
                        <header><h3>费用批次</h3><button class="btn btn-default btn-sm" data-refresh>刷新</button></header>
                        <button class="btn btn-primary awd-fee-new" data-new>＋ 新建批次</button>
                        <div class="awd-fee-batch-list" data-batches>正在加载…</div>
                    </aside>
                    <section class="awd-fee-card awd-fee-editor">
                        <header><div><h3 data-editor-title>新建费用批次</h3><span data-editor-subtitle>上传同一收费月份的费用报告，可先上传其中一份</span></div>
                            <a data-open-batch href="#" hidden>打开主表</a></header>
                        <label class="awd-fee-period">收费月份 <input type="month" data-period value="${month}"></label>
                        <div class="awd-fee-existing" data-existing hidden>
                            <span data-seller></span><span data-status></span><span data-row-count></span>
                        </div>
                        <div class="awd-fee-uploads">
                            <div class="awd-fee-file-group">
                                <label class="awd-fee-upload" data-slot="processing">
                                    <b>AWD月度处理费报告</b>
                                    <span data-file-label="processing">拖入 XLSX 文件或点击选择</span>
                                    <input type="file" accept=".xlsx" data-file="processing">
                                </label>
                                <a class="awd-fee-report-link" href="https://sellercentral.amazon.com/awd/reports/fee?reportType=AwdProcessingFee" target="_blank" rel="noopener noreferrer">打开亚马逊处理费报告 ↗</a>
                                <a data-current-file="processing" href="#" target="_blank" rel="noopener" hidden></a>
                            </div>
                            <div class="awd-fee-file-group">
                                <label class="awd-fee-upload" data-slot="transportation">
                                    <b>AWD月度运输费报告</b>
                                    <span data-file-label="transportation">拖入 XLSX 文件或点击选择</span>
                                    <input type="file" accept=".xlsx" data-file="transportation">
                                </label>
                                <a class="awd-fee-report-link" href="https://sellercentral.amazon.com/awd/reports/fee?reportType=AwdOutboundTransportationFee" target="_blank" rel="noopener noreferrer">打开亚马逊运输费报告 ↗</a>
                                <a data-current-file="transportation" href="#" target="_blank" rel="noopener" hidden></a>
                            </div>
                            <div class="awd-fee-file-group">
                                <label class="awd-fee-upload" data-slot="storage">
                                    <b>AWD月度仓储费报告</b>
                                    <span data-file-label="storage">拖入 XLSX 文件或点击选择</span>
                                    <input type="file" accept=".xlsx" data-file="storage">
                                </label>
                                <a class="awd-fee-report-link" href="https://sellercentral.amazon.com/awd/reports/fee?reportType=AwdStorageFee" target="_blank" rel="noopener noreferrer">打开亚马逊仓储费报告 ↗</a>
                                <a data-current-file="storage" href="#" target="_blank" rel="noopener" hidden></a>
                            </div>
                        </div>
                        <div class="awd-fee-summary" data-summary hidden>
                            <div><b data-processing-total>0.00</b><span>处理费 USD</span></div>
                            <div><b data-transportation-total>0.00</b><span>运输费 USD</span></div>
                            <div><b data-storage-total>0.00</b><span>仓储费 USD</span></div>
                            <div><b data-total-amount>0.00</b><span>合计 USD</span></div>
                        </div>
                        <button class="btn btn-primary" data-import>导入并保存批次</button>
                        <p class="awd-fee-note" data-mode-note>新建批次至少上传一份报告。保存不会生成会计凭证或修改库存成本。</p>
                    </section>
                </div>
            </div>`);
    }

    bind() {
        this.$root.on("change", "input[data-file]", (event) => {
            const slot = event.currentTarget.dataset.file;
            this.setFile(slot, event.currentTarget.files?.[0] || null);
        });
        this.$root.find("[data-slot]").each((_, element) => {
            element.addEventListener("dragover", (event) => {
                event.preventDefault();
                element.classList.add("is-dragging");
            });
            element.addEventListener("dragleave", () => element.classList.remove("is-dragging"));
            element.addEventListener("drop", (event) => {
                event.preventDefault();
                element.classList.remove("is-dragging");
                this.setFile(element.dataset.slot, event.dataTransfer.files?.[0] || null);
            });
        });
        this.$root.on("click", "[data-import]", () => this.importBatch());
        this.$root.on("click", "[data-refresh]", () => this.loadBatches());
        this.$root.on("click", "[data-new]", () => this.newBatch());
        this.$root.on("click", "[data-batch-name]", (event) => this.selectBatch(event.currentTarget.dataset.batchName));
    }

    setFile(slot, file) {
        if (file && !file.name.toLowerCase().endsWith(".xlsx")) {
            frappe.msgprint("请选择 XLSX 格式的 AWD 月度报告。");
            return;
        }
        this.files[slot] = file;
        this.$root.find(`[data-file-label="${slot}"]`).text(
            file?.name || (this.selectedName ? "拖入新文件以替换，或点击选择" : "拖入 XLSX 文件或点击选择")
        );
        this.$root.find(`[data-slot="${slot}"]`).toggleClass("has-file", !!file);
    }

    resetUploads() {
        this.files = { processing: null, transportation: null, storage: null };
        this.$root.find("input[data-file]").val("");
        this.$root.find("[data-slot]").removeClass("has-file");
        ["processing", "transportation", "storage"].forEach((slot) => this.setFile(slot, null));
    }

    newBatch() {
        this.explicitNew = true;
        this.selectedName = null;
        this.currentBatch = null;
        this.resetUploads();
        this.$root.find("[data-period]").val(frappe.datetime.get_today().slice(0, 7)).prop("disabled", false);
        this.$root.find("[data-editor-title]").text("新建费用批次");
        this.$root.find("[data-editor-subtitle]").text("上传同一收费月份的费用报告，可先上传其中一份");
        this.$root.find("[data-existing], [data-summary], [data-open-batch], [data-current-file]").prop("hidden", true);
        this.$root.find("[data-import]").text("导入并保存批次");
        this.$root.find("[data-mode-note]").text("新建批次至少上传一份报告。保存不会生成会计凭证或修改库存成本。");
        this.renderBatchList();
    }

    async selectBatch(name) {
        if (!name || this.importing) return;
        try {
            const response = await frappe.call({ method: `${AWD_FEE_METHOD}.get_batch`, args: { name } });
            const batch = response.message;
            this.explicitNew = false;
            this.selectedName = batch.name;
            this.currentBatch = batch;
            this.resetUploads();
            this.$root.find("[data-period]").val(batch.period).prop("disabled", true);
            this.$root.find("[data-editor-title]").text(batch.name);
            this.$root.find("[data-editor-subtitle]").text("只上传需要替换的文件，未选择的文件保持原样");
            this.$root.find("[data-seller]").text(`卖家客户编号：${batch.seller_customer_id}`);
            this.$root.find("[data-status]").text(`状态：${batch.status}`);
            this.$root.find("[data-row-count]").text(`明细 ${batch.row_count || 0} 行`);
            this.$root.find("[data-existing], [data-summary], [data-open-batch]").prop("hidden", false);
            this.$root.find("[data-open-batch]").attr("href", `/app/amazon-awd-fee-batch/${encodeURIComponent(batch.name)}`);
            for (const [slot, field] of [["processing", "processing_file"], ["transportation", "transportation_file"], ["storage", "storage_file"]]) {
                this.$root.find(`[data-current-file="${slot}"]`)
                    .text(`当前文件：${batch[field]?.name || "无"}`)
                    .attr("href", batch[field]?.url || "#")
                    .prop("hidden", !batch[field]?.url);
            }
            this.$root.find("[data-processing-total]").text(Number(batch.processing_total || 0).toFixed(2));
            this.$root.find("[data-transportation-total]").text(Number(batch.transportation_total || 0).toFixed(2));
            this.$root.find("[data-storage-total]").text(Number(batch.storage_total || 0).toFixed(2));
            this.$root.find("[data-total-amount]").text(Number(batch.total_amount || 0).toFixed(2));
            this.$root.find("[data-import]").text("保存文件修改");
            this.$root.find("[data-mode-note]").text("保存后重新解析本批次全部明细；如果选择了历史批次，它会成为当前批次。");
            this.renderBatchList();
        } catch (error) {
            frappe.msgprint({ title: "批次加载失败", message: error.message || "请刷新后重试。", indicator: "red" });
        }
    }

    async loadBatches() {
        try {
            const response = await frappe.call({ method: `${AWD_FEE_METHOD}.list_batches` });
            this.batches = response.message || [];
            this.renderBatchList();
            if (!this.explicitNew && !this.selectedName && !this.currentBatch && this.batches.length) {
                await this.selectBatch(this.batches[0].name);
            } else if (this.selectedName && !this.batches.some((row) => row.name === this.selectedName)) {
                this.newBatch();
            }
        } catch (error) {
            this.$root.find("[data-batches]").text("批次加载失败，请检查权限。");
        }
    }

    renderBatchList() {
        const body = this.$root.find("[data-batches]").empty();
        if (!this.batches?.length) {
            body.text("还没有 AWD 费用批次");
            return;
        }
        this.batches.forEach((row) => {
            const button = $("<button type='button' class='awd-fee-batch'>")
                .attr("data-batch-name", row.name)
                .toggleClass("active", row.name === this.selectedName);
            button.append($("<strong>").text(row.period || row.name));
            button.append($("<small>").text(row.name));
            button.append($("<span>").text(`${row.status || ""} · USD ${Number(row.total_amount || 0).toFixed(2)} · ${row.row_count || 0} 行`));
            body.append(button);
        });
    }

    uploadPrivateFile(file) {
        return new Promise((resolve, reject) => {
            const request = new XMLHttpRequest();
            request.open("POST", "/api/method/upload_file");
            request.setRequestHeader("Accept", "application/json");
            request.setRequestHeader("X-Frappe-CSRF-Token", frappe.csrf_token);
            request.onload = () => {
                let payload;
                try { payload = JSON.parse(request.responseText || "{}"); }
                catch { reject(new Error("文件上传返回内容无法识别。")); return; }
                if (request.status >= 200 && request.status < 300 && payload.message?.doctype === "File") {
                    resolve(payload.message.name);
                } else {
                    reject(new Error(`文件上传失败（${request.status}）：${payload.message || ""}`));
                }
            };
            request.onerror = () => reject(new Error("文件上传网络连接失败。"));
            const data = new FormData();
            data.append("file", file, file.name);
            data.append("is_private", "1");
            data.append("folder", "Home/Attachments");
            request.send(data);
        });
    }

    async importBatch() {
        const period = this.$root.find("[data-period]").val();
        const editing = !!this.selectedName;
        if (!period || (!editing && !Object.values(this.files).some(Boolean))) {
            frappe.msgprint("新建批次时请选择收费月份，并至少上传一份 AWD 报告。");
            return;
        }
        if (editing && !Object.values(this.files).some(Boolean)) {
            frappe.msgprint("请先选择要替换的报告。");
            return;
        }
        if (this.importing) return;
        this.importing = true;
        this.$root.find("[data-import]").prop("disabled", true);
        frappe.dom.freeze("正在上传并核对 AWD 报告…");
        const uploaded = [];
        try {
            let processing = null, transportation = null, storage = null;
            if (this.files.processing) {
                processing = await this.uploadPrivateFile(this.files.processing);
                uploaded.push(processing);
            }
            if (this.files.transportation) {
                transportation = await this.uploadPrivateFile(this.files.transportation);
                uploaded.push(transportation);
            }
            if (this.files.storage) {
                storage = await this.uploadPrivateFile(this.files.storage);
                uploaded.push(storage);
            }
            const response = await frappe.call({
                method: `${AWD_FEE_METHOD}.${editing ? "update_fee_batch" : "import_fee_batch"}`,
                args: editing
                    ? { name: this.selectedName, expected_modified: this.currentBatch.modified,
                        processing_file: processing, transportation_file: transportation, storage_file: storage }
                    : { period, processing_file: processing, transportation_file: transportation, storage_file: storage },
            });
            const batch = response.message;
            frappe.show_alert({
                message: `已${editing ? "更新" : "保存"} ${batch.name}：处理费 $${Number(batch.processing_total).toFixed(2)}，运输费 $${Number(batch.transportation_total).toFixed(2)}，仓储费 $${Number(batch.storage_total || 0).toFixed(2)}，共 ${batch.row_count} 行`,
                indicator: "green",
            }, 10);
            this.resetUploads();
            await this.loadBatches();
            await this.selectBatch(batch.name);
        } catch (error) {
            if (uploaded.length) {
                try {
                    await frappe.call({ method: `${AWD_FEE_METHOD}.discard_uploads`, args: { file_names: uploaded } });
                } catch (cleanupError) { console.warn("AWD 上传文件清理失败", cleanupError); }
            }
            frappe.msgprint({ title: "导入失败", message: error.message || "请检查文件和收费月份。", indicator: "red" });
        } finally {
            frappe.dom.unfreeze();
            this.importing = false;
            this.$root.find("[data-import]").prop("disabled", false);
        }
    }
}
