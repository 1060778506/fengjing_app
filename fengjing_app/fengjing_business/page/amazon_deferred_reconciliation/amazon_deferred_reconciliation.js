frappe.pages["amazon-deferred-reconciliation"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("亚马逊递延交易对账"),
		single_column: true,
	});
	wrapper.amazonDeferredReconciliation = new AmazonDeferredReconciliationPage(page);
};

frappe.pages["amazon-deferred-reconciliation"].on_page_show = function (wrapper) {
	wrapper.amazonDeferredReconciliation?.refresh();
};

frappe.pages["amazon-deferred-reconciliation"].on_page_hide = function (wrapper) {
	wrapper.amazonDeferredReconciliation?.stopPolling();
};

class AmazonDeferredReconciliationPage {
	constructor(page) {
		this.page = page;
		this.snapshots = [];
		this.selected = null;
		this.activeTab = "orders";
		this.search = "";
		this.busy = false;
		this.capturing = false;
		this.poller = null;
		this.rangeMode = "90d";
		this.monthStart = null;
		this.monthEnd = null;
		this.selectedStore = "";
		this.storeChoiceInitialized = false;
		this.storeOptions = [];
		this.contentCountry = "";
		this.deferredOnly = true;
		this.renderShell();
		this.bindEvents();
		this.render();
	}

	renderShell() {
		$(this.page.main).html(`
			<div class="adr-root">
				<header class="adr-header">
					<div><h1>${__("亚马逊递延交易对账")}</h1><p>${__("将所选时间范围的财务明细、完整订单和当前余额保存在同一份快照中。")}</p></div>
					<button type="button" class="btn btn-primary adr-capture">${__("抓取并生成快照")}</button>
				</header>
				<section class="adr-range" aria-label="${__("时间范围")}">
					<div class="adr-range-heading"><strong>${__("时间范围")}</strong><span class="adr-range-hint"></span></div>
					<div class="adr-range-options"></div>
					<div class="adr-store-picker">
						<label for="adr-store-select">${__("抓取站点")}</label>
						<select id="adr-store-select" class="form-control adr-store-select"><option value="">${__("全部站点")}</option></select>
						<span class="adr-store-hint"></span>
					</div>
				</section>
				<div class="adr-layout">
					<aside class="adr-sidebar">
						<div class="adr-sidebar-head"><h2>${__("数据快照")}</h2><span class="adr-snapshot-count">0</span></div>
						<input class="form-control adr-search" type="search" placeholder="${__("搜索快照")}" aria-label="${__("搜索快照")}">
						<div class="adr-snapshot-list"></div>
					</aside>
					<main class="adr-main">
						<section class="adr-overview">
							<div class="adr-context">
								<div><span class="adr-eyebrow">${__("当前快照")}</span><h2 class="adr-title">${__("尚无快照")}</h2><p class="adr-subtitle">${__("点击“抓取并生成快照”开始核对。")}</p></div>
								<div class="adr-status"><span class="adr-status-dot"></span><span class="adr-status-text">${__("尚无快照")}</span></div>
							</div>
							<div class="adr-source-times">
								<div><span>${__("余额抓取")} <small data-source-status="balance"></small></span><strong data-time="balance">—</strong></div>
								<div><span><b data-source-label="transactions">${__("财务明细抓取")}</b> <small data-source-status="transactions"></small></span><strong data-time="transactions">—</strong></div>
								<div><span>${__("订单抓取")} <small data-source-status="orders"></small></span><strong data-time="orders">—</strong></div>
							</div>
						</section>
						<div class="adr-currency-cards"></div>
						<section class="adr-panel adr-detail-panel">
							<div class="adr-panel-heading"><h3>${__("快照内容")}</h3><span class="adr-detail-count"></span></div>
							<div class="adr-content-filter"><label for="adr-country-select">${__("国家筛选")}</label><select id="adr-country-select" class="form-control adr-country-select"><option value="">${__("全部国家")}</option></select><label class="adr-deferred-filter" for="adr-deferred-only"><input id="adr-deferred-only" type="checkbox" checked>${__("财务明细只显示已延迟")}</label></div>
							<div class="adr-detail-totals"></div>
							<div class="adr-detail-note"></div>
							<div class="adr-tabs" role="tablist">
								<button type="button" role="tab" class="adr-tab is-active" data-tab="orders">${__("所有订单")}</button>
								<button type="button" role="tab" class="adr-tab" data-tab="transactions">${__("财务明细")}</button>
								<button type="button" role="tab" class="adr-tab" data-tab="balances">${__("余额账号")}</button>
							</div>
							<div class="adr-detail-content"></div>
						</section>
					</main>
				</div>
			</div>`);
		this.$ = $(this.page.main).find(".adr-root");
	}

