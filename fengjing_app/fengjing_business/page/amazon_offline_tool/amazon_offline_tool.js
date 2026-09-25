frappe.pages["amazon-offline-tool"].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: "亚马逊 CSV 离线翻译工具",
        single_column: true,
    });

    new AmazonOfflineTranslator(page, wrapper);
};

class AmazonOfflineTranslator {
    constructor(page, wrapper) {
        this.page = page;
        this.wrapper = wrapper;
        this.configuration = { report_types: [], rules: [], sku_item_mappings: [] };
        this.file = null;
        this.rows = [];
        this.translated_rows = [];
        this.header_index = -1;
        this.report_type = null;
        this.country = "";
        this.is_translated = false;
        this.preview_page = 1;
        this.preview_page_size = 10000;
        this.preview_mode = "source";
        this.column_widths = {};
        this.render();
        this.bind_events();
        this.load_configuration();
    }

    render() {
        this.page.main.html(`
            <style>${this.styles()}</style>
            <div class="aot-shell">
                <section class="aot-hero">
                    <div>
                        <div class="aot-eyebrow">AMAZON DATA WORKBENCH</div>
                        <h1>CSV 离线翻译中心</h1>
                        <p>自动识别亚马逊报表，按离线词典翻译表头与固定内容，并下载新的 CSV。</p>
                    </div>
                    <div class="aot-privacy">
                        <span class="aot-privacy-icon">✓</span>
                        <div><strong>不上传、不留档</strong><small>文件仅在当前浏览器内存中处理</small></div>
                    </div>
                </section>

                <section class="aot-workspace">
                    <div class="aot-upload" data-action="pick-file" tabindex="0">
                        <input class="aot-file-input" type="file" accept=".csv,text/csv" hidden>
                        <div class="aot-upload-icon">CSV</div>
                        <div class="aot-upload-copy">
                            <strong class="aot-file-title">拖入 CSV 文件，或点击选择</strong>
                            <span class="aot-file-subtitle">支持 UTF-8、UTF-8 BOM；不会上传到服务器</span>
                        </div>
                        <button class="btn btn-primary btn-sm" type="button">选择文件</button>
                    </div>

                    <div class="aot-controls">
                        <label><span>识别的报表类型</span><select class="form-control aot-report-select"></select></label>
                        <label><span>适用国家</span><select class="form-control aot-country-select"></select></label>
                        <div class="aot-actions">
                            <button class="btn btn-default aot-clear" disabled>清空</button>
                            <button class="btn btn-primary aot-translate" disabled>开始翻译</button>
                            <button class="btn btn-success aot-download" disabled>下载翻译文件</button>
                        </div>
                    </div>
                </section>

                <section class="aot-metrics">
                    ${this.metric("文件状态", "等待上传", "file")}
                    ${this.metric("数据行数", "—", "rows")}
                    ${this.metric("翻译规则", "—", "rules")}
                    ${this.metric("翻译结果", "尚未处理", "result")}
                </section>

                <section class="aot-panel aot-status-panel">
                    <div class="aot-panel-head">
                        <div><h3>处理状态</h3><p>自动识别表头位置和报表类型</p></div>
                        <span class="aot-status-pill is-idle">等待文件</span>
                    </div>
                    <div class="aot-progress"><i></i></div>
                    <div class="aot-status-message">请选择一个亚马逊 CSV 文件开始处理。</div>
                </section>

                <section class="aot-grid">
                    <div class="aot-panel aot-preview-panel">
                        <div class="aot-panel-head">
                            <div><h3>数据预览</h3><p class="aot-preview-note">每页最多显示 10,000 行，下载文件包含全部数据</p></div>
                            <div class="aot-view-switch">
                                <button class="is-active" data-view="source">原文</button>
                                <button data-view="translated">译文</button>
                            </div>
                        </div>
                        <div class="aot-table-empty">上传文件后在这里预览数据</div>
                        <div class="aot-table-wrap" hidden><table><thead></thead><tbody></tbody></table></div>
                        <div class="aot-preview-pagination" hidden>
                            <span class="aot-preview-page-info">—</span>
                            <div><button class="btn btn-default btn-xs aot-preview-prev">上一页</button><button class="btn btn-default btn-xs aot-preview-next">下一页</button></div>
                        </div>
                    </div>
                    <aside class="aot-panel aot-unmatched-panel">
                        <div class="aot-panel-head"><div><h3>词典匹配</h3><p>仅检查已有固定值词典的字段</p></div></div>
                        <div class="aot-match-summary"><strong>—</strong><span>等待分析</span></div>
                        <div class="aot-unmatched-list"><div class="aot-empty-small">暂无分析结果</div></div>
                    </aside>
                </section>
            </div>
        `);
        this.$root = this.page.main.find(".aot-shell");
    }

