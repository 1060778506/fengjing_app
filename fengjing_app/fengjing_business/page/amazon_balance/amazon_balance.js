frappe.pages["amazon-balance"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("亚马逊余额中心"),
		single_column: true,
	});
	wrapper.amazonBalancePage = new AmazonBalancePage(page);
};

frappe.pages["amazon-balance"].on_page_show = function (wrapper) {
	wrapper.amazonBalancePage?.load();
};

class AmazonBalancePage {
	constructor(page) {
		this.page = page;
		this.pageNo = 1;
		this.pageSize = 50;
		this.loading = false;
		this.charts = [];
		this.renderShell();
		this.bindEvents();
		this.load();
	}

	renderShell() {
		$(this.page.main).html(`
			<div class="ab-root">
				<section class="ab-hero">
					<div><span>AMAZON BALANCE INTELLIGENCE</span><h1>${__("亚马逊余额中心")}</h1><p>${__("当前余额、可用余额、预留余额与延期余额，按国家、账户和币种独立展示。")}</p></div>
					<div><a class="btn ab-config" href="/app/amazon_configuration">${__("余额配置")}</a><button class="btn btn-primary ab-refresh">${__("刷新数据")}</button></div>
				</section>
				<section class="ab-filter-panel">
					<div class="ab-ranges">${[7, 30, 90, 180, 365].map((days) => `<button data-days="${days}" class="ab-range ${days === 30 ? "active" : ""}">${days === 365 ? __("1年") : __("{0}天", [days])}</button>`).join("")}<button data-days="all" class="ab-range">${__("全部")}</button></div>
					<div class="ab-filters">
						${this.input("date_from", __("开始日期"), "date")}${this.input("date_to", __("结束日期"), "date")}
						${this.select("country", __("国家"))}${this.select("amazon_store", __("店铺"))}${this.select("currency_code", __("币种"))}${this.select("account_type", __("账户类型"))}
					</div>
				</section>
				<div class="ab-loading"><i></i>${__("正在分析余额快照…")}</div>
				<main class="ab-content">
					<section class="ab-overview-head"><div><h2>${__("最新余额")}</h2><span class="ab-latest-date">—</span></div><div><small>${__("上一快照")}</small><b class="ab-previous-date">—</b></div></section>
					<section class="ab-currencies"></section>
					<section class="ab-grid ab-grid-trend">
						<article class="ab-card"><header><div><h2>${__("余额趋势")}</h2><span>${__("不同币种分别显示，不进行跨币种相加")}</span></div></header><div id="ab-trend" class="ab-chart"></div></article>
						<article class="ab-card"><header><div><h2>${__("国家余额")}</h2><span>${__("最新快照的国家与站点余额")}</span></div></header><div class="ab-country-list"></div></article>
					</section>
					<section class="ab-card ab-account-card"><header><div><h2>${__("账户余额构成")}</h2><span>${__("同一国家可能返回多个互相独立的账户类型")}</span></div></header><div class="ab-table-wrap"><table><thead><tr><th>${__("国家 / 店铺")}</th><th>${__("账户类型")}</th><th>${__("币种")}</th><th>${__("总余额")}</th><th>${__("可用")}</th><th>${__("预留")}</th><th>${__("延期")}</th><th>${__("账户级预留")}</th><th>${__("亚马逊更新时间")}</th></tr></thead><tbody data-role="accounts"></tbody></table></div></section>
					<section class="ab-card ab-detail-card"><header><div><h2>${__("余额快照明细")}</h2><span class="ab-detail-count"></span></div><div class="ab-pages"></div></header><div class="ab-table-wrap"><table><thead><tr><th>${__("快照日期")}</th><th>${__("国家 / 店铺")}</th><th>${__("账户类型")}</th><th>${__("币种")}</th><th>${__("总余额")}</th><th>${__("可用")}</th><th>${__("预留")}</th><th>${__("延期")}</th><th>${__("账户级预留")}</th><th>${__("同步方式")}</th><th>${__("记录")}</th></tr></thead><tbody data-role="details"></tbody></table></div></section>
				</main>
			</div>`);
		this.$ = $(this.page.main).find(".ab-root");
	}