	bindEvents() {
		this.$.on("click", ".adr-capture", () => this.startSnapshot());
		this.$.on("click", "[data-range-days]", (event) => {
			this.rangeMode = event.currentTarget.dataset.rangeDays;
			this.monthStart = null;
			this.monthEnd = null;
			this.renderRange();
		});
		this.$.on("click", "[data-range-month]", (event) => this.selectMonth(event.currentTarget.dataset.rangeMonth));
		this.$.on("change", ".adr-store-select", (event) => {
			this.selectedStore = event.currentTarget.value;
			this.storeChoiceInitialized = true;
			this.renderStoreOptions();
			this.renderRange();
		});
		this.$.on("input", ".adr-search", (event) => {
			this.search = event.currentTarget.value.trim().toLowerCase();
			this.renderList();
		});
		this.$.on("click", "[data-snapshot]", (event) => this.selectSnapshot(event.currentTarget.dataset.snapshot));
		this.$.on("click", "[data-delete-snapshot]", (event) => {
			const name = event.currentTarget.dataset.deleteSnapshot;
			const row = this.snapshots.find((item) => item.name === name);
			if (!row || ["等待中", "抓取中"].includes(row.snapshot_status)) return;
			frappe.confirm(
				`${__("删除快照")}“${this.esc(row.snapshot_label || name)}”？${__("对应的快照表记录也会删除，无法恢复。")}`,
				() => this.deleteSnapshot(name)
			);
		});
		this.$.on("click", "[data-tab]", (event) => {
			this.activeTab = event.currentTarget.dataset.tab;
			this.renderDetails();
		});
		this.$.on("change", ".adr-country-select", (event) => {
			this.contentCountry = event.currentTarget.value;
			this.renderDetails();
		});
		this.$.on("change", ".adr-deferred-filter input", (event) => {
			this.deferredOnly = event.currentTarget.checked;
			this.renderDetails();
		});
	}

	setSnapshots(rows) {
		this.snapshots = Array.isArray(rows) ? rows : [];
		const current = this.snapshots.find((row) => row.name === this.selected?.name) || this.snapshots[0] || null;
		this.selected = current && current.name === this.selected?.name ? { ...this.selected, ...current } : current;
		this.render();
	}

	async call(method, args = {}) {
		const result = await frappe.call({
			method: `fengjing_app.fengjing_business.page.amazon_deferred_reconciliation.amazon_deferred_reconciliation.${method}`,
			args,
		});
		return result.message;
	}

	async refresh() {
		if (this.busy) return;
		this.busy = true;
		try {
			const [rows, options] = await Promise.all([
				this.call("list_snapshots"), this.call("get_store_options"),
			]);
			this.storeOptions = Array.isArray(options) ? options : [];
			if (!this.storeChoiceInitialized && this.storeOptions.length) {
				this.selectedStore = this.storeOptions.find((row) =>
					String(row.marketplace_id || "").toUpperCase() === "ATVPDKIKX0DER"
				)?.name || "";
				this.storeChoiceInitialized = true;
			}
			this.renderStoreOptions();
			this.setSnapshots(rows);
			if (this.selected) await this.selectSnapshot(this.selected.name);
			this.updatePolling();
		} catch (error) {
			this.showError(error);
		} finally {
			this.busy = false;
		}
	}

	async selectSnapshot(name) {
		if (!name) return;
		const row = this.snapshots.find((item) => item.name === name);
		if (!row) return;
		this.selected = this.selected?.name === name ? { ...this.selected, ...row } : row;
		this.render();
		try {
			const detail = await this.call("get_snapshot", { name });
			if (this.selected?.name === name) {
				this.selected = detail;
				this.render();
			}
		} catch (error) {
			this.showError(error);
		}
	}