    metric(label, value, key) {
        return `<div class="aot-metric"><span>${label}</span><strong data-metric="${key}">${value}</strong></div>`;
    }

    bind_events() {
        const $input = this.$root.find(".aot-file-input");
        this.$root.on("click", "[data-action='pick-file']", (event) => {
            if ($(event.target).is("select, option")) return;
            $input.trigger("click");
        });
        this.$root.on("keydown", "[data-action='pick-file']", (event) => {
            if (event.key === "Enter" || event.key === " ") $input.trigger("click");
        });
        $input.on("click", (event) => event.stopPropagation());
        $input.on("change", (event) => this.receive_file(event.target.files?.[0]));

        const upload = this.$root.find(".aot-upload").get(0);
        ["dragenter", "dragover"].forEach((name) => upload.addEventListener(name, (event) => {
            event.preventDefault();
            upload.classList.add("is-dragging");
        }));
        ["dragleave", "drop"].forEach((name) => upload.addEventListener(name, (event) => {
            event.preventDefault();
            upload.classList.remove("is-dragging");
        }));
        upload.addEventListener("drop", (event) => this.receive_file(event.dataTransfer.files?.[0]));

        this.$root.on("change", ".aot-report-select", (event) => {
            this.select_report($(event.currentTarget).val(), false);
        });
        this.$root.on("change", ".aot-country-select", (event) => {
            this.country = $(event.currentTarget).val();
            if (this.rows.length) this.analyse_matches();
        });
        this.$root.on("click", ".aot-clear", () => this.clear());
        this.$root.on("click", ".aot-translate", () => this.translate());
        this.$root.on("click", ".aot-download", () => this.download());
        this.$root.on("click", ".aot-view-switch button", (event) => {
            const $button = $(event.currentTarget);
            $button.addClass("is-active").siblings().removeClass("is-active");
            this.preview_mode = $button.data("view") === "translated" ? "translated" : "source";
            this.preview_page = 1;
            this.render_current_preview();
        });
        this.$root.on("click", ".aot-preview-prev", () => this.change_preview_page(-1));
        this.$root.on("click", ".aot-preview-next", () => this.change_preview_page(1));
        this.$root.on("mousedown", ".aot-col-resizer", (event) => this.begin_column_resize(event));
    }

    async load_configuration() {
        this.set_status("正在读取离线翻译词典…", "working", 20);
        try {
            const response = await frappe.call({
                method: "fengjing_app.fengjing_business.page.amazon_offline_tool.amazon_offline_tool.get_translation_configuration",
            });
            this.configuration = response.message || { report_types: [], rules: [], sku_item_mappings: [] };
            this.populate_report_options();
            this.set_status(
                `词典已就绪，共 ${this.configuration.report_types.length} 种报表、${this.configuration.rules.length} 条规则。`,
                "ready",
                100
            );
        } catch (error) {
            this.set_status("无法读取翻译配置，请检查权限或刷新页面。", "error", 100);
            console.error(error);
        }
    }

    populate_report_options() {
        const options = ["<option value=''>自动识别</option>"];
        this.configuration.report_types.forEach((row) => {
            options.push(`<option value="${this.escape_attr(row.name)}">${this.escape_html(row.report_type_name)}</option>`);
        });
        this.$root.find(".aot-report-select").html(options.join(""));
        this.update_country_options();
    }

    update_country_options() {
        const report_name = this.report_type?.name || this.$root.find(".aot-report-select").val();
        const countries = [...new Set(this.configuration.rules
            .filter((row) => !report_name || row.report_type === report_name)
            .map((row) => row.applicable_country)
            .filter(Boolean))];
        const options = ["<option value=''>通用规则</option>"]
            .concat(countries.map((country) => `<option value="${this.escape_attr(country)}">${this.country_label(country)}</option>`));
        this.$root.find(".aot-country-select").html(options.join(""));
        this.country = countries.includes("United States") ? "United States" : (countries[0] || "");
        this.$root.find(".aot-country-select").val(this.country);
    }