	input(name, label, type) {
		return `<label>${this.esc(label)}<input type="${type}" data-filter="${name}"></label>`;
	}

	select(name, label) {
		return `<label>${this.esc(label)}<select data-filter="${name}"><option value="">${__("全部")}</option></select></label>`;
	}

	bindEvents() {
		this.$.on("click", ".ab-refresh", () => this.load());
		this.$.on("click", ".ab-range", (event) => {
			const value = event.currentTarget.dataset.days;
			const latest = this.data?.limits?.latest_date || frappe.datetime.get_today();
			this.$.find(".ab-range").removeClass("active");
			$(event.currentTarget).addClass("active");
			this.$.find("[data-filter=date_to]").val(latest);
			this.$.find("[data-filter=date_from]").val(value === "all" ? (this.data?.limits?.earliest_date || "") : frappe.datetime.add_days(latest, -Number(value) + 1));
			this.pageNo = 1;
			this.load();
		});
		this.$.on("change", "[data-filter]", () => {
			this.$.find(".ab-range").removeClass("active");
			this.pageNo = 1;
			this.load();
		});
		this.$.on("click", "[data-page]", (event) => {
			this.pageNo = Number(event.currentTarget.dataset.page || 1);
			this.load();
		});
		this.$.on("click", "[data-copy]", (event) => {
			frappe.utils.copy_to_clipboard(String($(event.currentTarget).data("copy") || ""));
			frappe.show_alert({ message: __("已复制"), indicator: "green" });
		});
		$(window).off("resize.amazon-balance").on("resize.amazon-balance", () => this.charts.forEach((chart) => chart?.resize()));
	}

	filters() {
		const result = {};
		this.$.find("[data-filter]").each((_, element) => {
			if (element.value) result[element.dataset.filter] = element.value;
		});
		return result;
	}

	async load() {
		if (this.loading) return;
		this.loading = true;
		this.$.addClass("is-loading");
		try {
			await this.loadEcharts();
			const response = await frappe.call({
				method: "fengjing_app.fengjing_business.page.amazon_balance.amazon_balance.get_balance_dashboard_data",
				args: { filters: this.filters(), page: this.pageNo, page_size: this.pageSize },
			});
			this.data = response.message || {};
			this.render();
		} catch (error) {
			console.error(error);
			frappe.msgprint({ title: __("余额页面加载失败"), message: error?.message || __("请检查后台日志"), indicator: "red" });
		} finally {
			this.loading = false;
			this.$.removeClass("is-loading");
		}
	}

	render() {
		this.applyDates();
		this.fillOptions();
		this.renderCurrencies();
		this.renderTrend();
		this.renderCountries();
		this.renderAccounts();
		this.renderDetails();
	}

	applyDates() {
		const filters = this.data.filters || {};
		if (!this.$.find("[data-filter=date_from]").val()) this.$.find("[data-filter=date_from]").val(filters.date_from || "");
		if (!this.$.find("[data-filter=date_to]").val()) this.$.find("[data-filter=date_to]").val(filters.date_to || "");
		this.$.find(".ab-latest-date").text(`${__("快照日期")}：${this.data.latest_date || "—"}`);
		this.$.find(".ab-previous-date").text(this.data.previous_date || "—");
	}

	fillOptions() {
		["country", "amazon_store", "currency_code", "account_type"].forEach((key) => {
			const element = this.$.find(`[data-filter=${key}]`);
			const current = element.val();
			const options = (this.data.options?.[key] || []).map((value) => `<option value="${this.esc(value)}">${this.esc(this.translate(value))}</option>`).join("");
			element.html(`<option value="">${__("全部")}</option>${options}`).val(current || "");
		});
	}