	async startSnapshot() {
		const $button = this.$.find(".adr-capture");
		if (this.capturing || (this.rangeMode === "months" && !this.monthEnd)) return;
		this.capturing = true;
		$button.text(__("正在启动…"));
		this.renderRange();
		try {
			const result = await this.call("start_snapshot", {
				range_mode: this.rangeMode,
				month_start: this.rangeMode === "months" ? this.monthStart : null,
				month_end: this.rangeMode === "months" ? this.monthEnd : null,
				amazon_store: this.selectedStore || null,
			});
			frappe.show_alert({ message: result.status === "already_running" ? __("已有快照正在抓取") : __("三组抓取任务已启动"), indicator: "blue" });
			const rows = await this.call("list_snapshots");
			this.setSnapshots(rows);
			await this.selectSnapshot(result.name);
			this.updatePolling();
		} catch (error) {
			this.showError(error);
		} finally {
			this.capturing = false;
			$button.text(__("抓取并生成快照"));
			this.renderRange();
		}
	}

	async deleteSnapshot(name) {
		try {
			await this.call("delete_snapshot", { name });
			if (this.selected?.name === name) this.selected = null;
			this.setSnapshots(this.snapshots.filter((row) => row.name !== name));
			if (this.selected) await this.selectSnapshot(this.selected.name);
			frappe.show_alert({ message: __("快照及对应记录已删除"), indicator: "green" });
			this.updatePolling();
		} catch (error) {
			this.showError(error);
		}
	}

	updatePolling() {
		const active = this.snapshots.some((row) => ["等待中", "抓取中"].includes(row.snapshot_status));
		if (active && !this.poller) this.poller = window.setInterval(() => this.refresh(), 10000);
		if (!active) this.stopPolling();
	}

	stopPolling() {
		if (this.poller) window.clearInterval(this.poller);
		this.poller = null;
	}

	showError(error) {
		const message = error?.message || __("请求失败，请查看服务器错误信息。 ");
		frappe.show_alert({ message: this.esc(message), indicator: "red" }, 8);
	}

	render() {
		this.$.find("#adr-deferred-only").prop("checked", this.deferredOnly);
		this.renderRange();
		this.renderList();
		this.renderContext();
		this.renderSummary();
		this.renderCountryOptions();
		this.renderDetails();
	}

	monthOptions() {
		const now = new Date();
		const today = frappe.datetime?.get_today?.()
			|| `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-01`;
		const match = /^(\d{4})-(\d{1,2})/.exec(today);
		const current = match ? new Date(Number(match[1]), Number(match[2]) - 1, 1) : new Date();
		return Array.from({ length: 12 }, (_, index) => {
			const date = new Date(current.getFullYear(), current.getMonth() - 11 + index, 1);
			const year = date.getFullYear();
			const month = date.getMonth() + 1;
			return { key: `${year}-${String(month).padStart(2, "0")}`, label: `${String(year).slice(-2)}年${month}月` };
		});
	}

	selectMonth(key) {
		if (!this.monthOptions().some((month) => month.key === key)) return;
		if (this.rangeMode !== "months" || !this.monthStart || this.monthEnd) {
			this.rangeMode = "months";
			this.monthStart = key;
			this.monthEnd = null;
		} else {
			this.monthEnd = key;
			if (this.monthEnd < this.monthStart) [this.monthStart, this.monthEnd] = [this.monthEnd, this.monthStart];
		}
		this.renderRange();
	}

