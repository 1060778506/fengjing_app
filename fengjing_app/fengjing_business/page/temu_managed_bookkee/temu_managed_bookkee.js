const TEMU_BOOKKEEPING_METHOD =
	"fengjing_app.fengjing_business.page.temu_managed_bookkee.temu_managed_bookkee";
const TEMU_BOOKKEEPING_DOCTYPE = "Temu Financial Reconciliation Batch";
const INITIAL_FILE_SLOTS = 11;
const DEFAULT_FILE_TYPES = [
	"欧区待处理",
	"全球待处理",
	"美区待处理",
	"欧区财务",
	"全球财务",
	"美区财务",
	"财务明细",
	"广告》数据报表》店铺数据报表",
	"广告》数据报表》商品数据报表",
	"广告》财务管理》推广流水》对账单",
	"广告》财务管理》推广流水》已支付",
];

frappe.pages["temu_managed_bookkee"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Temu 全托管财务对账",
		single_column: true,
	});
	wrapper.temuManagedBookkeeping = new TemuManagedBookkeepingPage(wrapper, page);
};

frappe.pages["temu_managed_bookkee"].on_page_show = function (wrapper) {
	wrapper.temuManagedBookkeeping?.show();
};

class TemuManagedBookkeepingPage {
	constructor(wrapper, page) {
		this.wrapper = wrapper;
		this.page = page;
		this.currentBatch = null;
		this.fileSlots = [];
		this.batchList = [];
		this.dirty = false;
		this.loaded = false;
		this.renderShell();
		this.makeDateControls();
		this.bindEvents();
		this.addPageActions();
		this.startNewBatch(false);
	}

	renderShell() {
		$(this.wrapper).addClass("tmfb-page");
		this.$main = $(this.page.main);
		this.$main.html(`
			<div class="tmfb-shell">
				<aside class="tmfb-sidebar">
					<div class="tmfb-sidebar-head">
						<div><div class="tmfb-eyebrow">上传记录</div><h3>对账批次</h3></div>
						<button type="button" class="btn btn-primary btn-sm tmfb-new-batch">新建</button>
					</div>
					<div class="tmfb-search-wrap">
						<span class="octicon octicon-search"></span>
						<input type="search" class="form-control tmfb-search" placeholder="搜索批次或备注">
					</div>
					<div class="tmfb-batch-list"></div>
				</aside>
				<section class="tmfb-workspace">
					<div class="tmfb-workspace-head">
						<div>
							<div class="tmfb-eyebrow">Temu 财务文件</div>
							<h2 class="tmfb-current-title">新建对账批次</h2>
							<div class="tmfb-current-meta">日期可以稍后填写，文件仅支持 XLSX</div>
						</div>
						<div class="tmfb-head-actions">
							<span class="tmfb-status-pill">草稿</span>
							<button type="button" class="btn btn-danger btn-sm tmfb-delete-batch hide">删除整条</button>
						</div>
					</div>
					<div class="tmfb-date-card">
						<div class="tmfb-date-field tmfb-start-date"></div>
						<div class="tmfb-date-arrow">至</div>
						<div class="tmfb-date-field tmfb-end-date"></div>
						<div class="tmfb-file-counter"><strong>0</strong><span>个文件</span></div>
					</div>
					<div class="tmfb-files-head">
						<div><h3>对账文件</h3><p>拖入文件或点击区域选择。保存后可以单独替换或删除。</p></div>
					</div>
					<div class="tmfb-file-grid"></div>
					<div class="tmfb-remarks-card">
						<label for="tmfb-remarks">备注</label>
						<textarea id="tmfb-remarks" class="form-control tmfb-remarks" rows="3" placeholder="可填写这次对账文件的说明"></textarea>
					</div>
				</section>
			</div>
		`);
		this.$batchList = this.$main.find(".tmfb-batch-list");
		this.$fileGrid = this.$main.find(".tmfb-file-grid");
	}

	makeDateControls() {
		this.startDateControl = frappe.ui.form.make_control({
			parent: this.$main.find(".tmfb-start-date"),
			df: { fieldname: "start_date", fieldtype: "Datetime", label: "开始日期", change: () => this.normalizeDateControl(this.startDateControl, "00:00:00") },
			render_input: true,
		});
		this.endDateControl = frappe.ui.form.make_control({
			parent: this.$main.find(".tmfb-end-date"),
			df: { fieldname: "end_date", fieldtype: "Datetime", label: "结束日期", change: () => this.normalizeDateControl(this.endDateControl, "23:59:59") },
			render_input: true,
		});
	}

