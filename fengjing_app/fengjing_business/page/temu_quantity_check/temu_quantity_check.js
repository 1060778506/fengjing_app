const TEMU_QUANTITY_FILE_TYPES = [
	"欧区待处理", "全球待处理", "美区待处理",
	"欧区财务", "全球财务", "美区财务",
];
const TEMU_QUANTITY_METHOD =
	"fengjing_app.fengjing_business.page.temu_quantity_check.temu_quantity_check.analyze_file";

frappe.pages["temu_quantity_check"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: "Temu六文件物料数量统计", single_column: true });
	wrapper.temuQuantityCheck = new TemuQuantityCheckPage(wrapper, page);
};

class TemuQuantityCheckPage {
	constructor(wrapper, page) {
		this.wrapper = wrapper;
		this.results = new Map();
		this.manualQuantities = new Map();
		this.pending = new Set();
		this.sequence = new Map();
		this.$ = $(page.main);
		$(wrapper).addClass("tqc-page");
		this.$.html(`
			<section class="tqc-intro">
				<div><span>TEMU · 数量统计</span><h2>六文件物料数量统计</h2>
					<p>将各文件拖到对应位置，自动读取数量。文件和结果只保留在当前页面，刷新后清空。</p></div>
				<button type="button" class="btn btn-default tqc-clear">清空全部</button>
			</section>
			<section class="tqc-upload-grid">${TEMU_QUANTITY_FILE_TYPES.map((type) => `
				<div class="tqc-upload" data-file-type="${this.esc(type)}">
					<input type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden>
					<div class="tqc-upload-icon">XLSX</div><div class="tqc-upload-copy"><strong>${this.esc(type)}</strong>
						<small class="tqc-file-name">点击选择或拖入文件</small></div><span class="tqc-upload-state">待上传</span>
				</div>`).join("")}</section>
			<section class="tqc-summary"><div class="tqc-summary-heading"><div><h3>数量汇总</h3><p>同一 SKU 按文件分别列出；跨文件合计是原始数量相加，未按订单去重。</p></div>
				<input type="search" class="form-control tqc-search" placeholder="搜索 SKU 或货品名称"></div>
				<div class="tqc-stats"></div><div class="tqc-table-wrap"></div>
			</section>`);
		this.$.on("click", ".tqc-upload", (event) => {
			if ($(event.target).is("input")) return;
			$(event.currentTarget).find('input[type="file"]').trigger("click");
		});
		this.$.on("change", '.tqc-upload input[type="file"]', (event) => {
			const input = event.currentTarget;
			if (input.files?.[0]) this.acceptFile($(input).closest(".tqc-upload").data("fileType"), input.files[0]);
			input.value = "";
		});
		this.$.on("dragover", ".tqc-upload", (event) => {
			event.preventDefault();
			$(event.currentTarget).addClass("is-dragging");
		});
		this.$.on("dragleave drop", ".tqc-upload", (event) => {
			event.preventDefault();
			$(event.currentTarget).removeClass("is-dragging");
			if (event.type === "drop") {
				const file = event.originalEvent?.dataTransfer?.files?.[0];
				if (file) this.acceptFile($(event.currentTarget).data("fileType"), file);
			}
		});
		this.$.on("input", ".tqc-search", () => this.renderSummary());
		this.$.on("input", ".tqc-manual-input", (event) => {
			const input = event.currentTarget;
			const sku = input.getAttribute("data-sku");
			const values = this.manualQuantities.get(sku) || {};
			values[input.getAttribute("data-field")] = input.value;
			this.manualQuantities.set(sku, values);
			const $row = $(input).closest("tr");
			const fileTotal = Number($row.attr("data-file-total")) || 0;
			$row.find(".tqc-total").text(this.num(fileTotal + this.manualTotalOf(sku)));
		});
		this.$.on("click", ".tqc-clear", () => {
			this.results.clear();
			this.manualQuantities.clear();
			for (const type of TEMU_QUANTITY_FILE_TYPES) {
				this.sequence.set(type, (this.sequence.get(type) || 0) + 1);
			}
			this.pending.clear();
			this.$.find(".tqc-search").val("");
			this.render();
		});
		this.render();
	}