	renderRange() {
		const months = this.monthOptions();
		if (this.rangeMode === "months" && !months.some((month) => month.key === this.monthStart)) {
			this.rangeMode = "90d";
			this.monthStart = null;
			this.monthEnd = null;
		}
		const inRange = (key) => this.rangeMode === "months" && key >= this.monthStart && key <= (this.monthEnd || this.monthStart);
		this.$.find(".adr-range-options").html(`
			${[90, 180].map((days) => `<button type="button" class="adr-range-button ${this.rangeMode === `${days}d` ? "is-active" : ""}" data-range-days="${days}d" aria-pressed="${this.rangeMode === `${days}d`}">${__(`${days}天`)}</button>`).join("")}
			${months.map((month) => `<button type="button" class="adr-range-button ${inRange(month.key) ? "is-active" : ""} ${month.key === this.monthStart ? "is-start" : ""} ${month.key === this.monthEnd ? "is-end" : ""}" data-range-month="${month.key}" aria-pressed="${inRange(month.key)}">${month.label}</button>`).join("")}`);
		const first = months.find((month) => month.key === this.monthStart);
		const last = months.find((month) => month.key === this.monthEnd);
		const selection = this.rangeMode === "90d" ? __("默认：最近90天")
			: this.rangeMode === "180d" ? __("最近180天")
			: last ? `${first.label}—${last.label} · ${__("按所选月份抓取财务明细和订单")}`
				: `${first?.label || ""} · ${__("再点一个月份作为结束月份；单月可再次点击同一月份")}`;
		this.$.find(".adr-range-hint").text(selection);
		const $capture = this.$.find(".adr-capture");
		const selected = this.storeOptions.find((row) => row.name === this.selectedStore);
		const storesReady = this.selectedStore ? !!selected?.ready : this.storeOptions.length > 0 && this.storeOptions.every((row) => row.ready);
		$capture.prop("disabled", this.capturing || !storesReady || (this.rangeMode === "months" && !this.monthEnd));
		$capture.attr("title", !storesReady ? __("请检查站点的余额、财务和订单配置。")
			: this.rangeMode === "months" && !this.monthEnd ? __("请再选择结束月份。") : "");
	}

	renderStoreOptions() {
		if (this.selectedStore && !this.storeOptions.some((row) => row.name === this.selectedStore)) this.selectedStore = "";
		const options = this.storeOptions.map((row) => {
			const label = [row.label, row.marketplace_id].filter(Boolean).join(" · ");
			return `<option value="${this.esc(row.name)}" ${row.ready ? "" : "disabled"}>${this.esc(label)}${row.ready ? "" : ` · ${__("配置未完成")}`}</option>`;
		});
		this.$.find(".adr-store-select").html(`<option value="">${__("全部站点")}</option>${options.join("")}`).val(this.selectedStore);
		const selected = this.storeOptions.find((row) => row.name === this.selectedStore);
		const region = ({ "North America": __("北美区域"), Europe: __("欧洲区域"), "Far East": __("远东区域") })[selected?.api_region] || selected?.api_region || "—";
		this.$.find(".adr-store-hint").text(selected
			? `${__("仅抓取所选站点")} · ${region}`
			: `${__("抓取所有已启用站点")} · ${this.storeOptions.length} ${__("个站点")}`);
	}

	renderList() {
		this.$.find(".adr-snapshot-count").text(this.snapshots.length);
		const rows = this.snapshots.filter((row) =>
			[row.snapshot_label, row.amazon_store, row.marketplace_id, row.requested_at]
				.some((value) => String(value || "").toLowerCase().includes(this.search))
		);
		this.$.find(".adr-snapshot-list").html(rows.map((row) => `
			<div class="adr-snapshot-entry ${row.name === this.selected?.name ? "is-selected" : ""}">
			<button type="button" class="adr-snapshot" data-snapshot="${this.esc(row.name)}">
				<span class="adr-snapshot-title">${this.esc(row.snapshot_label || this.date(row.requested_at) || __("未命名快照"))}</span>
				<span class="adr-snapshot-meta">${this.esc(row.amazon_store || row.snapshot_scope || __("全部站点"))} · ${this.esc(row.snapshot_status || __("等待中"))}</span>
				<span class="adr-snapshot-age">${this.esc(this.age(row.completed_at || row.requested_at))}</span>
			</button>
			<button type="button" class="adr-delete-snapshot" data-delete-snapshot="${this.esc(row.name)}" title="${__("删除这次快照")}" aria-label="${__("删除这次快照")}" ${["等待中", "抓取中"].includes(row.snapshot_status) || [row.balance_status, row.transactions_status, row.orders_status].some((status) => ["等待中", "运行中"].includes(status)) ? "disabled" : ""}>×</button>
			</div>`).join("") || `<div class="adr-sidebar-empty">${this.snapshots.length ? __("没有匹配的快照") : __("暂无快照，点击右上角开始抓取。")}</div>`);
	}