	normalizeDateControl(control, boundaryTime) {
		const value = control?.get_value();
		if (value) {
			const normalized = `${String(value).split(" ")[0]} ${boundaryTime}`;
			if (value !== normalized) control.set_value(normalized);
		}
		this.markDirty();
	}

	addPageActions() {
		this.page.set_primary_action("保存批次", () => this.saveBatch(), "save");
		this.page.add_inner_button("刷新记录", () => this.loadBatchList());
	}

	bindEvents() {
		this.$main.on("click", ".tmfb-new-batch", () => this.confirmDiscard(() => this.startNewBatch()));
		this.$main.on("click", ".tmfb-delete-batch", () => this.deleteCurrentBatch());
		this.$main.on("input", ".tmfb-search", frappe.utils.debounce(() => this.renderBatchList(), 180));
		this.$main.on("input", ".tmfb-remarks", () => this.markDirty());
		this.$main.on("click", ".tmfb-batch-item", (event) => {
			const name = $(event.currentTarget).data("name");
			if (name && name !== this.currentBatch?.name) this.confirmDiscard(() => this.openBatch(name));
		});
		this.$main.on("input", ".tmfb-file-type", (event) => {
			const slot = this.getSlot($(event.currentTarget).closest(".tmfb-file-slot").data("slot-id"));
			if (slot) {
				slot.file_type = event.currentTarget.value;
				this.markDirty();
			}
		});
		this.$main.on("click", ".tmfb-file-slot-body, .tmfb-replace-file", (event) => {
			if ($(event.target).closest("a, button, input").length && !$(event.target).hasClass("tmfb-replace-file")) return;
			event.preventDefault();
			$(event.currentTarget).closest(".tmfb-file-slot").find(".tmfb-file-input").trigger("click");
		});
		this.$main.on("change", ".tmfb-file-input", (event) => {
			const file = event.currentTarget.files?.[0];
			const slotId = $(event.currentTarget).closest(".tmfb-file-slot").data("slot-id");
			if (file) this.setPendingFile(slotId, file);
			event.currentTarget.value = "";
		});
		this.$main.on("dragover", ".tmfb-file-slot-body", (event) => {
			event.preventDefault();
			$(event.currentTarget).closest(".tmfb-file-slot").addClass("is-dragging");
		});
		this.$main.on("dragleave drop", ".tmfb-file-slot-body", (event) => {
			event.preventDefault();
			const $slot = $(event.currentTarget).closest(".tmfb-file-slot").removeClass("is-dragging");
			if (event.type === "drop") {
				const file = event.originalEvent.dataTransfer?.files?.[0];
				if (file) this.setPendingFile($slot.data("slot-id"), file);
			}
		});
		this.$main.on("click", ".tmfb-remove-file", (event) => {
			event.preventDefault();
			event.stopPropagation();
			this.removeFile($(event.currentTarget).closest(".tmfb-file-slot").data("slot-id"));
		});
	}

	async show() {
		if (!this.loaded) {
			await this.loadBatchList();
			this.loaded = true;
		}
	}

	markDirty() {
		this.dirty = true;
	}

	confirmDiscard(action) {
		if (!this.dirty) return action();
		frappe.confirm("当前修改尚未保存，确定离开吗？", action);
	}

	makeEmptySlot(index) {
		return {
			client_id: frappe.utils.get_random(10), row_name: null,
			file_type: DEFAULT_FILE_TYPES[index - 1] || `文件 ${index}`,
			file_url: null, original_file_name: null, file_size: 0, file_hash: null,
			sort_order: index, uploaded_at: null, uploaded_by: null, notes: "", pending_file: null,
		};
	}

	startNewBatch(markDirty = false) {
		this.currentBatch = null;
		this.fileSlots = Array.from({ length: INITIAL_FILE_SLOTS }, (_, index) => this.makeEmptySlot(index + 1));
		this.startDateControl.set_value("");
		this.endDateControl.set_value("");
		this.$main.find(".tmfb-remarks").val("");
		this.$main.find(".tmfb-current-title").text("新建对账批次");
		this.$main.find(".tmfb-status-pill").text("草稿");
		this.$main.find(".tmfb-delete-batch").addClass("hide");
		this.$batchList.find(".tmfb-batch-item").removeClass("is-active");
		this.dirty = markDirty;
		this.renderFileSlots();
	}

	getSlot(slotId) {
		return this.fileSlots.find((slot) => slot.client_id === slotId);
	}