	async acceptFile(type, file) {
		if (!file.name.toLowerCase().endsWith(".xlsx")) {
			frappe.msgprint({ title: "文件格式不正确", message: "只支持 XLSX 文件。", indicator: "red" });
			return;
		}
		const sequence = (this.sequence.get(type) || 0) + 1;
		this.sequence.set(type, sequence);
		this.results.delete(type);
		this.pending.add(type);
		this.render();
		try {
			const result = await this.analyzeFile(type, file);
			if (this.sequence.get(type) !== sequence) return;
			this.results.set(type, result);
			frappe.show_alert({ message: `${type}：已统计 ${result.row_count} 行`, indicator: "green" });
		} catch (error) {
			if (this.sequence.get(type) !== sequence) return;
			frappe.msgprint({ title: `${type} 读取失败`, message: this.esc(error.message || "请检查文件格式。"), indicator: "red" });
		} finally {
			if (this.sequence.get(type) === sequence) {
				this.pending.delete(type);
				this.render();
			}
		}
	}

	analyzeFile(type, file) {
		return new Promise((resolve, reject) => {
			const xhr = new XMLHttpRequest();
			xhr.open("POST", `/api/method/${TEMU_QUANTITY_METHOD}`, true);
			xhr.setRequestHeader("Accept", "application/json");
			xhr.setRequestHeader("X-Frappe-CSRF-Token", frappe.csrf_token);
			xhr.onload = () => {
				let payload;
				try { payload = JSON.parse(xhr.responseText || "{}"); }
				catch (_) { reject(new Error("服务器返回了无法识别的内容。")); return; }
				if (xhr.status >= 200 && xhr.status < 300 && payload.message) resolve(payload.message);
				else reject(new Error(this.responseError(payload) || `读取失败（${xhr.status}）`));
			};
			xhr.onerror = () => reject(new Error("网络连接失败。"));
			const data = new FormData();
			data.append("file_type", type);
			data.append("file", file, file.name);
			xhr.send(data);
		});
	}

	responseError(payload) {
		try {
			return JSON.parse(payload._server_messages || "[]")
				.map((message) => JSON.parse(message).message).filter(Boolean).join("；");
		} catch (_) { return payload.exception || ""; }
	}

	render() {
		this.$.find(".tqc-upload").each((_, element) => {
			const type = $(element).data("fileType");
			const result = this.results.get(type);
			const busy = this.pending.has(type);
			$(element).toggleClass("is-ready", !!result).toggleClass("is-busy", busy);
			$(element).find(".tqc-file-name").text(result?.file_name || (busy ? "正在读取文件…" : "点击选择或拖入文件"));
			$(element).find(".tqc-upload-state").text(result ? `${this.num(result.quantity)} 件` : busy ? "读取中" : "待上传");
		});
		this.renderSummary();
	}