	renderCurrencies() {
		const rows = this.data.currencies || [];
		this.$.find(".ab-currencies").html(rows.map((row) => {
			const change = row.change_amount;
			const changeClass = change == null ? "neutral" : Number(change) >= 0 ? "positive" : "negative";
			const changeText = change == null ? __("无上一快照") : `${Number(change) >= 0 ? "+" : ""}${this.money(change, row.currency_code)}`;
			return `<article class="ab-currency">
				<header><span>${this.flag(row.currency_code)}</span><div><b>${this.esc(row.currency_code || __("未知币种"))}</b><small>${this.num(row.account_count)} ${__("个账户")}</small></div><em class="${changeClass}">${changeText}</em></header>
				<strong>${this.money(row.total_balance, row.currency_code)}</strong>
				<div><span>${__("可用")}<b>${this.money(row.available_balance, row.currency_code)}</b></span><span>${__("预留")}<b>${this.money(row.reserved_balance, row.currency_code)}</b></span><span>${__("延期")}<b>${this.money(row.deferred_balance, row.currency_code)}</b></span><span>${__("账户级预留")}<b>${this.money(row.account_level_reserve, row.currency_code)}</b></span></div>
			</article>`;
		}).join("") || `<div class="ab-empty">${__("当前筛选范围没有余额快照")}</div>`);
	}

	renderTrend() {
		this.charts.forEach((chart) => chart?.dispose());
		this.charts = [];
		const rows = this.data.trend || [];
		const dates = [...new Set(rows.map((row) => String(row.as_of_date)))];
		const currencies = [...new Set(rows.map((row) => row.currency_code || __("未知")))];
		const lookup = new Map(rows.map((row) => [`${row.as_of_date}|${row.currency_code || __("未知")}`, row]));
		const series = [];
		currencies.forEach((currency) => {
			series.push({
				name: `${currency} ${__("总余额")}`,
				type: "line",
				smooth: true,
				showSymbol: dates.length < 15,
				data: dates.map((date) => lookup.get(`${date}|${currency}`)?.total_balance ?? null),
				lineStyle: { width: 3 },
				areaStyle: { opacity: 0.06 },
			});
		});
		if (currencies.length === 1) {
			const currency = currencies[0];
			[["available_balance", __("可用余额"), "#13a37b"], ["reserved_balance", __("预留余额"), "#f29b38"], ["deferred_balance", __("延期余额"), "#8a67dc"]].forEach(([field, label, color]) => {
				series.push({ name: `${currency} ${label}`, type: "line", smooth: true, showSymbol: false, data: dates.map((date) => lookup.get(`${date}|${currency}`)?.[field] ?? null), lineStyle: { width: 2, color } });
			});
		}
		const element = this.$.find("#ab-trend")[0];
		if (!element || typeof echarts === "undefined") return;
		const chart = echarts.init(element);
		chart.setOption({
			tooltip: { trigger: "axis", appendToBody: true },
			legend: { top: 5, type: "scroll" },
			grid: { left: 72, right: 25, top: 52, bottom: 52 },
			xAxis: { type: "category", data: dates },
			yAxis: { type: "value", scale: true },
			dataZoom: [{ type: "inside" }, { type: "slider", height: 17, bottom: 8 }],
			series,
		});
		this.charts.push(chart);
	}

	renderCountries() {
		const rows = this.data.countries || [];
		this.$.find(".ab-country-list").html(rows.map((row) => `<div class="ab-country">
			<span>${this.flag(row.currency_code)}</span><div><b>${this.esc(this.translate(row.country || __("未知国家")))}</b><small>${this.esc(row.amazon_store || "—")} · ${this.esc(row.currency_code || "—")}</small></div>
			<strong>${this.money(row.total_balance, row.currency_code)}</strong><em>${__("可用")} ${this.money(row.available_balance, row.currency_code)} · ${__("预留")} ${this.money(row.reserved_balance, row.currency_code)}</em>
		</div>`).join("") || `<div class="ab-empty">${__("暂无国家余额")}</div>`);
	}

	renderAccounts() {
		const rows = this.data.accounts || [];
		this.$.find("[data-role=accounts]").html(rows.map((row) => `<tr>
			<td><b>${this.esc(this.translate(row.country || "—"))}</b><small>${this.esc(row.amazon_store || "—")}</small></td>
			<td><b>${this.esc(this.translateAccount(row.account_type))}</b><small>${this.esc(row.account_type || "—")}</small></td><td>${this.esc(row.currency_code || "—")}</td>
			<td class="num">${this.money(row.total_balance, row.currency_code)}</td><td class="num">${this.money(row.available_balance, row.currency_code)}</td><td class="num">${this.money(row.reserved_balance, row.currency_code)}</td><td class="num">${this.money(row.deferred_balance, row.currency_code)}</td><td class="num">${this.money(row.account_level_reserve, row.currency_code)}</td><td>${this.dateTime(row.last_updated_time)}</td>
		</tr>`).join("") || `<tr><td colspan="9" class="ab-empty">${__("暂无账户余额")}</td></tr>`);
	}