	renderContext() {
		const row = this.selected;
		this.$.find('[data-source-label="transactions"]').text(Number(row?.snapshot_schema_version || 1) >= 2
			? __("财务明细抓取") : __("递延交易抓取"));
		this.$.find(".adr-title").text(row?.snapshot_label || __("尚无快照"));
		const range = row?.range_start && row?.range_end ? ` · ${__("财务/订单范围")} ${this.date(row.range_start)} — ${this.date(row.range_end)}（${__("不含结束时刻")}）` : "";
		this.$.find(".adr-subtitle").text(row
			? `${__("发起于")} ${this.date(row.requested_at)} · ${this.age(row.completed_at || row.requested_at)}${range}${row.last_error ? ` · ${row.last_error}` : ""}`
			: __("点击“抓取并生成快照”开始核对。"));
		this.$.find(".adr-status-text").text(row?.snapshot_status || __("尚无快照"));
		this.$.find(".adr-status").toggleClass("is-complete", row?.snapshot_status === "已完成");
		this.$.find(".adr-status").toggleClass("is-error", ["失败", "不完整"].includes(row?.snapshot_status));
		for (const [key, field, statusField] of [
			["balance", "balance_fetched_at", "balance_status"],
			["transactions", "transactions_fetched_at", "transactions_status"],
			["orders", "orders_fetched_at", "orders_status"],
		]) {
			this.$.find(`[data-source-status=${key}]`).text(row?.[statusField] || "—");
			this.$.find(`[data-time=${key}]`).text(row?.[field] ? `${this.date(row[field])} · ${this.age(row[field])}` : "—");
		}
	}

	renderSummary() {
		const balances = this.rows(this.selected?.balance_json);
		const version = Number(this.selected?.snapshot_schema_version || 1);
		const transactions = version >= 2
			? this.uniqueTransactions(this.rows(this.selected?.financial_transactions_json).filter((row) => row.transaction_status === "DEFERRED"))
			: this.uniqueTransactions(this.rows(this.selected?.deferred_transactions_json));
		const groups = new Map();
		const getGroup = (row) => {
			const key = [row.amazon_store || row.store || "", row.marketplace_id || "", row.currency_code || ""].join("|");
			if (!groups.has(key)) groups.set(key, { store: row.amazon_store || row.store || "", marketplace: row.marketplace_id || "", currency: row.currency_code || "", balance: 0, detail: 0, count: 0 });
			return groups.get(key);
		};
		balances.forEach((row) => { getGroup(row).balance += this.number(row.deferred_balance); });
		transactions.forEach((row) => { const group = getGroup(row); group.detail += this.number(row.total_amount ?? row.net_amount); group.count += 1; });
		const result = [...groups.values()].sort((a, b) => `${a.currency}|${a.store}`.localeCompare(`${b.currency}|${b.store}`));
		const byCurrency = new Map();
		result.forEach((row) => {
			if (!byCurrency.has(row.currency)) byCurrency.set(row.currency, { currency: row.currency, balance: 0, detail: 0, count: 0 });
			const item = byCurrency.get(row.currency);
			item.balance += row.balance;
			item.detail += row.detail;
			item.count += row.count;
		});
		this.$.find(".adr-currency-cards").html([...byCurrency.values()].map((row) => `
			<article class="adr-currency-card"><span>${this.esc(row.currency || __("未知币种"))}</span><strong>${this.money(row.balance, row.currency)}</strong><small>${__("余额中的递延")}</small><div><span>${__("明细合计")} <b>${this.money(row.detail, row.currency)}</b></span><span>${__("待核差额")} <b class="${Math.abs(row.balance - row.detail) > 0.005 ? "adr-difference" : ""}">${this.money(row.balance - row.detail, row.currency)}</b></span></div></article>`).join("") || `<div class="adr-empty adr-empty-cards">${__("当前快照没有余额或递延交易数据。")}</div>`);
	}

	allSnapshotRows() {
		const version = Number(this.selected?.snapshot_schema_version || 1);
		return {
			orders: this.rows(this.selected?.orders_json),
			transactions: version >= 2
				? this.rows(this.selected?.financial_transactions_json)
				: this.uniqueTransactions(this.rows(this.selected?.deferred_transactions_json)),
			balances: this.rows(this.selected?.balance_json),
		};
	}