	renderSummary() {
		const search = String(this.$.find(".tqc-search").val() || "").trim().toLocaleLowerCase();
		const groups = new Map();
		let totalRows = 0;
		let totalQuantity = 0;
		let totalReship = 0;
		for (const type of TEMU_QUANTITY_FILE_TYPES) {
			const result = this.results.get(type);
			if (!result) continue;
			totalRows += Number(result.row_count || 0);
			totalQuantity += Number(result.quantity || 0);
			totalReship += Number(result.reship_quantity || 0);
			for (const row of result.items || []) {
				const key = row.sku_id || row.sku_code;
				if (!groups.has(key)) groups.set(key, { sku_id: row.sku_id, sku_code: row.sku_code, product_name: row.product_name, item_code: row.item_code, item_image: row.item_image, quantities: {}, reship_quantity: 0 });
				const group = groups.get(key);
				group.sku_id ||= row.sku_id;
				group.sku_code ||= row.sku_code;
				group.product_name ||= row.product_name;
				group.item_code ||= row.item_code;
				group.item_image ||= row.item_image;
				group.quantities[type] = (group.quantities[type] || 0) + Number(row.quantity || 0);
				group.reship_quantity += Number(row.reship_quantity || 0);
			}
		}
		const rows = [...groups.values()]
			.filter((row) => !search || `${row.sku_id} ${row.sku_code} ${row.product_name}`.toLocaleLowerCase().includes(search))
			.sort((a, b) => this.totalOf(b) - this.totalOf(a) || String(a.sku_id).localeCompare(String(b.sku_id)));
		const stat = (label, value) => `<article><b>${this.num(value)}</b><span>${label}</span></article>`;
		this.$.find(".tqc-stats").html(
			stat("已读取文件", this.results.size) + stat("数量行", totalRows)
			+ stat("待处理与财务数量合计", totalQuantity) + stat("售后补寄数量（另列）", totalReship)
		);
		const heading = `<thead><tr><th>SKU ID</th><th>图片</th><th>SKU货号</th><th>货品名称</th>${TEMU_QUANTITY_FILE_TYPES.map((type) => `<th>${this.esc(type)}</th>`).join("")}<th>可用</th><th>待收货</th><th>待发货</th><th>数量合计</th><th>售后补寄</th></tr></thead>`;
		const manualInput = (sku, field) => `<input class="form-control tqc-manual-input" type="number" min="0" step="1" inputmode="numeric" data-sku="${this.esc(sku)}" data-field="${field}" value="${this.esc(this.manualQuantities.get(sku)?.[field] || "")}" aria-label="${field === "available" ? "可用" : field === "receiving" ? "待收货" : "待发货"}数量">`;
		const body = rows.map((row) => {
			const sku = row.sku_id || row.sku_code;
			const image = row.item_image ? `<img src="${this.esc(row.item_image)}" alt="${this.esc(row.product_name || sku)}" loading="lazy">` : '<span class="tqc-no-image">无图</span>';
			return `<tr data-file-total="${this.fileTotalOf(row)}"><td>${this.esc(row.sku_id || "—")}</td><td class="tqc-image">${row.item_code ? `<a href="/app/item/${encodeURIComponent(row.item_code)}" title="${this.esc(row.item_code)}">${image}</a>` : image}</td><td>${this.esc(row.sku_code || "—")}</td><td title="${this.esc(row.product_name || "")}">${this.esc(row.product_name || "—")}</td>${TEMU_QUANTITY_FILE_TYPES.map((type) => `<td>${this.num(row.quantities[type] || 0)}</td>`).join("")}<td>${manualInput(sku, "available")}</td><td>${manualInput(sku, "receiving")}</td><td>${manualInput(sku, "shipping")}</td><td class="tqc-total">${this.num(this.totalOf(row))}</td><td>${this.num(row.reship_quantity)}</td></tr>`;
		}).join("");
		this.$.find(".tqc-table-wrap").html(`<table class="tqc-table">${heading}<tbody>${body || '<tr><td colspan="15" class="tqc-empty">上传文件后，这里会按 SKU 显示数量。</td></tr>'}</tbody></table>`);
	}

	totalOf(row) {
		return this.fileTotalOf(row) + this.manualTotalOf(row.sku_id || row.sku_code);
	}

	fileTotalOf(row) {
		return TEMU_QUANTITY_FILE_TYPES.reduce((total, type) => total + Number(row.quantities[type] || 0), 0);
	}

	manualTotalOf(sku) {
		const values = this.manualQuantities.get(sku) || {};
		return ["available", "receiving", "shipping"].reduce((total, field) => total + (Number(values[field]) || 0), 0);
	}

	num(value) {
		return Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 6 });
	}

	esc(value) {
		return frappe.utils.escape_html(String(value ?? ""));
	}
}