	setPendingFile(slotId, file) {
		if (!file?.name?.toLowerCase().endsWith(".xlsx")) {
			frappe.msgprint({ title: "文件格式不正确", message: "这里只允许上传 .xlsx 文件。", indicator: "red" });
			return;
		}
		const slot = this.getSlot(slotId);
		if (!slot) return;
		slot.pending_file = file;
		if (!slot.file_type || (slot.sort_order > INITIAL_FILE_SLOTS && /^文件 \d+$/.test(slot.file_type))) {
			slot.file_type = file.name.replace(/\.xlsx$/i, "");
		}
		this.markDirty();
		this.renderFileSlots();
	}

	async removeFile(slotId) {
		const slot = this.getSlot(slotId);
		if (!slot) return;
		if (!slot.row_name) {
			if (slot.pending_file) slot.pending_file = null;
			else if (this.fileSlots.length > INITIAL_FILE_SLOTS) this.fileSlots = this.fileSlots.filter((row) => row.client_id !== slotId);
			this.markDirty();
			this.renderFileSlots();
			return;
		}
		frappe.confirm(`确定删除文件“${frappe.utils.escape_html(slot.original_file_name || slot.file_type)}”吗？`, async () => {
			try {
				await frappe.call({
					method: `${TEMU_BOOKKEEPING_METHOD}.delete_batch_file`,
					args: { batch_name: this.currentBatch.name, row_name: slot.row_name },
					freeze: true, freeze_message: "正在删除文件…",
				});
				await this.reloadCurrentBatch();
				await this.loadBatchList();
				frappe.show_alert({ message: "文件已删除", indicator: "green" });
			} catch (error) {
				this.showError("文件删除失败", error);
			}
		});
	}

	reindexSlots() {
		this.fileSlots.forEach((slot, index) => { slot.sort_order = index + 1; });
	}

	renderFileSlots() {
		this.reindexSlots();
		this.$fileGrid.html(this.fileSlots.map((slot, index) => this.fileSlotHtml(slot, index)).join(""));
		this.$main.find(".tmfb-file-counter strong").text(this.fileSlots.filter((slot) => slot.file_url || slot.pending_file).length);
	}

	fileSlotHtml(slot, index) {
		const pending = slot.pending_file;
		const stored = Boolean(slot.file_url);
		const displayName = pending?.name || slot.original_file_name || "拖入 XLSX 文件";
		const helper = pending ? (stored ? "待保存并替换原文件" : "待保存") : stored ? this.fileSizeText(slot.file_size) : "也可以点击这里选择文件";
		const stateClass = pending ? "has-pending" : stored ? "has-file" : "is-empty";
		const openLink = stored && !pending
			? `<a class="btn btn-xs btn-default tmfb-open-file" href="${frappe.utils.escape_html(slot.file_url)}" target="_blank" rel="noopener">查看</a>` : "";
		const removeLabel = slot.row_name ? "删除" : pending ? "移除" : this.fileSlots.length > INITIAL_FILE_SLOTS ? "移除位置" : "";
		let layoutClass = "tmfb-file-slot--extra";
		if (index <= 2) layoutClass = "tmfb-file-slot--pending";
		else if (index <= 5) layoutClass = "tmfb-file-slot--finance";
		else if (index === 6) layoutClass = "tmfb-file-slot--featured";
		else if (index <= 10) layoutClass = "tmfb-file-slot--advertising";
		const extraRow = index >= INITIAL_FILE_SLOTS ? `style="grid-row: ${5 + Math.floor((index - INITIAL_FILE_SLOTS) / 3)}"` : "";
		return `
			<article class="tmfb-file-slot ${stateClass} ${layoutClass}" ${extraRow} data-slot-id="${slot.client_id}">
				<div class="tmfb-file-slot-head">
					<input class="form-control tmfb-file-type" value="${frappe.utils.escape_html(slot.file_type || "")}" placeholder="文件类型">
					<span class="tmfb-slot-number">${index + 1}</span>
				</div>
				<div class="tmfb-file-slot-body" tabindex="0" role="button">
					<input type="file" class="tmfb-file-input" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden>
					<div class="tmfb-file-icon">XLSX</div>
					<div class="tmfb-file-copy"><strong title="${frappe.utils.escape_html(displayName)}">${frappe.utils.escape_html(displayName)}</strong><span>${frappe.utils.escape_html(helper)}</span></div>
				</div>
				<div class="tmfb-file-actions">
					${openLink}<button type="button" class="btn btn-xs btn-default tmfb-replace-file">${stored || pending ? "替换" : "选择文件"}</button>
					${removeLabel ? `<button type="button" class="btn btn-xs btn-link text-danger tmfb-remove-file">${removeLabel}</button>` : ""}
				</div>
			</article>`;
	}