	filteredSnapshotRows() {
		const rows = this.allSnapshotRows();
		const filtered = this.contentCountry
			? Object.fromEntries(Object.entries(rows).map(([key, values]) => [
				key, values.filter((row) => String(row.marketplace_id || "").toUpperCase() === this.contentCountry),
			]))
			: rows;
		if (this.deferredOnly) filtered.transactions = filtered.transactions.filter(
			(row) => String(row.transaction_status || "").toUpperCase() === "DEFERRED"
		);
		return filtered;
	}

	// An Amazon order/transaction can have several SKU rows, each carrying its full amount.
	// Count that amount once per source ID and store when displaying monetary totals.
	sumByCurrency(rows, idField, amountField) {
		const seen = new Set();
		const totals = new Map();
		rows.forEach((row) => {
			const key = [row.amazon_store || row.store || "", row.marketplace_id || "", row[idField] || row.name || ""].join("|");
			if (seen.has(key)) return;
			seen.add(key);
			const currency = row.currency_code || __("未知币种");
			totals.set(currency, (totals.get(currency) || 0) + this.number(row[amountField]));
		});
		return [...totals].sort(([a], [b]) => a.localeCompare(b)).map(([currency, amount]) => this.money(amount, currency)).join(" · ") || "—";
	}

	renderDetailTotals(rows) {
		const groups = [
			[__("财务明细"), rows.transactions, "transaction_id", "total_amount"],
			[__("所有订单"), rows.orders, "amazon_order_id", "order_total"],
			[__("余额账号"), rows.balances, "name", "total_balance"],
		];
		this.$.find(".adr-detail-totals").html(groups.map(([label, values, id, amount]) => `
			<div class="adr-detail-total"><span>${label}${label === __("财务明细") && this.deferredOnly ? ` · ${__("已延迟")}` : ""}</span><strong>${this.esc(this.sumByCurrency(values, id, amount))}</strong><small>${values.length} ${__("条记录")}</small></div>
		`).join(""));
	}

	countryLabel(row) {
		const id = String(row.marketplace_id || "").toUpperCase();
		const known = {
			ATVPDKIKX0DER: "美国", A2EUQ1WTGCTBG2: "加拿大", A1AM78C64UM0Y8: "墨西哥", A2Q3Y263D00KWC: "巴西",
			A1F83G8C2ARO7P: "英国", A13V1IB3VIYZZH: "法国", A1PA6795UKMFR9: "德国", A1RKKUPIHCS9HS: "西班牙",
			APJ6JRA9NG5V4: "意大利", A1805IZSGTT6HS: "荷兰", AMEN7PMS3EDWL: "比利时", A1C3SOZRARQ6R3: "波兰",
			A28R8C7NBKEWEA: "爱尔兰", A2NODRKZP88ZB9: "瑞典", A1VC38T7YXB528: "日本", A39IBJ37TRP1C6: "澳大利亚",
		};
		return known[id] || row.country || this.storeOptions.find((store) => store.marketplace_id === id)?.country || id;
	}

	renderCountryOptions() {
		const rows = this.allSnapshotRows();
		const countries = new Map();
		for (const row of [...rows.orders, ...rows.transactions, ...rows.balances]) {
			const id = String(row.marketplace_id || "").toUpperCase();
			if (id && !countries.has(id)) countries.set(id, this.countryLabel(row));
		}
		if (this.contentCountry && !countries.has(this.contentCountry)) this.contentCountry = "";
		this.$.find(".adr-country-select").html(`<option value="">${__("全部国家")}</option>${[...countries].sort((a, b) => a[1].localeCompare(b[1], "zh-CN")).map(([id, label]) => `<option value="${this.esc(id)}">${this.esc(label)}</option>`).join("")}`).val(this.contentCountry);
	}

	orderRelationKey(row) {
		const orderId = String(row.amazon_order_id || "").trim();
		if (!orderId) return "";
		return [row.store || "", String(row.marketplace_id || "").toUpperCase(), orderId].join("|");
	}