    async receive_file(file) {
        if (!file) return;
        if (!file.name.toLowerCase().endsWith(".csv")) {
            frappe.msgprint({ title: "文件格式不正确", message: "请选择 .csv 文件。", indicator: "orange" });
            return;
        }
        this.file = file;
        this.is_translated = false;
        this.set_status("正在浏览器中读取 CSV…", "working", 35);
        try {
            const buffer = await file.arrayBuffer();
            let text = new TextDecoder("utf-8", { fatal: false }).decode(buffer);
            if (text.startsWith("\uFEFF")) text = text.slice(1);
            this.rows = this.parse_csv(text);
            this.translated_rows = [];
            this.preview_page = 1;
            this.preview_mode = "source";
            this.column_widths = {};
            this.header_index = this.find_header_index(this.rows);
            if (this.header_index < 0) throw new Error("未找到可识别的 CSV 表头");
            this.detect_report();
            this.update_file_ui();
            this.render_preview(this.rows);
            this.analyse_matches();
            this.set_status("文件读取完成，可以开始翻译。", "ready", 100);
        } catch (error) {
            this.clear(false);
            this.set_status(error.message || "CSV 读取失败。", "error", 100);
            frappe.msgprint({ title: "读取失败", message: this.escape_html(error.message || "无法解析该文件"), indicator: "red" });
        }
    }

    parse_csv(text) {
        const rows = [];
        let row = [];
        let value = "";
        let quoted = false;
        for (let index = 0; index < text.length; index += 1) {
            const char = text[index];
            if (quoted) {
                if (char === '"' && text[index + 1] === '"') { value += '"'; index += 1; }
                else if (char === '"') quoted = false;
                else value += char;
            } else if (char === '"') quoted = true;
            else if (char === ",") { row.push(value); value = ""; }
            else if (char === "\n") {
                row.push(value.replace(/\r$/, ""));
                rows.push(row);
                row = [];
                value = "";
            } else value += char;
        }
        if (value.length || row.length) { row.push(value.replace(/\r$/, "")); rows.push(row); }
        while (rows.length && rows[rows.length - 1].every((cell) => !cell)) rows.pop();
        return rows;
    }

    find_header_index(rows) {
        let best = { index: -1, score: 0 };
        rows.slice(0, 50).forEach((row, index) => {
            const cells = new Set(row.map((cell) => String(cell).trim()));
            const score = this.configuration.report_types.reduce((maximum, report) => {
                const fields = this.recognition_fields(report);
                return Math.max(maximum, fields.filter((field) => cells.has(field)).length);
            }, 0);
            if (score > best.score) best = { index, score };
        });
        return best.score >= 2 ? best.index : -1;
    }

    detect_report() {
        const headers = new Set(this.rows[this.header_index].map((cell) => String(cell).trim()));
        const ranked = this.configuration.report_types.map((report) => {
            const fields = this.recognition_fields(report);
            return { report, score: fields.filter((field) => headers.has(field)).length, total: fields.length };
        }).sort((a, b) => (b.score - a.score) || ((b.report.recognition_priority || 0) - (a.report.recognition_priority || 0)));
        const winner = ranked[0];
        if (!winner || winner.score < 2) throw new Error("没有找到匹配的报表类型，请先配置识别字段");
        this.select_report(winner.report.name, true);
    }

    select_report(name, automatic) {
        this.report_type = this.configuration.report_types.find((row) => row.name === name) || null;
        this.$root.find(".aot-report-select").val(name || "");
        this.update_country_options();
        if (this.rows.length) {
            this.is_translated = false;
            this.translated_rows = [];
            this.$root.find(".aot-download").prop("disabled", true);
            this.analyse_matches();
            this.render_preview(this.rows);
        }
        if (automatic && this.report_type) {
            this.$root.find("[data-metric='result']").text(`已识别：${this.report_type.report_type_name}`);
        }
    }