	fileSizeText(bytes) {
		const size = Number(bytes || 0);
		if (!size) return "文件已保存";
		if (size < 1024) return `${size} B`;
		if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
		return `${(size / 1024 / 1024).toFixed(2)} MB`;
	}

	async loadBatchList() {
		try {
			const response = await frappe.call({ method: `${TEMU_BOOKKEEPING_METHOD}.get_batches` });
			this.batchList = response.message || [];
			this.renderBatchList();
		} catch (error) {
			this.showError("对账批次读取失败", error);
		}
	}

	renderBatchList() {
		const query = String(this.$main.find(".tmfb-search").val() || "").trim().toLowerCase();
		const rows = this.batchList.filter((row) => !query || `${row.name} ${row.remarks || ""}`.toLowerCase().includes(query));
		if (!rows.length) {
			this.$batchList.html('<div class="tmfb-empty-list">暂无对账批次</div>');
			return;
		}
		this.$batchList.html(rows.map((row) => {
			const range = row.start_date || row.end_date ? `${row.start_date || "未设置"} 至 ${row.end_date || "未设置"}` : "未设置日期";
			return `<button type="button" class="tmfb-batch-item ${row.name === this.currentBatch?.name ? "is-active" : ""}" data-name="${frappe.utils.escape_html(row.name)}">
				<span class="tmfb-batch-item-top"><strong>${frappe.utils.escape_html(row.name)}</strong><em>${Number(row.file_count || 0)} 个文件</em></span>
				<span class="tmfb-batch-range">${frappe.utils.escape_html(range)}</span>
				<span class="tmfb-batch-item-bottom"><span>${frappe.utils.escape_html(row.status || "草稿")}</span><time>${frappe.datetime.str_to_user(row.modified)}</time></span>
			</button>`;
		}).join(""));
	}

	async openBatch(name) {
		try {
			const response = await frappe.call({ method: `${TEMU_BOOKKEEPING_METHOD}.get_batch`, args: { name } });
			this.applyBatch(response.message);
		} catch (error) {
			this.showError("对账批次读取失败", error);
		}
	}