	linkedOrdersCell(row, orderIndex) {
		const key = this.orderRelationKey(row);
		const matches = key ? orderIndex.get(key) || [] : [];
		if (!matches.length) return `<span class="adr-unlinked">${__("未关联")}</span>`;
		return `<div class="adr-linked-orders">${matches.map((order) => {
			const label = `${order.amazon_order_id || order.name}${order.sku ? ` · SKU ${order.sku}` : ""}`;
			return order.name
				? `<a href="/app/amazon-order-synchronization/${encodeURIComponent(order.name)}" title="${this.esc(label)}">${this.esc(label)}</a>`
				: `<span>${this.esc(label)}</span>`;
		}).join("")}</div>`;
	}

	linkedOrderAmountCell(row, orderIndex) {
		const key = this.orderRelationKey(row);
		const matches = key ? orderIndex.get(key) || [] : [];
		const order = matches.find((item) => item.order_total !== null && item.order_total !== undefined && item.order_total !== "");
		return order ? this.money(order.order_total, order.currency_code || row.currency_code) : "—";
	}

	financialTaxCell(row) {
		const fields = ["product_sales_tax", "shipping_credits_tax", "promotional_rebates_tax"];
		if (fields.every((field) => row[field] === null || row[field] === undefined || row[field] === "")) return "—";
		return this.money(fields.reduce((sum, field) => sum + this.number(row[field]), 0), row.currency_code);
	}

	renderDetails() {
		const version = Number(this.selected?.snapshot_schema_version || 1);
		const filtered = this.filteredSnapshotRows();
		this.renderDetailTotals(filtered);
		const labels = { orders: __("所有订单"), transactions: __("财务明细"), balances: __("余额账号") };
		this.$.find(".adr-detail-note").text(this.selected && version < 2
			? __("旧快照只保存了关联订单和递延交易；重新抓取快照后可查看所选范围内的所有订单和财务明细。")
			: "").toggle(!!this.selected && version < 2);
		this.$.find(".adr-tab").each((_, element) => {
			const active = element.dataset.tab === this.activeTab;
			$(element).text(labels[element.dataset.tab]).toggleClass("is-active", active).attr("aria-selected", active);
		});
		const rows = filtered[this.activeTab];
		this.$.find(".adr-detail-count").text(`${rows.length} ${__("条记录")}`);
		const columns = {
			transactions: [["posted_date", __("记账时间")], ["amazon_order_id", __("订单号")], ["transaction_id", __("交易 ID")], ["store", __("店铺")], ["transaction_type", __("类型")], ["transaction_status", __("状态")], ["sku", "SKU"], ["asin", "ASIN"], ["release_date", __("预计释放")], ["product_sales", __("商品销售额（财务）")], ["financial_tax", __("税金（财务）")], ["total_amount", __("交易金额（财务）")], ["linked_orders", __("已关联所有订单")], ["linked_order_amount", __("订单总额（订单）")]],
			orders: [["amazon_order_id", __("订单号")], ["store", __("店铺")], ["purchase_date", __("购买时间")], ["order_status", __("订单状态")], ["sku", "SKU"], ["asin", "ASIN"], ["quantity_ordered", __("数量")], ["order_total", __("订单总额（订单）")]],
			balances: [["amazon_store", __("店铺")], ["marketplace_id", __("站点")], ["account_type", __("账户类型")], ["total_balance", __("总余额")], ["available_balance", __("可用")], ["reserved_balance", __("预留")], ["deferred_balance", __("递延")]],
		}[this.activeTab];
		const moneyFields = new Set(["product_sales", "total_amount", "order_total", "total_balance", "available_balance", "reserved_balance", "deferred_balance"]);
		const orderIndex = new Map();
		if (this.activeTab === "transactions") filtered.orders.forEach((order) => {
			const key = this.orderRelationKey(order);
			if (!key) return;
			if (!orderIndex.has(key)) orderIndex.set(key, []);
			orderIndex.get(key).push(order);
		});
		const amountNote = this.activeTab === "balances" ? "" : `<p class="adr-amount-note">${__("订单总额（订单）来自亚马逊 OrderTotal，可能包含商品金额、运费和税费，并受优惠影响；商品销售额、税金和交易金额来自财务明细。税金汇总商品税、运费税和促销税，不扣减平台代扣税。")}</p>`;
		this.$.find(".adr-detail-content").html(`${amountNote}<div class="adr-table-wrap"><table><thead><tr>${columns.map(([, label]) => `<th>${label}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${columns.map(([key]) => `<td>${key === "linked_orders" ? this.linkedOrdersCell(row, orderIndex) : key === "linked_order_amount" ? this.linkedOrderAmountCell(row, orderIndex) : key === "financial_tax" ? this.financialTaxCell(row) : moneyFields.has(key) ? row[key] === null || row[key] === undefined || row[key] === "" ? "—" : this.money(row[key], row.currency_code) : this.esc(this.translateField(key, row[key]))}</td>`).join("")}</tr>`).join("") || `<tr><td colspan="${columns.length}" class="adr-empty">${this.selected ? __("当前快照没有这类明细") : __("暂无快照，请先抓取并生成快照")}</td></tr>`}</tbody></table></div>`);
	}