    recognition_fields(report) {
        return String(report.recognition_fields || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
    }

    applicable_rules(type) {
        if (!this.report_type) return [];
        return this.configuration.rules.filter((row) =>
            row.report_type === this.report_type.name &&
            row.translation_type === type &&
            (!row.applicable_country || (this.country && row.applicable_country === this.country))
        );
    }

    analyse_matches() {
        if (this.header_index < 0 || !this.report_type) return;
        const headers = this.rows[this.header_index];
        const content_rules = this.applicable_rules("字段内容");
        const by_field = new Map();
        content_rules.forEach((rule) => {
            if (!by_field.has(rule.file_field_name)) by_field.set(rule.file_field_name, new Set());
            by_field.get(rule.file_field_name).add(String(rule.source_text));
        });
        const unmatched = [];
        let matched = 0;
        let checked = 0;
        by_field.forEach((known, field) => {
            // description 同时包含商品标题和固定费用名称，商品标题不属于“漏翻词条”。
            if (field === "description" || field === "__preamble__") return;
            const column = headers.indexOf(field);
            if (column < 0) return;
            const counts = new Map();
            this.rows.slice(this.header_index + 1).forEach((row) => {
                const value = String(row[column] ?? "").trim();
                if (!value) return;
                checked += 1;
                if (known.has(value)) matched += 1;
                else counts.set(value, (counts.get(value) || 0) + 1);
            });
            counts.forEach((count, value) => unmatched.push({ field, value, count }));
        });
        unmatched.sort((a, b) => b.count - a.count || a.field.localeCompare(b.field));
        const rate = checked ? Math.round((matched / checked) * 100) : 100;
        this.$root.find(".aot-match-summary strong").text(`${rate}%`);
        this.$root.find(".aot-match-summary span").text(`${matched}/${checked} 个固定值已匹配`);
        this.$root.find("[data-metric='rules']").text(`${this.applicable_rules("表头").length + content_rules.length} 条`);
        const $list = this.$root.find(".aot-unmatched-list");
        if (!unmatched.length) {
            $list.html('<div class="aot-all-matched">✓ 当前固定字段全部匹配</div>');
        } else {
            $list.html(unmatched.slice(0, 100).map((item) => `
                <div class="aot-unmatched-item">
                    <div><span>${this.escape_html(item.field)}</span><strong title="${this.escape_attr(item.value)}">${this.escape_html(item.value)}</strong></div>
                    <em>${item.count} 次</em>
                </div>`).join(""));
        }
        this.match_analysis = { matched, checked, unmatched };
    }

    translate() {
        if (!this.rows.length || this.header_index < 0 || !this.report_type) return;
        const header_rules = new Map(this.applicable_rules("表头").map((row) => [String(row.source_text), String(row.target_text)]));
        const content_rules = new Map();
        this.applicable_rules("字段内容").forEach((row) => {
            content_rules.set(`${row.file_field_name}\u0000${row.source_text}`, String(row.target_text));
        });
        const source_headers = this.rows[this.header_index];
        const sku_column = source_headers.indexOf("sku");
        const description_column = source_headers.indexOf("description");
        let enriched_descriptions = 0;
        const preamble_rules = new Map(this.applicable_rules("字段内容")
            .filter((row) => row.file_field_name === "__preamble__")
            .map((row) => [String(row.source_text), String(row.target_text)]));
        this.translated_rows = this.rows.map((row, row_index) => {
            if (row_index < this.header_index) {
                return row.map((cell) => preamble_rules.get(String(cell)) || cell);
            }
            if (row_index === this.header_index) return row.map((cell) => header_rules.get(String(cell)) || cell);
            const translated_row = row.map((cell, column) => {
                const field = source_headers[column] || "";
                return content_rules.get(`${field}\u0000${cell}`) || cell;
            });
            if (sku_column >= 0 && description_column >= 0) {
                const mapping = this.find_item_mapping(row[sku_column]);
                const amazon_title = String(row[description_column] || "").trim();
                if (mapping?.物料名称 && amazon_title) {
                    translated_row[description_column] = `${mapping.物料名称}｜${amazon_title}`;
                    enriched_descriptions += 1;
                }
            }
            return translated_row;
        });
        this.enriched_description_count = enriched_descriptions;
        this.is_translated = true;
        this.$root.find(".aot-download").prop("disabled", false);
        this.$root.find(".aot-view-switch button[data-view='translated']").trigger("click");
        const translated_cells = this.count_translated_cells(header_rules, content_rules, preamble_rules, source_headers)
            + enriched_descriptions;
        this.$root.find("[data-metric='result']").text(`${translated_cells} 处已翻译`);
        this.set_status(`翻译完成，共替换 ${translated_cells} 处内容。文件仍只存在于当前浏览器中。`, "ready", 100);
        frappe.show_alert({ message: `翻译完成：${translated_cells} 处`, indicator: "green" });
    }

    count_translated_cells(header_rules, content_rules, preamble_rules, source_headers) {
        let count = this.rows.slice(0, this.header_index)
            .reduce((total, row) => total + row.filter((cell) => preamble_rules.has(String(cell))).length, 0);
        count += source_headers.filter((cell) => header_rules.has(String(cell))).length;
        this.rows.slice(this.header_index + 1).forEach((row) => row.forEach((cell, column) => {
            if (content_rules.has(`${source_headers[column] || ""}\u0000${cell}`)) count += 1;
        }));
        return count;
    }

    find_item_mapping(sku) {
        const normalized = String(sku || "").trim().toUpperCase();
        if (!normalized) return null;
        const country_sites = {
            "United States": ["ATVPDKIKX0DER"],
            "Canada": ["A2EUQ1WTGCTBG2"],
            "Mexico": ["A1AM78C64UM0Y8"],
            "Brazil": ["A2Q3Y263D00KWC"],
        };
        const allowed_sites = country_sites[this.country] || [];
        return (this.configuration.sku_item_mappings || []).find((row) =>
            String(row.平台sku || "").trim().toUpperCase() === normalized &&
            (!allowed_sites.length || allowed_sites.includes(String(row.站点id || "").trim()))
        ) || null;
    }

    download() {
        if (!this.is_translated || !this.translated_rows.length) return;
        const csv = this.translated_rows.map((row) => row.map((cell) => this.csv_cell(cell)).join(",")).join("\r\n");
        const blob = new Blob(["\uFEFF", csv], { type: "text/csv;charset=utf-8" });
        const link = document.createElement("a");
        const base = this.file.name.replace(/\.csv$/i, "");
        link.href = URL.createObjectURL(blob);
        link.download = `${base}-中文翻译.csv`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
        frappe.show_alert({ message: "翻译文件已下载", indicator: "green" });
    }

    csv_cell(value) {
        const text = String(value ?? "");
        return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    }

    update_file_ui() {
        const data_rows = Math.max(0, this.rows.length - this.header_index - 1);
        this.$root.find(".aot-file-title").text(this.file.name);
        this.$root.find(".aot-file-subtitle").text(`${this.format_bytes(this.file.size)} · 表头位于第 ${this.header_index + 1} 行`);
        this.$root.find("[data-metric='file']").text("读取成功");
        this.$root.find("[data-metric='rows']").text(`${data_rows.toLocaleString()} 行`);
        this.$root.find(".aot-clear, .aot-translate").prop("disabled", false);
    }

    render_current_preview() {
        const rows = this.preview_mode === "translated" && this.translated_rows.length
            ? this.translated_rows
            : this.rows;
        this.render_preview(rows);
    }

    render_preview(rows) {
        const $empty = this.$root.find(".aot-table-empty");
        const $wrap = this.$root.find(".aot-table-wrap");
        const $pagination = this.$root.find(".aot-preview-pagination");
        if (!rows?.length || this.header_index < 0) {
            $empty.show(); $wrap.prop("hidden", true); $pagination.prop("hidden", true); return;
        }
        const header = rows[this.header_index] || [];
        const total_rows = Math.max(0, rows.length - this.header_index - 1);
        const total_pages = Math.max(1, Math.ceil(total_rows / this.preview_page_size));
        this.preview_page = Math.min(Math.max(1, this.preview_page), total_pages);
        const start = this.header_index + 1 + ((this.preview_page - 1) * this.preview_page_size);
        const end = Math.min(rows.length, start + this.preview_page_size);
        const body = rows.slice(start, end);
        this.current_preview_headers = header;
        const widths = header.map((cell, index) => this.column_widths[index] || this.default_column_width(cell));
        const table_width = widths.reduce((sum, width) => sum + width, 0);
        const $table = $wrap.find("table");
        $table.css("width", `${table_width}px`);
        $table.children("colgroup").remove();
        $table.prepend(`<colgroup>${widths.map((width, index) => `<col data-column="${index}" style="width:${width}px">`).join("")}</colgroup>`);
        $wrap.find("thead").html(`<tr>${header.map((cell, index) => `<th data-column="${index}"><span>${this.escape_html(cell)}</span><i class="aot-col-resizer" title="拖动调整列宽"></i></th>`).join("")}</tr>`);
        $wrap.find("tbody").html(body.map((row) => `<tr>${header.map((_, index) => `<td title="${this.escape_attr(row[index] ?? "")}">${this.escape_html(row[index] ?? "")}</td>`).join("")}</tr>`).join(""));
        $empty.hide();
        $wrap.prop("hidden", false);
        $pagination.prop("hidden", false);
        $pagination.find(".aot-preview-page-info").text(
            `第 ${this.preview_page}/${total_pages} 页 · 当前 ${body.length.toLocaleString()} 行 · 共 ${total_rows.toLocaleString()} 行`
        );
        $pagination.find(".aot-preview-prev").prop("disabled", this.preview_page <= 1);
        $pagination.find(".aot-preview-next").prop("disabled", this.preview_page >= total_pages);
    }

    default_column_width(header) {
        const name = String(header || "").toLowerCase();
        if (name === "description" || name === "描述") return 420;
        if (name.includes("date") || name.includes("time") || name.includes("时间")) return 190;
        if (name.includes("order id") || name.includes("订单号")) return 185;
        if (name === "sku") return 155;
        if (["quantity", "数量", "total", "合计"].includes(name)) return 105;
        return 145;
    }

    begin_column_resize(event) {
        event.preventDefault();
        event.stopPropagation();
        const $th = $(event.currentTarget).closest("th");
        const index = Number($th.data("column"));
        const start_x = event.clientX;
        const start_width = this.column_widths[index] || $th.outerWidth();
        $(document.body).addClass("aot-is-resizing");
        $(document).off(".aotResize")
            .on("mousemove.aotResize", (move_event) => {
                const width = Math.max(70, Math.min(700, start_width + move_event.clientX - start_x));
                this.column_widths[index] = Math.round(width);
                this.apply_column_width(index, width);
            })
            .on("mouseup.aotResize", () => {
                $(document).off(".aotResize");
                $(document.body).removeClass("aot-is-resizing");
            });
    }

    apply_column_width(index, width) {
        const $table = this.$root.find(".aot-table-wrap table");
        $table.find(`col[data-column='${index}']`).css("width", `${width}px`);
        const total = (this.current_preview_headers || []).reduce(
            (sum, header, column) => sum + (this.column_widths[column] || this.default_column_width(header)), 0
        );
        $table.css("width", `${total}px`);
    }

    change_preview_page(step) {
        const rows = this.preview_mode === "translated" && this.translated_rows.length
            ? this.translated_rows : this.rows;
        const total_rows = Math.max(0, rows.length - this.header_index - 1);
        const total_pages = Math.max(1, Math.ceil(total_rows / this.preview_page_size));
        const next = Math.min(total_pages, Math.max(1, this.preview_page + step));
        if (next === this.preview_page) return;
        this.preview_page = next;
        this.render_preview(rows);
        this.$root.find(".aot-table-wrap").scrollTop(0);
    }

    clear(reset_status = true) {
        this.file = null;
        this.rows = [];
        this.translated_rows = [];
        this.header_index = -1;
        this.report_type = null;
        this.is_translated = false;
        this.preview_page = 1;
        this.preview_mode = "source";
        this.column_widths = {};
        this.$root.find(".aot-file-input").val("");
        this.$root.find(".aot-file-title").text("拖入 CSV 文件，或点击选择");
        this.$root.find(".aot-file-subtitle").text("支持 UTF-8、UTF-8 BOM；不会上传到服务器");
        this.$root.find(".aot-report-select").val("");
        this.update_country_options();
        this.$root.find(".aot-clear, .aot-translate, .aot-download").prop("disabled", true);
        this.$root.find("[data-metric='file']").text("等待上传");
        this.$root.find("[data-metric='rows'], [data-metric='rules']").text("—");
        this.$root.find("[data-metric='result']").text("尚未处理");
        this.$root.find(".aot-table-empty").show();
        this.$root.find(".aot-table-wrap").prop("hidden", true);
        this.$root.find(".aot-preview-pagination").prop("hidden", true);
        this.$root.find(".aot-match-summary strong").text("—");
        this.$root.find(".aot-match-summary span").text("等待分析");
        this.$root.find(".aot-unmatched-list").html('<div class="aot-empty-small">暂无分析结果</div>');
        this.$root.find(".aot-view-switch button[data-view='source']").addClass("is-active").siblings().removeClass("is-active");
        if (reset_status) this.set_status("当前文件已从浏览器内存中清除。", "idle", 0);
    }

    set_status(message, state, progress) {
        const labels = { idle: "等待文件", working: "处理中", ready: "已就绪", error: "处理失败" };
        this.$root.find(".aot-status-message").text(message);
        this.$root.find(".aot-status-pill").attr("class", `aot-status-pill is-${state}`).text(labels[state] || state);
        this.$root.find(".aot-progress i").css("width", `${Math.max(0, Math.min(100, progress))}%`);
    }

    format_bytes(bytes) {
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / 1048576).toFixed(1)} MB`;
    }

    country_label(country) {
        return ({ "United States": "美国" })[country] || country;
    }

    escape_html(value) {
        return $("<div>").text(String(value ?? "")).html();
    }

    escape_attr(value) {
        return this.escape_html(value)
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;")
            .replace(/`/g, "&#96;");
    }

    styles() {
        return `
            .aot-shell{--blue:#2563eb;--ink:#17233c;--muted:#66758f;--line:#dfe7f3;--panel:#fff;max-width:1760px;margin:0 auto;padding:18px 8px 40px;color:var(--ink)}
            .aot-hero{display:flex;justify-content:space-between;align-items:center;gap:24px;padding:30px 34px;border-radius:22px;background:linear-gradient(125deg,#102a63 0%,#174da5 55%,#2478db 100%);color:#fff;box-shadow:0 18px 45px rgba(30,75,150,.2)}
            .aot-eyebrow{font-size:11px;font-weight:800;letter-spacing:2.2px;color:#9ed8ff}.aot-hero h1{margin:7px 0 8px!important;font-size:30px;font-weight:800;color:#fff!important}.aot-hero p{margin:0;color:#dceaff;font-size:14px}
            .aot-privacy{display:flex;align-items:center;gap:11px;min-width:250px;padding:14px 17px;border:1px solid rgba(255,255,255,.22);border-radius:15px;background:rgba(255,255,255,.1);backdrop-filter:blur(8px)}
            .aot-privacy-icon{display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:#30cf8b;color:#fff;font-weight:900}.aot-privacy strong,.aot-privacy small{display:block}.aot-privacy small{margin-top:2px;color:#dceaff}
            .aot-workspace,.aot-panel,.aot-metric{background:var(--panel);border:1px solid var(--line);box-shadow:0 8px 24px rgba(45,70,110,.06)}
            .aot-workspace{margin-top:18px;padding:18px;border-radius:18px}.aot-upload{display:flex;align-items:center;gap:16px;min-height:94px;padding:18px 22px;border:2px dashed #b9c9e5;border-radius:15px;background:#f8fbff;cursor:pointer;transition:.2s}
            .aot-upload:hover,.aot-upload.is-dragging{border-color:var(--blue);background:#eef5ff;transform:translateY(-1px)}.aot-upload-icon{display:grid;place-items:center;width:54px;height:54px;border-radius:14px;background:#e4eeff;color:var(--blue);font-weight:900;font-size:13px}.aot-upload-copy{min-width:0;flex:1}.aot-upload-copy strong,.aot-upload-copy span{display:block}.aot-upload-copy strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:15px}.aot-upload-copy span{margin-top:5px;color:var(--muted);font-size:12px}
            .aot-controls{display:grid;grid-template-columns:minmax(210px,1fr) minmax(170px,.7fr) auto;gap:14px;align-items:end;margin-top:16px}.aot-controls label{margin:0}.aot-controls label>span{display:block;margin:0 0 6px 2px;color:var(--muted);font-size:12px;font-weight:700}.aot-actions{display:flex;gap:8px;justify-content:flex-end}.aot-actions .btn{white-space:nowrap}
            .aot-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:14px}.aot-metric{padding:16px 18px;border-radius:15px;border-top:3px solid #75a4ff}.aot-metric:nth-child(2){border-top-color:#32c5c7}.aot-metric:nth-child(3){border-top-color:#9d79ef}.aot-metric:nth-child(4){border-top-color:#38bd7c}.aot-metric span,.aot-metric strong{display:block}.aot-metric span{color:var(--muted);font-size:12px}.aot-metric strong{margin-top:7px;font-size:18px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
            .aot-panel{border-radius:17px;overflow:hidden}.aot-status-panel{margin-top:14px;padding:17px 20px}.aot-panel-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.aot-panel-head h3{margin:0;font-size:15px;font-weight:800}.aot-panel-head p{margin:4px 0 0;color:var(--muted);font-size:11px}.aot-status-pill{padding:5px 10px;border-radius:999px;font-size:11px;font-weight:800}.aot-status-pill.is-idle{background:#eef1f6;color:#64748b}.aot-status-pill.is-working{background:#fff3d6;color:#a06400}.aot-status-pill.is-ready{background:#e2f8ed;color:#168255}.aot-status-pill.is-error{background:#ffe7e7;color:#c53737}.aot-progress{height:5px;margin-top:14px;overflow:hidden;border-radius:10px;background:#edf1f7}.aot-progress i{display:block;width:0;height:100%;background:linear-gradient(90deg,#2672f3,#39c8ba);transition:width .3s}.aot-status-message{margin-top:9px;color:var(--muted);font-size:12px}
            .aot-grid{display:grid;grid-template-columns:minmax(0,3fr) minmax(280px,1fr);gap:14px;margin-top:14px}.aot-preview-panel,.aot-unmatched-panel{height:570px}.aot-preview-panel>.aot-panel-head,.aot-unmatched-panel>.aot-panel-head{height:66px;padding:0 18px;border-bottom:1px solid var(--line)}.aot-view-switch{display:flex;padding:3px;border-radius:9px;background:#edf2f8}.aot-view-switch button{border:0;padding:5px 12px;border-radius:7px;background:transparent;color:var(--muted);font-size:11px}.aot-view-switch button.is-active{background:#fff;color:var(--blue);box-shadow:0 2px 7px rgba(40,70,110,.14)}
            .aot-table-empty{display:grid;place-items:center;height:460px;color:#94a0b4}.aot-table-wrap{height:454px;overflow:auto}.aot-table-wrap[hidden],.aot-preview-pagination[hidden]{display:none!important}.aot-table-wrap table{min-width:100%;table-layout:fixed;border-collapse:separate;border-spacing:0}.aot-table-wrap th{position:sticky;top:0;z-index:2;padding:10px 16px 10px 12px;border-right:1px solid #e6ebf3;border-bottom:1px solid #d7e0ed;background:#f3f7fc;color:#3f506b;font-size:11px;white-space:nowrap}.aot-table-wrap th>span{display:block;overflow:hidden;text-overflow:ellipsis}.aot-table-wrap td{padding:8px 12px;border-right:1px solid #edf1f6;border-bottom:1px solid #edf1f6;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px}.aot-table-wrap tbody tr:hover td{background:#f6faff}.aot-col-resizer{position:absolute;top:0;right:-3px;width:7px;height:100%;cursor:col-resize;z-index:4}.aot-col-resizer:hover,.aot-is-resizing .aot-col-resizer{background:rgba(37,99,235,.35)}.aot-is-resizing{cursor:col-resize!important;user-select:none!important}.aot-preview-pagination{display:flex;align-items:center;justify-content:space-between;height:49px;padding:0 16px;border-top:1px solid var(--line);background:#fff;color:var(--muted);font-size:11px}.aot-preview-pagination>div{display:flex;gap:7px}
            .aot-match-summary{display:flex;align-items:baseline;gap:8px;margin:15px 16px;padding:14px;border-radius:12px;background:linear-gradient(135deg,#eef5ff,#f5f0ff)}.aot-match-summary strong{font-size:26px;color:var(--blue)}.aot-match-summary span{color:var(--muted);font-size:11px}.aot-unmatched-list{height:410px;padding:0 15px 15px;overflow:auto}.aot-empty-small,.aot-all-matched{display:grid;place-items:center;height:130px;color:#91a0b5}.aot-all-matched{color:#168255;font-weight:700}.aot-unmatched-item{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:10px 2px;border-bottom:1px solid #edf1f6}.aot-unmatched-item div{min-width:0}.aot-unmatched-item span{display:block;color:#8995a8;font-size:10px}.aot-unmatched-item strong{display:block;max-width:210px;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px}.aot-unmatched-item em{flex:0 0 auto;padding:3px 7px;border-radius:10px;background:#fff0e6;color:#c76520;font-size:10px;font-style:normal}
            @media(max-width:900px){.aot-hero{align-items:flex-start;flex-direction:column}.aot-privacy{width:100%}.aot-controls{grid-template-columns:1fr 1fr}.aot-actions{grid-column:1/-1;justify-content:flex-start}.aot-metrics{grid-template-columns:1fr 1fr}.aot-grid{grid-template-columns:1fr}.aot-unmatched-panel{height:430px}.aot-unmatched-list{height:275px}}
            @media(max-width:560px){.aot-shell{padding-top:8px}.aot-hero{padding:23px 20px}.aot-hero h1{font-size:24px}.aot-controls{grid-template-columns:1fr}.aot-actions{flex-wrap:wrap}.aot-metrics{grid-template-columns:1fr 1fr}.aot-upload{align-items:flex-start;flex-wrap:wrap}.aot-upload-copy{min-width:180px}}
        `;
    }
}