	applyBatch(batch) {
		this.currentBatch = batch;
		const storedFiles = [...(batch.files || [])].sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));
		const slotCount = Math.max(INITIAL_FILE_SLOTS, ...storedFiles.map((row) => Number(row.sort_order || 0)));
		this.fileSlots = Array.from({ length: slotCount }, (_, index) => this.makeEmptySlot(index + 1));
		storedFiles.forEach((row) => {
			const namedPosition = DEFAULT_FILE_TYPES.indexOf(row.file_type);
			let position = namedPosition >= 0 ? namedPosition : Math.max(0, Number(row.sort_order || 1) - 1);
			while (this.fileSlots[position]?.row_name) position += 1;
			while (!this.fileSlots[position]) this.fileSlots.push(this.makeEmptySlot(this.fileSlots.length + 1));
			this.fileSlots[position] = {
				client_id: frappe.utils.get_random(10), row_name: row.name,
				file_type: DEFAULT_FILE_TYPES[position] || row.file_type || `文件 ${position + 1}`,
				file_url: row.file_url, original_file_name: row.original_file_name, file_size: row.file_size,
				file_hash: row.file_hash, sort_order: position + 1, uploaded_at: row.uploaded_at,
				uploaded_by: row.uploaded_by, notes: row.notes || "", pending_file: null,
			};
		});
		this.startDateControl.set_value(batch.start_date || "");
		this.endDateControl.set_value(batch.end_date || "");
		this.$main.find(".tmfb-remarks").val(batch.remarks || "");
		this.$main.find(".tmfb-current-title").text(batch.name);
		this.$main.find(".tmfb-status-pill").text(batch.status || "草稿");
		this.$main.find(".tmfb-delete-batch").toggleClass("hide", !batch.can_delete);
		this.dirty = false;
		this.renderFileSlots();
		this.renderBatchList();
	}

	async reloadCurrentBatch() {
		if (!this.currentBatch?.name) return;
		const response = await frappe.call({ method: `${TEMU_BOOKKEEPING_METHOD}.get_batch`, args: { name: this.currentBatch.name } });
		this.applyBatch(response.message);
	}

	async saveBatch() {
		const startDate = this.startDateControl.get_value() || null;
		const endDate = this.endDateControl.get_value() || null;
		if (startDate && endDate && startDate > endDate) return frappe.msgprint("结束日期不能早于开始日期。");
		const pendingSlots = this.fileSlots.filter((slot) => slot.pending_file);
		const metadata = this.fileSlots.filter((slot) => slot.row_name).map((slot) => ({
			name: slot.row_name, file_type: String(slot.file_type || "").trim(), sort_order: slot.sort_order, notes: slot.notes || "",
		}));
		frappe.dom.freeze("正在保存对账批次…");
		try {
			const saveResponse = await frappe.call({
				method: `${TEMU_BOOKKEEPING_METHOD}.save_batch`,
				args: { name: this.currentBatch?.name || null, start_date: startDate, end_date: endDate,
					remarks: this.$main.find(".tmfb-remarks").val() || "", file_metadata: metadata },
			});
			const batchName = saveResponse.message.name;
			for (const slot of pendingSlots) {
				const fileDoc = await this.uploadPrivateFile(slot.pending_file, batchName);
				await frappe.call({
					method: `${TEMU_BOOKKEEPING_METHOD}.register_file`,
					args: { batch_name: batchName, file_doc_name: fileDoc.name, row_name: slot.row_name || null,
						file_type: String(slot.file_type || slot.pending_file.name).trim(), sort_order: slot.sort_order, notes: slot.notes || "" },
				});
			}
			const syncResponse = await frappe.call({
				method: `${TEMU_BOOKKEEPING_METHOD}.sync_batch_data`,
				args: { batch_name: batchName },
			});
			const response = await frappe.call({ method: `${TEMU_BOOKKEEPING_METHOD}.get_batch`, args: { name: batchName } });
			this.applyBatch(response.message);
			await this.loadBatchList();
			const syncResult = syncResponse.message || {};
			const message = syncResult.status === "unchanged"
				? `对账批次已保存，${syncResult.total || 0} 条数据完整，无需重复写入`
				: `对账批次已保存并同步 ${syncResult.total || 0} 条数据`;
			frappe.show_alert({ message, indicator: "green" }, 9);
		} catch (error) {
			this.showError("对账批次保存失败", error);
		} finally {
			frappe.dom.unfreeze();
		}
	}

	uploadPrivateFile(file, batchName) {
		return new Promise((resolve, reject) => {
			const xhr = new XMLHttpRequest();
			xhr.open("POST", "/api/method/upload_file", true);
			xhr.setRequestHeader("Accept", "application/json");
			xhr.setRequestHeader("X-Frappe-CSRF-Token", frappe.csrf_token);
			xhr.onload = () => {
				let payload = {};
				try { payload = JSON.parse(xhr.responseText || "{}"); }
				catch (error) { return reject(new Error("文件上传返回了无法识别的内容。")); }
				if (xhr.status >= 200 && xhr.status < 300 && payload.message?.doctype === "File") return resolve(payload.message);
				reject(new Error(this.responseError(payload) || `文件上传失败（${xhr.status}）`));
			};
			xhr.onerror = () => reject(new Error("文件上传网络连接失败。"));
			const formData = new FormData();
			formData.append("file", file, file.name);
			formData.append("is_private", "1");
			formData.append("folder", "Home/Attachments");
			formData.append("doctype", TEMU_BOOKKEEPING_DOCTYPE);
			formData.append("docname", batchName);
			formData.append("fieldname", "files");
			xhr.send(formData);
		});
	}

	responseError(payload) {
		try {
			return JSON.parse(payload._server_messages || "[]").map((message) => JSON.parse(message).message).filter(Boolean).join("；");
		} catch (error) {
			return payload._error_message || payload.exception || "";
		}
	}

	deleteCurrentBatch() {
		if (!this.currentBatch?.name) return;
		const name = this.currentBatch.name;
		frappe.confirm(`确定删除整个批次“${frappe.utils.escape_html(name)}”吗？<br>其中保存的全部文件也会一起删除。`, async () => {
			try {
				await frappe.call({ method: `${TEMU_BOOKKEEPING_METHOD}.delete_batch`, args: { name }, freeze: true, freeze_message: "正在删除批次及文件…" });
				this.startNewBatch(false);
				await this.loadBatchList();
				frappe.show_alert({ message: "批次及文件已删除", indicator: "green" });
			} catch (error) {
				this.showError("批次删除失败", error);
			}
		});
	}

	showError(title, error) {
		console.error(title, error);
		frappe.msgprint({ title, message: error?.message || error?._server_messages || "请查看错误日志。", indicator: "red" });
	}
}