	rows(raw) {
		try {
			const value = typeof raw === "string" ? JSON.parse(raw) : raw;
			return Array.isArray(value) ? value : Array.isArray(value?.rows) ? value.rows : [];
		} catch {
			return [];
		}
	}

	uniqueTransactions(rows) {
		const seen = new Set();
		return rows.filter((row) => {
			const key = [row.store || row.amazon_store || "", row.marketplace_id || "", row.transaction_id || row.name || ""].join("|");
			if (seen.has(key)) return false;
			seen.add(key);
			return true;
		});
	}

	translateAccount(value) {
		return ({ STANDARD_ORDERS: __("标准订单账户"), INVOICED_ORDERS: __("开票订单账户"), AMEX: __("美国运通账户"), BOLETO: __("Boleto账户"), DINERS_CLUB: __("大来卡账户"), ELO: __("Elo账户"), MASTERCARD_CREDIT: __("万事达信用卡账户"), MASTERCARD_DEBIT: __("万事达借记卡账户"), VISA_CREDIT: __("Visa信用卡账户"), VISA_DEBIT: __("Visa借记卡账户") })[value] || value || __("未分类账户");
	}

	translateField(key, value) {
		if (key === "account_type") return this.translateAccount(value);
		if (value === null || value === undefined || value === "") return "—";
		if (key === "transaction_type") return ({
			Shipment: __("发货交易"), Refund: __("退款交易"), ServiceFee: __("服务费"),
			Adjustment: __("调整交易"), DebtRecovery: __("欠款回收"),
			FBAInventoryReimbursement: __("FBA库存赔偿"), ProductAdsPayment: __("广告付款"),
			RemovalShipment: __("移除货件"), Retrocharge: __("追溯扣款"), Transfer: __("资金划转"),
			ShipmentEvent: __("发货交易"), RefundEvent: __("退款交易"), ServiceFeeEvent: __("服务费"),
			AdjustmentEvent: __("调整交易"), ChargebackEvent: __("拒付交易"),
		})[value] || __(value);
		if (key === "transaction_status") return ({
			DEFERRED: __("已延迟"), DEFERRED_RELEASED: __("延迟后已释放"), RELEASED: __("已释放"),
			PENDING: __("待处理"), CANCELLED: __("已取消"),
		})[value] || __(value);
		if (key === "order_status") return ({
			PENDING_AVAILABILITY: __("待上市"), PENDING: __("待处理"), UNSHIPPED: __("未发货"),
			PARTIALLY_SHIPPED: __("部分发货"), SHIPPED: __("已发货"), CANCELLED: __("已取消"),
			UNFULFILLABLE: __("无法履约"),
		})[value] || __(value);
		return value;
	}

	number(value) { return Number(value || 0); }
	money(value, currency) { return `${this.esc(currency || "")} ${this.number(value).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`.trim(); }
	date(value) { return value ? String(value).replace("T", " ").replace(/\.\d+$/, "").slice(0, 19) : ""; }
	age(value) {
		if (!value) return "—";
		const date = new Date(String(value).replace(" ", "T"));
		const minutes = Math.floor((Date.now() - date.getTime()) / 60000);
		if (!Number.isFinite(minutes) || minutes < 0) return "—";
		if (minutes < 1) return __("不到 1 分钟");
		if (minutes < 60) return `${minutes} ${__("分钟前")}`;
		const hours = Math.floor(minutes / 60);
		return hours < 24 ? `${hours} ${__("小时前")}` : `${Math.floor(hours / 24)} ${__("天前")}`;
	}
	esc(value) { return frappe.utils.escape_html(String(value ?? "")); }
}