	renderDetails() {
		const page = this.data.pagination || {};
		this.$.find(".ab-detail-count").text(__("共 {0} 条 · 第 {1}/{2} 页", [this.num(page.total), page.page || 1, page.pages || 1]));
		this.$.find(".ab-pages").html(`<button class="btn btn-xs" data-page="${Number(page.page || 1) - 1}" ${Number(page.page || 1) <= 1 ? "disabled" : ""}>${__("上一页")}</button><button class="btn btn-xs" data-page="${Number(page.page || 1) + 1}" ${Number(page.page || 1) >= Number(page.pages || 1) ? "disabled" : ""}>${__("下一页")}</button>`);
		this.$.find("[data-role=details]").html((this.data.details || []).map((row) => `<tr>
			<td><b>${this.esc(String(row.as_of_date || "—"))}</b><small>${this.dateTime(row.fetched_at)}</small></td><td><b>${this.esc(this.translate(row.country || "—"))}</b><small>${this.esc(row.amazon_store || "—")}</small></td><td><b>${this.esc(this.translateAccount(row.account_type))}</b><small>${this.esc(row.account_type || "—")}</small></td><td>${this.esc(row.currency_code || "—")}</td>
			<td class="num">${this.money(row.total_balance, row.currency_code)}</td><td class="num">${this.money(row.available_balance, row.currency_code)}</td><td class="num">${this.money(row.reserved_balance, row.currency_code)}</td><td class="num">${this.money(row.deferred_balance, row.currency_code)}</td><td class="num">${this.money(row.account_level_reserve, row.currency_code)}</td><td>${this.esc(this.translateSync(row.sync_type))}</td><td><a href="/app/amazon-balance-snapshot/${encodeURIComponent(row.name)}">${__("打开")}</a><button data-copy="${this.esc(row.snapshot_key || "")}">${__("复制键值")}</button></td>
		</tr>`).join("") || `<tr><td colspan="11" class="ab-empty">${__("暂无余额快照")}</td></tr>`);
	}

	loadEcharts() {
		if (!this.echartsPromise) this.echartsPromise = new Promise((resolve, reject) => frappe.require("/assets/fengjing_app/js/图表-echarts.js", resolve, reject));
		return this.echartsPromise;
	}

	translate(value) {
		return ({ "United States": __("美国"), Canada: __("加拿大"), Mexico: __("墨西哥"), Brazil: __("巴西") })[value] || value;
	}
	translateAccount(value) {
		return ({ STANDARD_ORDERS: __("标准订单账户"), INVOICED_ORDERS: __("开票订单账户"), AMEX: __("美国运通账户"), BOLETO: __("Boleto账户"), DINERS_CLUB: __("大来卡账户"), ELO: __("Elo账户"), MASTERCARD_CREDIT: __("万事达信用卡账户"), MASTERCARD_DEBIT: __("万事达借记卡账户"), VISA_CREDIT: __("Visa信用卡账户"), VISA_DEBIT: __("Visa借记卡账户") })[value] || value || __("未分类账户");
	}
	translateSync(value) { return ({ history: __("历史余额"), current: __("当前余额"), routine: __("日常余额"), recheck: __("近期复核") })[value] || value || "—"; }
	money(value, currency) { return `${currency || ""} ${Number(value || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`; }
	num(value) { return Number(value || 0).toLocaleString("zh-CN"); }
	dateTime(value) { return value ? String(value).replace("T", " ").replace(/\.\d+$/, "").slice(0, 19) : "—"; }
	flag(currency) { return ({ USD: "🇺🇸", CAD: "🇨🇦", MXN: "🇲🇽", BRL: "🇧🇷", EUR: "🇪🇺", GBP: "🇬🇧", JPY: "🇯🇵", CNY: "🇨🇳" })[currency] || "💱"; }
	esc(value) { return frappe.utils.escape_html(String(value ?? "")); }
}
