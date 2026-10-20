const FENGJING_CALENDAR_METHOD =
	"fengjing_app.fengjing_business.page.fengjin_team_calenda.fengjin_team_calenda";
const FENGJING_CALENDAR_MAP_FILES = {
	countries: "/assets/fengjing_app/data/Vector_map/国家边界ne_10m_admin_0_countries_chn/ne_10m_admin_0_countries_chn.geojson",
	regions: "/assets/fengjing_app/data/Vector_map/省州边界ne_10m_admin_1_states_provinces/countries",
	cities: "/assets/fengjing_app/data/Vector_map/城市位置ne_10m_populated_places/ne_10m_populated_places.geojson",
	taiwanProvince: "/assets/fengjing_app/data/Vector_map/国家边界ne_10m_admin_0_countries_chn/taiwan_province.geojson",
};
const FENGJING_CALENDAR_REGION_ALIASES = {
	"CN-NM": "Nei Mongol",
	"CN-NX": "Ningxia Hui",
	"CN-QH": "Qinghai",
	"CN-XJ": "Xinjiang Uygur",
	"CN-XZ": "Xizang",
};
const FENGJING_CALENDAR_CITY_REGION_OVERRIDES = {
	"1159150819": "CN-QH", // Natural Earth mistakenly lists Xining under Gansu.
};
const FENGJING_CALENDAR_EXTRA_WEATHER_CITIES = {
	"CN-SD": ["1159149919"], // 山东省：临沂；济南仍由省会规则加入。
};
const FENGJING_CALENDAR_CHINA_SPECIAL_REGIONS = {
	HKG: { key: "CN-HK", name: "香港特别行政区", name_en: "Hong Kong" },
	MAC: { key: "CN-MO", name: "澳门特别行政区", name_en: "Macau" },
};
const FENGJING_US_HOLIDAY_NAMES = Object.freeze({
	"New Year's Day": "元旦",
	"Martin Luther King, Jr. Day": "马丁·路德·金纪念日",
	"Martin Luther King Jr. Day": "马丁·路德·金纪念日",
	"Presidents Day": "总统日",
	"Presidents' Day": "总统日",
	"Washington's Birthday": "总统日",
	"Memorial Day": "阵亡将士纪念日",
	"Juneteenth National Independence Day": "六月节",
	"Independence Day": "独立日",
	"Labour Day": "劳动节",
	"Labor Day": "劳动节",
	"Columbus Day": "哥伦布日",
	"Veterans Day": "退伍军人节",
	"Thanksgiving Day": "感恩节",
	"Christmas Day": "圣诞节",
});

function fengjingCountryFlag(code, countryName) {
	const iso = String(code || "").toLowerCase();
	if (!/^[a-z]{2}$/.test(iso)) return null;
	const flag = document.createElement("img");
	flag.className = "fjtc-country-flag";
	flag.src = `/assets/fengjing_app/flags/4x3/${iso}.svg`;
	flag.alt = `${countryName || iso.toUpperCase()}国旗`;
	flag.width = 18;
	flag.height = 14;
	return flag;
}

function fengjingWeatherIcon(code, isDay = true) {
	if (code == null || code === "") return "🌡️";
	const value = Number(code);
	if (value === 0) return isDay ? "☀️" : "🌙";
	if ([1, 2].includes(value)) return isDay ? "🌤️" : "☁️";
	if (value === 3) return "☁️";
	if ([45, 48].includes(value)) return "🌫️";
	if ([71, 73, 75, 77, 85, 86].includes(value)) return "❄️";
	if ([95, 96, 99].includes(value)) return "⛈️";
	if ([51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82].includes(value)) return "🌧️";
	return "🌡️";
}

frappe.pages["fengjin_team_calenda"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "丰境日历",
		single_column: true,
	});
	wrapper.fengjing_team_calendar = new FengjingTeamCalendarPage(wrapper, page);
};

frappe.pages["fengjin_team_calenda"].on_page_show = function (wrapper) {
	wrapper.fengjing_team_calendar?.show();
};

frappe.pages["fengjin_team_calenda"].on_page_hide = function (wrapper) {
	wrapper.fengjing_team_calendar?.hide();
};

class FengjingTeamCalendarPage {
	constructor(wrapper, page) {
		this.wrapper = wrapper;
		this.page = page;
		this.calendar = null;
		this.loadingPromise = null;
		this.viewMode = "calendar";
		this.mapDataPromises = {};
		this.countryLookup = new Map();
		this.regionLookup = new Map();
		this.cityLookup = new Map();
		this.holidayCountries = new Map();
		this.weatherCities = new Map();
		this.timeCities = new Map();
		this.defaultHolidayCountry = null;
		this.defaultWeatherCity = null;
		this.defaultTimeCity = null;
		this.qweatherCredentialId = "";
		this.qweatherApiKey = "";
		this.qweatherApiHost = "";
		this.weatherViewActive = false;
		this.weatherViewCity = null;
		this.clockTimer = null;
		this.clockFormatters = new Map();
		this.pageVisible = false;
		this.mapSettingsLoaded = false;
		this.mapSettingsLoading = null;
		this.mapSettingsSaveQueue = Promise.resolve();
		this.holidayRequestToken = 0;
		this.holidayPollTimer = null;
		this.weatherRequestToken = 0;
		this.weatherByCity = new Map();
		this.weatherLoaded = false;
		this.weatherPollTimer = null;
		this.activeCountry = null;
		this.selectedRegion = null;
		this.selectedCity = null;
		this.countryLoadToken = 0;
		this.countryMapLoaded = false;
		this.searchQuery = "";
		this.plottedCities = [];
		this.regionOnlyMarkers = [];
		this.cityRegionKeys = new Map();
		this.renderShell();
		$(this.wrapper).on("hide.fjtc", () => this.hide());
		if (window.ResizeObserver) {
			this.mapResizeObserver = new ResizeObserver(() => {
				if (this.viewMode === "settings") this.mapChart?.resize();
			});
			this.mapResizeObserver.observe(this.mapContainer);
		}
		this.addActions();
	}

	renderShell() {
		$(this.wrapper).addClass("fjtc-page");
		this.$main = $(this.page.main);
		this.$main.html(`
			<div class="fjtc-shell">
				<div class="fjtc-loading">
					<span class="fjtc-spinner"></span>
					<span>正在加载丰境日历…</span>
				</div>
				<div class="fjtc-calendar" aria-label="丰境日历"></div>
				<div class="fjtc-windy-overlay" role="dialog" aria-modal="true" aria-label="Windy 风场地图" hidden>
					<button type="button" class="fjtc-windy-close" aria-label="关闭放大的风场地图" title="关闭">×</button>
					<iframe class="fjtc-windy-expanded-map" title="Windy 风场地图" frameborder="0"></iframe>
				</div>
				<div class="fjtc-settings" aria-label="地区设置" hidden>
					<div class="fjtc-settings-head">
						<h2>地区设置</h2>
						<div class="fjtc-settings-actions">
							<span class="fjtc-map-breadcrumb">世界</span>
							<button type="button" class="btn btn-default btn-sm" data-map-action="world">世界地图</button>
							<button type="button" class="btn btn-default btn-sm" data-map-action="calendar">返回日历</button>
						</div>
					</div>
					<div class="fjtc-settings-layout">
						<div class="fjtc-map-column">
							<div class="fjtc-map-toolbar">
								<div class="fjtc-map-selections"></div>
							</div>
							<div class="fjtc-map-stage">
								<div class="fjtc-map-canvas" role="img" aria-label="离线矢量地图"></div>
								<div class="fjtc-map-status">正在加载地图…</div>
							</div>
						</div>
						<aside class="fjtc-settings-panel"></aside>
					</div>
				</div>
			</div>
		`);
		this.container = this.$main.find(".fjtc-calendar")[0];
		this.mapContainer = this.$main.find(".fjtc-map-canvas")[0];
		this.$main.on("click", "[data-map-action='calendar']", () => this.returnToCalendar());
		this.$main.on("click", ".fjtc-windy-expand", (event) => this.openWindyMap(event.currentTarget));
		this.$main.on("click", ".fjtc-windy-close", () => this.closeWindyMap());
		this.$main.on("keydown", ".fjtc-windy-overlay", (event) => {
			if (event.key === "Escape") this.closeWindyMap();
		});
		this.$main.on("click", "[data-map-action='world']", () =>
			this.renderWorldMap().catch((error) => this.showError("地图加载失败", error)));
		this.$main.on("click", ".fjtc-city-result[data-city-key]", (event) =>
			this.selectCity($(event.currentTarget).attr("data-city-key"), true));
		this.$main.on("click", "[data-region-key]", (event) => this.selectRegion($(event.currentTarget).attr("data-region-key"), true));
		this.$main.on("click", "[data-map-remove-holiday]", (event) => {
			if (this.mapSelectionClickSuppressedUntil > Date.now()) return;
			const key = $(event.currentTarget).attr("data-map-remove-holiday");
			this.holidayCountries.delete(key);
			if (this.defaultHolidayCountry === key) {
				this.defaultHolidayCountry = this.holidayCountries.keys().next().value || null;
			}
			this.renderSettingsPanel();
			this.scheduleMapSettingsSave();
		});
		this.$main.on("click", "[data-map-remove-weather]", (event) => {
			if (this.mapSelectionClickSuppressedUntil > Date.now()) return;
			const key = $(event.currentTarget).attr("data-map-remove-weather");
			this.weatherCities.delete(key);
			if (this.defaultWeatherCity === key) {
				this.defaultWeatherCity = this.weatherCities.keys().next().value || null;
			}
			if (this.weatherViewCity === key) {
				this.weatherViewCity = this.defaultWeatherCity;
				this.weatherViewActive = Boolean(this.weatherViewCity);
				this.container.classList.toggle("fjtc-weather-mode", this.weatherViewActive);
			}
			this.renderSettingsPanel();
			this.scheduleMapSettingsSave();
		});
		this.$main.on("click", "[data-map-remove-time]", (event) => {
			if (this.mapSelectionClickSuppressedUntil > Date.now()) return;
			const key = $(event.currentTarget).attr("data-map-remove-time");
			this.timeCities.delete(key);
			if (this.defaultTimeCity === key) {
				this.defaultTimeCity = this.timeCities.keys().next().value || null;
			}
			this.renderSettingsPanel();
			this.scheduleMapSettingsSave();
		});
		this.$main.on("click", "[data-map-default-holiday]", (event) => {
			if (this.mapSelectionClickSuppressedUntil > Date.now()) return;
			const key = $(event.currentTarget).attr("data-map-default-holiday");
			if (!this.holidayCountries.has(key) || this.defaultHolidayCountry === key) return;
			this.defaultHolidayCountry = key;
			this.renderMapSelections();
			this.scheduleMapSettingsSave();
		});
		this.$main.on("click", "[data-map-default-weather]", (event) => {
			if (this.mapSelectionClickSuppressedUntil > Date.now()) return;
			const key = $(event.currentTarget).attr("data-map-default-weather");
			if (!this.weatherCities.has(key) || this.defaultWeatherCity === key) return;
			this.defaultWeatherCity = key;
			this.weatherViewCity = key;
			this.weatherViewActive = true;
			this.container.classList.add("fjtc-weather-mode");
			this.renderMapSelections();
			this.scheduleMapSettingsSave();
		});
		this.$main.on("click", "[data-map-default-time]", (event) => {
			if (this.mapSelectionClickSuppressedUntil > Date.now()) return;
			const key = $(event.currentTarget).attr("data-map-default-time");
			if (!this.timeCities.has(key) || this.defaultTimeCity === key) return;
			this.defaultTimeCity = key;
			this.renderMapSelections();
			this.scheduleMapSettingsSave();
		});
		this.$main.on("pointerdown", ".fjtc-map-selection-chip[data-map-order-kind]", (event) =>
			this.startMapSelectionPointer(event));
		this.$main.on("change", "[data-map-setting]", (event) => this.changeMapSetting(event));
		this.$main.on("change", "[data-weather-credential]", (event) => {
			const field = event.currentTarget.dataset.weatherCredential;
			if (field === "qweather_credential_id") this.qweatherCredentialId = event.currentTarget.value;
			if (field === "qweather_api_key") this.qweatherApiKey = event.currentTarget.value;
			if (field === "qweather_api_host") this.qweatherApiHost = event.currentTarget.value;
			this.scheduleMapSettingsSave();
		});
		this.$main.on("click", '[data-weather-credential="qweather_credential_id"], [data-weather-credential="qweather_api_key"]', (event) => {
			const credential = this.$main.find('[data-weather-credential="qweather_credential_id"]').val() || "";
			const apiKey = this.$main.find('[data-weather-credential="qweather_api_key"]').val() || "";
			if (!event.currentTarget.value.trim() && !credential.trim() && !apiKey.trim()) {
				window.open("https://console.qweather.com/", "_blank", "noopener,noreferrer");
			}
		});
		this.$main.on("click", ".fjtc-weather-view-toggle", (event) => {
			const key = event.currentTarget.dataset.cityKey;
			this.weatherViewActive = this.weatherViewCity === key ? !this.weatherViewActive : true;
			this.weatherViewCity = key;
			if (this.weatherViewActive && this.calendar?.view.type !== "dayGridMonth") this.calendar.changeView("dayGridMonth");
			this.container.classList.toggle("fjtc-weather-mode", this.weatherViewActive);
			this.renderWeatherPreview();
			this.renderDailyWeather();
		});
		this.$main.on("input", ".fjtc-city-search", (event) => this.renderCityResults(event.currentTarget.value));
		this.renderSettingsPanel();
	}

	addActions() {
		this.page.set_primary_action("新建日程", () => this.openEventDialog(), "add");
		this.page.add_inner_button("刷新", () => {
			this.calendar?.refetchEvents();
			this.refreshVisibleHolidays();
			this.loadWeather(true);
		});
		this.page.add_inner_button("日程列表", () => frappe.set_route("List", "Event"));
		this.page.add_inner_button("设置", () => this.openSettings());
	}

	async show() {
		this.pageVisible = true;
		document.body.classList.add("fjtc-full-height-page");
		if (this.viewMode === "settings") {
			this.mapChart?.resize();
			return;
		}
		try {
			await this.ensureCalendar();
			if (this.viewMode !== "calendar") return;
			// FullCalendar 7 no longer exposes updateSize(). Re-render after the
			// Frappe page becomes visible so it can measure the real container.
			this.calendar.render();
			this.calendar.refetchEvents();
			this.refreshVisibleHolidays();
			try {
				await this.loadMapSettings();
				await this.ensureCityTimezones();
			} catch (error) {
				this.showError("城市时间加载失败", error);
			}
			if (this.pageVisible && this.viewMode === "calendar") {
				this.renderCityClocks();
				this.renderWeatherPreview();
				this.loadWeather(true);
				this.startCityClock();
			}
		} catch (error) {
			this.showError("日历加载失败", error);
		}
	}

	hide() {
		this.pageVisible = false;
		this.closeWindyMap();
		document.body.classList.remove("fjtc-full-height-page");
		this.cancelMapSelectionPointer();
		this.stopCityClock();
		if (this.weatherPollTimer) window.clearTimeout(this.weatherPollTimer);
		this.weatherPollTimer = null;
		if (this.holidayPollTimer) window.clearTimeout(this.holidayPollTimer);
		this.holidayPollTimer = null;
		this.holidayRequestToken += 1;
	}

	ensureCalendar() {
		if (this.calendar) return Promise.resolve(this.calendar);
		if (this.loadingPromise) return this.loadingPromise;
		this.loadingPromise = new Promise((resolve, reject) => {
			frappe.require([
				"/assets/fengjing_app/calendar/fullcalendar/fengjing_calendar.css",
				"/assets/fengjing_app/calendar/fullcalendar/fengjing_calendar.js",
			], () => {
				try {
					const library = window.FengjingFullCalendar;
					if (!library?.Calendar) throw new Error("未找到 FullCalendar 构建资源。");
					this.calendar = this.createCalendar(library);
					this.$main.find(".fjtc-loading").hide();
					if (this.viewMode === "calendar") {
						this.$main.find(".fjtc-calendar").show();
						this.calendar.render();
					}
					resolve(this.calendar);
				} catch (error) {
					reject(error);
				}
			});
		}).finally(() => {
			this.loadingPromise = null;
		});
		return this.loadingPromise;
	}

	async openSettings() {
		this.closeWindyMap();
		this.viewMode = "settings";
		this.stopCityClock();
		if (this.holidayPollTimer) window.clearTimeout(this.holidayPollTimer);
		this.holidayPollTimer = null;
		this.holidayRequestToken += 1;
		this.$main.find(".fjtc-shell").addClass("fjtc-shell--settings");
		this.$main.find(".fjtc-calendar, .fjtc-loading").hide();
		this.container.style.setProperty("display", "none", "important");
		this.$main.find(".fjtc-settings").prop("hidden", false);
		this.setMapStatus("正在加载离线地图…");
		try {
			await this.loadMapSettings();
		} catch (error) {
			this.setMapStatus("地区设置加载失败，请刷新页面重试。");
			this.showError("地区设置加载失败", error);
			return;
		}
		try {
			await this.renderWorldMap();
		} catch (error) {
			this.setMapStatus("地图加载失败，请检查地图文件是否已发布到 /assets/fengjing_app/data/Vector_map。");
			this.showError("地图加载失败", error);
		}
	}

	loadMapSettings() {
		if (this.mapSettingsLoaded) return Promise.resolve();
		if (this.mapSettingsLoading) return this.mapSettingsLoading;
		this.mapSettingsLoading = Promise.resolve(frappe.call({
			method: `${FENGJING_CALENDAR_METHOD}.get_calendar_map_settings`,
		})).then(({ message }) => {
			const settings = message || {};
			this.holidayCountries = new Map((settings.holiday_countries || [])
				.map(({ key, name }) => [String(key), String(name)]));
			this.weatherCities = new Map((settings.weather_cities || [])
				.map(({ key, country, region, city }) => [String(key), { country, region, city }]));
			this.timeCities = new Map((settings.time_cities || [])
				.map(({ key, country, region, city, time_zone, country_code }) =>
					[String(key), { country, region, city, timeZone: time_zone || "", countryCode: country_code || "" }]));
			this.defaultHolidayCountry = settings.default_holiday_country ||
				this.holidayCountries.keys().next().value || null;
			this.defaultWeatherCity = settings.default_weather_city ||
				this.weatherCities.keys().next().value || null;
			this.weatherViewCity = this.defaultWeatherCity;
			this.weatherViewActive = Boolean(this.weatherViewCity);
			this.container.classList.toggle("fjtc-weather-mode", this.weatherViewActive);
			this.defaultTimeCity = settings.default_time_city ||
				this.timeCities.keys().next().value || null;
			this.qweatherCredentialId = settings.qweather_credential_id || "";
			this.qweatherApiKey = settings.qweather_api_key || "";
			this.qweatherApiHost = settings.qweather_api_host || "";
			this.mapSettingsLoaded = true;
			this.renderSettingsPanel();
			this.renderWeatherPreview();
		}).finally(() => {
			this.mapSettingsLoading = null;
		});
		return this.mapSettingsLoading;
	}

	async ensureCityTimezones() {
		const missing = [...this.timeCities].filter(([, location]) => !location.timeZone);
		if (!missing.length) return;
		const data = await this.loadMapData("cities");
		const zones = new Map((data.features || []).map(({ properties }) =>
			[String(properties.NE_ID), properties.TIMEZONE || ""]));
		for (const [key, location] of missing) {
			location.timeZone = zones.get(key.split(":", 2)[1]) || "";
		}
	}

	scheduleMapSettingsSave() {
		const values = JSON.stringify({
			holiday_countries: [...this.holidayCountries].map(([key, name]) => ({ key, name })),
			weather_cities: [...this.weatherCities].map(([key, location]) => ({ key, ...location })),
			time_cities: [...this.timeCities].map(([key, location]) => ({ key, ...location })),
			default_holiday_country: this.defaultHolidayCountry,
			default_weather_city: this.defaultWeatherCity,
			default_time_city: this.defaultTimeCity,
			qweather_credential_id: this.qweatherCredentialId,
			qweather_api_key: this.qweatherApiKey,
			qweather_api_host: this.qweatherApiHost,
		});
		// Keep writes in order when several checkboxes change while a request is running.
		this.mapSettingsSaveQueue = this.mapSettingsSaveQueue.catch(() => {}).then(() =>
			frappe.call({
				method: `${FENGJING_CALENDAR_METHOD}.save_calendar_map_settings`,
				args: { values },
			})).catch((error) => this.showError("地区设置自动保存失败，请重新勾选后重试", error));
	}

	async returnToCalendar() {
		this.viewMode = "calendar";
		this.countryLoadToken += 1;
		this.$main.find(".fjtc-shell").removeClass("fjtc-shell--settings");
		this.$main.find(".fjtc-settings").prop("hidden", true);
		this.container.style.removeProperty("display");
		this.$main.find(this.calendar ? ".fjtc-calendar" : ".fjtc-loading").show();
		try {
			await this.ensureCalendar();
			if (this.viewMode !== "calendar") return;
			this.$main.find(".fjtc-calendar").show();
			this.calendar.render();
			this.calendar.refetchEvents();
			this.refreshVisibleHolidays();
			try {
				await this.ensureCityTimezones();
			} catch (error) {
				this.showError("城市时区读取失败", error);
			}
			this.renderCityClocks();
			this.renderWeatherPreview();
			this.loadWeather(true);
			this.startCityClock();
		} catch (error) {
			this.showError("日历加载失败", error);
		}
	}

	loadMapData(kind, countryCode = "") {
		const cacheKey = kind === "regions" ? `regions:${countryCode}` : kind;
		const path = kind === "regions"
			? `${FENGJING_CALENDAR_MAP_FILES.regions}/${countryCode}.geojson`
			: FENGJING_CALENDAR_MAP_FILES[kind];
		if (!this.mapDataPromises[cacheKey]) {
			this.mapDataPromises[cacheKey] = fetch(encodeURI(path), {
				credentials: "same-origin",
			}).then((response) => {
				if (!response.ok) throw new Error(`${kind} 地图文件读取失败（HTTP ${response.status}）`);
				return response.json();
			}).catch((error) => {
				delete this.mapDataPromises[cacheKey];
				throw error;
			});
		}
		return this.mapDataPromises[cacheKey];
	}

	async ensureMap() {
		if (this.mapChart) return this.mapChart;
		if (this.mapLoadingPromise) return this.mapLoadingPromise;
		this.mapLoadingPromise = Promise.all([
			new Promise((resolve) => frappe.require("/assets/fengjing_app/js/图表-echarts.js", resolve)),
			this.loadMapData("countries"),
		]).then(([, countries]) => {
			if (!window.echarts?.registerMap) throw new Error("ECharts 地图组件未加载。");
			const features = countries.features.map((feature) => {
				const key = String(feature.properties.NE_ID);
				this.countryLookup.set(key, feature);
				return { ...feature, properties: { ...feature.properties, name: key } };
			});
			echarts.registerMap("fjtc-world", { type: "FeatureCollection", features });
			this.mapChart = echarts.init(this.mapContainer);
			this.mapChart.on("click", (params) => this.handleMapClick(params));
			return this.mapChart;
		}).finally(() => {
			this.mapLoadingPromise = null;
		});
		return this.mapLoadingPromise;
	}

	async renderWorldMap() {
		await this.loadMapSettings();
		this.countryLoadToken += 1;
		this.activeCountry = null;
		this.selectedRegion = null;
		this.selectedCity = null;
		this.searchQuery = "";
		this.countryMapLoaded = false;
		this.renderSettingsPanel();
		this.$main.find(".fjtc-map-breadcrumb").text("世界");
		this.setMapStatus("正在加载世界地图…");
		const chart = await this.ensureMap();
		if (this.viewMode !== "settings" || this.activeCountry) return;
		chart.setOption({
			animation: false,
			tooltip: {
				trigger: "item",
				formatter: (params) => this.escapeHtml(this.countryLookup.get(String(params.name))?.properties.name || ""),
			},
			geo: {
				map: "fjtc-world",
				roam: true,
				layoutCenter: ["50%", "50%"],
				layoutSize: "95%",
				itemStyle: { areaColor: "#e9eef5", borderColor: "#9aa9ba", borderWidth: 0.5 },
				label: {
					show: true,
					color: "#334155",
					fontSize: 10,
					textBorderColor: "#fff",
					textBorderWidth: 2,
					formatter: (params) => this.countryLookup.get(String(params.name))?.properties.name || "",
				},
				emphasis: { itemStyle: { areaColor: "#c8ddf9" } },
			},
			series: [],
		}, true);
		chart.resize();
		this.setMapStatus("");
	}

	buildRegionSpatialIndex() {
		return [...this.regionLookup].flatMap(([key, region]) => {
			const geometry = region.geometry;
			const polygons = geometry?.type === "Polygon" ? [geometry.coordinates]
				: geometry?.type === "MultiPolygon" ? geometry.coordinates : [];
			return polygons.filter((polygon) => polygon[0]?.length).map((polygon) => {
				const outer = polygon[0];
				let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
				for (const [lon, lat] of outer) {
					minLon = Math.min(minLon, lon);
					maxLon = Math.max(maxLon, lon);
					minLat = Math.min(minLat, lat);
					maxLat = Math.max(maxLat, lat);
				}
				return { key, polygon, minLon, maxLon, minLat, maxLat };
			});
		});
	}

	pointInRing(lon, lat, ring) {
		let inside = false;
		for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
			const [xi, yi] = ring[i];
			const [xj, yj] = ring[j];
			if ((yi > lat) !== (yj > lat) && lon < (xj - xi) * (lat - yi) / (yj - yi) + xi) {
				inside = !inside;
			}
		}
		return inside;
	}

	regionAtPoint(index, lon, lat) {
		for (const entry of index) {
			if (lon < entry.minLon || lon > entry.maxLon || lat < entry.minLat || lat > entry.maxLat) continue;
			if (!this.pointInRing(lon, lat, entry.polygon[0])) continue;
			if (entry.polygon.slice(1).some((ring) => this.pointInRing(lon, lat, ring))) continue;
			return entry.key;
		}
		return null;
	}

	fitCountryMap(index) {
		if (!index.length || !this.mapContainer.clientWidth || !this.mapContainer.clientHeight) return;
		// 阿拉斯加的岛屿跨越 180° 经线；美国初次进入时以本土州为视野中心。
		const mainland = this.activeCountry?.properties.ADM0_A3 === "USA"
			? index.filter((polygon) => !["US-AK", "US-HI"].includes(
				this.regionLookup.get(polygon.key)?.properties.iso_3166_2))
			: index;
		const focus = mainland.length ? mainland : index;
		const bounds = focus.reduce((result, polygon) => ({
			minLon: Math.min(result.minLon, polygon.minLon),
			maxLon: Math.max(result.maxLon, polygon.maxLon),
			minLat: Math.min(result.minLat, polygon.minLat),
			maxLat: Math.max(result.maxLat, polygon.maxLat),
		}), { minLon: Infinity, maxLon: -Infinity, minLat: Infinity, maxLat: -Infinity });
		if (bounds.maxLon - bounds.minLon > 180) return;
		const topLeft = this.mapChart.convertToPixel({ geoIndex: 0 }, [bounds.minLon, bounds.maxLat]);
		const bottomRight = this.mapChart.convertToPixel({ geoIndex: 0 }, [bounds.maxLon, bounds.minLat]);
		const width = Math.abs(bottomRight?.[0] - topLeft?.[0]);
		const height = Math.abs(bottomRight?.[1] - topLeft?.[1]);
		if (!Number.isFinite(width) || !Number.isFinite(height) || !width || !height) return;
		const zoom = Math.min(
			this.mapContainer.clientWidth * 0.92 / width,
			this.mapContainer.clientHeight * 0.92 / height,
		);
		if (Number.isFinite(zoom) && zoom > 0) this.mapChart.setOption({ geo: {
			center: [(bounds.minLon + bounds.maxLon) / 2, (bounds.minLat + bounds.maxLat) / 2],
			zoom,
		} });
	}

	createMapLabelLayout() {
		let placed = [];
		let previousSeries = Infinity;
		let previousIndex = Infinity;
		return ({ seriesIndex, dataIndex, rect, labelRect }) => {
			// 每次地图重新布局时，从空白画布重新安排所有标签。
			if (seriesIndex < previousSeries ||
				(seriesIndex === previousSeries && dataIndex <= previousIndex)) placed = [];
			previousSeries = seriesIndex;
			previousIndex = dataIndex;
			if (!rect || !labelRect || !Number.isFinite(rect.x) || !Number.isFinite(labelRect.width)) {
				return { hideOverlap: false };
			}
			const width = this.mapChart.getWidth();
			const height = this.mapChart.getHeight();
			const labelWidth = labelRect.width;
			const labelHeight = labelRect.height;
			const centerX = rect.x + rect.width / 2;
			const centerY = rect.y + rect.height / 2;
			const gap = 7;
			const candidates = [];
			for (const offset of [0, -18, 18, -36, 36, -54, 54]) {
				candidates.push([centerX + gap, centerY - labelHeight / 2 + offset]);
				candidates.push([centerX - gap - labelWidth, centerY - labelHeight / 2 + offset]);
			}
			for (const offset of [0, -25, 25, -50, 50]) {
				candidates.push([centerX - labelWidth / 2 + offset, centerY - gap - labelHeight]);
				candidates.push([centerX - labelWidth / 2 + offset, centerY + gap]);
			}
			const padding = 2;
			const options = candidates.map(([x, y], index) => {
				const overlap = placed.reduce((total, label) => total +
					Math.max(0, Math.min(x + labelWidth + padding, label.x + label.width + padding) -
						Math.max(x - padding, label.x - padding)) *
					Math.max(0, Math.min(y + labelHeight + padding, label.y + label.height + padding) -
						Math.max(y - padding, label.y - padding)), 0);
				const overflow = Math.max(0, -x) + Math.max(0, -y) +
					Math.max(0, x + labelWidth - width) + Math.max(0, y + labelHeight - height);
				const distance = Math.hypot(x + labelWidth / 2 - centerX, y + labelHeight / 2 - centerY);
				return { x, y, score: (overlap + overflow) * 10000 + distance + index * 0.01 };
			});
			options.sort((a, b) => a.score - b.score);
			const choice = options[0];
			placed.push({ x: choice.x, y: choice.y, width: labelWidth, height: labelHeight });
			return { x: choice.x, y: choice.y + labelHeight / 2,
				align: "left", verticalAlign: "middle", hideOverlap: false };
		};
	}

	async openCountry(country) {
		if (!country) return;
		const token = ++this.countryLoadToken;
		this.activeCountry = country;
		this.selectedRegion = null;
		this.selectedCity = null;
		this.searchQuery = "";
		this.countryMapLoaded = false;
		this.regionLookup.clear();
		this.cityLookup.clear();
		this.cityRegionKeys.clear();
		this.plottedCities = [];
		this.regionOnlyMarkers = [];
		this.renderSettingsPanel();
		this.$main.find(".fjtc-map-breadcrumb").text(country.properties.name);
		this.setMapStatus(`正在加载${country.properties.name}的省州和城市…`);
		try {
			const isChina = country.properties.ADM0_A3 === "CHN";
			const [regions, cities, taiwanProvince] = await Promise.all([
				this.loadMapData("regions", country.properties.ADM0_A3),
				this.loadMapData("cities"),
				isChina ? this.loadMapData("taiwanProvince") : Promise.resolve(null),
			]);
			if (token !== this.countryLoadToken || this.viewMode !== "settings") return;
			const iso2 = country.properties.ISO_A2;
			const iso3 = country.properties.ADM0_A3;
			const specialRegions = isChina ? [...this.countryLookup.values()].flatMap((feature) => {
				const region = FENGJING_CALENDAR_CHINA_SPECIAL_REGIONS[feature.properties.ADM0_A3];
				return region ? [{
					type: "Feature",
					properties: { ...region, iso_a2: "CN", adm0_a3: "CHN",
						iso_3166_2: region.key, ne_id: region.key, NE_ID: region.key },
					geometry: feature.geometry,
				}] : [];
			}) : [];
			const countryRegions = regions.features.filter((feature) =>
				feature.properties.iso_a2 === iso2 || feature.properties.adm0_a3 === iso3
			).concat(taiwanProvince?.features || [], specialRegions).map((feature) => {
				const key = String(feature.properties.NE_ID ?? feature.properties.ne_id);
				this.regionLookup.set(key, feature);
				return { ...feature, properties: { ...feature.properties, name: key } };
			});
			const countryCities = cities.features.filter((feature) =>
				feature.properties.ISO_A2 === iso2 || feature.properties.ADM0_A3 === iso3 ||
				(isChina && (feature.properties.ADM0_A3 === "TWN" ||
					FENGJING_CALENDAR_CHINA_SPECIAL_REGIONS[feature.properties.ADM0_A3]))
			);
			for (const city of countryCities) this.cityLookup.set(String(city.properties.NE_ID), city);
			const mapName = `fjtc-country-${country.properties.NE_ID}`;
			const features = countryRegions.length ? countryRegions : [{
				...country,
				properties: { ...country.properties, name: String(country.properties.NE_ID) },
			}];
			echarts.registerMap(mapName, { type: "FeatureCollection", features });
			const regionByName = new Map();
			for (const region of this.regionLookup.values()) {
				const regionKey = String(region.properties.ne_id);
				for (const name of [region.properties.name_en, region.properties.name,
					FENGJING_CALENDAR_REGION_ALIASES[region.properties.iso_3166_2]]) {
					if (name) regionByName.set(String(name).trim().toLocaleLowerCase(), regionKey);
				}
			}
			const regionSpatialIndex = this.buildRegionSpatialIndex();
			const mainCityByRegion = new Map();
			for (const city of countryCities) {
				if (!Number.isFinite(Number(city.properties.LONGITUDE)) || !Number.isFinite(Number(city.properties.LATITUDE))) continue;
				const cityKey = String(city.properties.NE_ID);
				const regionKey = (isChina && (city.properties.ADM0_A3 === "TWN" ? "CN-TW"
					: FENGJING_CALENDAR_CHINA_SPECIAL_REGIONS[city.properties.ADM0_A3]?.key)) ||
					(FENGJING_CALENDAR_CITY_REGION_OVERRIDES[cityKey]
					? [...this.regionLookup.values()].find((region) =>
						region.properties.iso_3166_2 === FENGJING_CALENDAR_CITY_REGION_OVERRIDES[cityKey])?.properties.ne_id
					: null) || this.regionAtPoint(regionSpatialIndex,
					Number(city.properties.LONGITUDE), Number(city.properties.LATITUDE)) ||
					regionByName.get(String(city.properties.ADM1NAME || "").trim().toLocaleLowerCase());
				if (!regionKey) continue;
				this.cityRegionKeys.set(cityKey, String(regionKey));
				const previous = mainCityByRegion.get(String(regionKey));
				const isCapital = Boolean(city.properties.ADMIN1_CAPITAL);
				const previousIsCapital = Boolean(previous?.properties.ADMIN1_CAPITAL);
				if (!previous || (isCapital && !previousIsCapital) ||
					(isCapital === previousIsCapital && (Number(city.properties.POP_MAX) || 0) > (Number(previous.properties.POP_MAX) || 0))) {
					mainCityByRegion.set(String(regionKey), city);
				}
			}
			this.plottedCities = [...mainCityByRegion]
				.map(([regionKey, city]) => ({
					name: city.properties.name,
					regionName: this.regionLookup.get(regionKey)?.properties.name || "",
					cityKey: String(city.properties.NE_ID),
					label: { show: true },
					value: [Number(city.properties.LONGITUDE), Number(city.properties.LATITUDE)],
				}));
			for (const cityKey of Object.values(FENGJING_CALENDAR_EXTRA_WEATHER_CITIES).flat()) {
				const city = this.cityLookup.get(cityKey);
				const regionKey = this.cityRegionKeys.get(cityKey);
				if (!city || !regionKey || this.plottedCities.some((entry) => entry.cityKey === cityKey)) continue;
				this.plottedCities.push({
					name: city.properties.name,
					regionName: this.regionLookup.get(regionKey)?.properties.name || "",
					cityKey,
					isExtraCity: true,
					label: { show: true, position: "bottom" },
					value: [Number(city.properties.LONGITUDE), Number(city.properties.LATITUDE)],
				});
			}
			this.regionOnlyMarkers = [...this.regionLookup]
				.filter(([key]) => !mainCityByRegion.has(key))
				.map(([key, region]) => ({
					name: region.properties.name,
					regionKey: key,
					value: [Number(region.properties.longitude), Number(region.properties.latitude)],
				}))
				.filter((marker) => marker.value.every(Number.isFinite));
			const labelLayout = this.createMapLabelLayout();
			this.mapChart.setOption({
				animation: false,
				tooltip: {
					trigger: "item",
					formatter: (params) => this.escapeHtml(params.seriesType === "scatter"
						? params.data.name
						: this.regionLookup.get(String(params.name))?.properties.name || country.properties.name),
				},
				geo: {
					map: mapName,
					roam: true,
					layoutCenter: ["50%", "50%"],
					layoutSize: "96%",
					aspectScale: 0.9,
					itemStyle: { areaColor: "#e9eef5", borderColor: "#9aa9ba", borderWidth: 0.8 },
					label: { show: false },
					emphasis: { itemStyle: { areaColor: "#c8ddf9" } },
				},
				series: [{
					type: "scatter", coordinateSystem: "geo", data: this.plottedCities,
					labelLayout,
					z: 20, symbolSize: 9,
					itemStyle: { color: "#eab308", borderColor: "#fff", borderWidth: 1.5 },
					label: { show: true, position: "right", color: "#713f12", fontSize: 12,
						fontWeight: 600, textBorderColor: "#fff", textBorderWidth: 3,
						formatter: (params) => this.formatMapCityLabel(params.data) },
					emphasis: { scale: 1.5 },
				}, {
					type: "scatter", coordinateSystem: "geo", data: this.regionOnlyMarkers,
					labelLayout,
					z: 19, symbolSize: 8,
					itemStyle: { color: "#eab308", borderColor: "#fff", borderWidth: 1.5 },
					label: { show: true, position: "right", color: "#713f12", fontSize: 12,
						fontWeight: 600, textBorderColor: "#fff", textBorderWidth: 3,
						formatter: (params) => params.data.name },
				}],
			}, true);
			this.mapChart.resize();
			this.fitCountryMap(regionSpatialIndex);
			this.countryMapLoaded = true;
			this.renderSettingsPanel();
			this.updateMapHighlights();
			this.setMapStatus("");
		} catch (error) {
			if (token !== this.countryLoadToken) return;
			this.setMapStatus("省州或城市地图加载失败，请检查离线地图文件。");
			this.showError("地区地图加载失败", error);
		}
	}

	handleMapClick(params) {
		if (params.seriesType === "scatter" && params.data?.cityKey) {
			this.selectCity(params.data.cityKey);
			return;
		}
		if (params.seriesType === "scatter" && params.data?.regionKey) {
			this.selectRegion(params.data.regionKey);
			return;
		}
		if (params.componentType !== "geo") return;
		if (!this.activeCountry) {
			this.openCountry(this.countryLookup.get(String(params.name)));
			return;
		}
		this.selectRegion(params.name);
	}

	selectRegion(key, preserveSearch = false) {
		const region = this.regionLookup.get(String(key));
		if (!region) return;
		if (String(this.selectedRegion?.properties.ne_id) === String(key)) {
			this.selectedRegion = null;
			this.selectedCity = null;
			if (!preserveSearch) this.searchQuery = "";
			this.renderSettingsPanel();
			this.updateMapHighlights();
			return;
		}
		this.selectedRegion = region;
		this.selectedCity = null;
		if (!preserveSearch) this.searchQuery = "";
		const mainCity = this.plottedCities.find((city) =>
			this.cityRegionKeys.get(city.cityKey) === String(key));
		if (mainCity) {
			this.selectCity(mainCity.cityKey, preserveSearch);
			return;
		}
		this.renderSettingsPanel();
		this.updateMapHighlights();
	}

	selectCity(key, preserveSearch = false) {
		const city = this.cityLookup.get(String(key));
		if (!city) return;
		if (String(this.selectedCity?.properties.NE_ID) === String(key)) {
			this.selectedCity = null;
			if (!preserveSearch) this.searchQuery = "";
			this.renderSettingsPanel();
			this.updateMapHighlights();
			return;
		}
		this.selectedCity = city;
		this.selectedRegion = this.regionLookup.get(this.cityRegionKeys.get(String(key))) || null;
		if (!preserveSearch) this.searchQuery = "";
		this.renderSettingsPanel();
		this.updateMapHighlights();
	}

	updateMapHighlights() {
		if (!this.countryMapLoaded || !this.mapChart || !this.activeCountry) return;
		const term = this.searchQuery.trim().toLocaleLowerCase();
		const matches = (values) => term && values.some((value) =>
			String(value || "").toLocaleLowerCase().includes(term));
		const selectedRegionKey = this.selectedRegion && String(this.selectedRegion.properties.ne_id);
		const selectedCityKey = this.selectedCity && String(this.selectedCity.properties.NE_ID);
		const matchedCities = [...this.cityLookup.values()].filter((city) =>
			matches([city.properties.name, city.properties.NAME_EN])).slice(0, 12);
		const plotted = new Map(this.plottedCities.map((city) => [city.cityKey, city]));
		for (const city of [...matchedCities, this.selectedCity].filter(Boolean)) {
			const cityKey = String(city.properties.NE_ID);
			if (!plotted.has(cityKey)) {
				const regionKey = this.cityRegionKeys.get(cityKey);
				plotted.set(cityKey, {
					name: city.properties.name, cityKey,
					regionName: this.regionLookup.get(regionKey)?.properties.name || city.properties.ADM1NAME || "",
					isExtraCity: Boolean(regionKey && [...plotted.values()].some((entry) =>
						this.cityRegionKeys.get(entry.cityKey) === regionKey)),
					value: [Number(city.properties.LONGITUDE), Number(city.properties.LATITUDE)],
				});
			}
		}
		const cities = [...plotted.values()].map((city) => {
			const highlighted = city.cityKey === selectedCityKey ||
				matches([city.name, this.cityLookup.get(city.cityKey)?.properties.NAME_EN]);
			return highlighted ? { ...city, symbolSize: city.cityKey === selectedCityKey ? 16 : 13,
				itemStyle: { color: "#f59e0b", borderColor: "#1e40af", borderWidth: 2.5 },
				label: { show: true, fontWeight: 700, color: "#92400e" } } : city;
		});
		const regions = [...this.regionLookup].map(([key, region]) => {
			const selected = key === selectedRegionKey;
			const highlighted = selected || matches([region.properties.name, region.properties.name_en,
				region.properties.iso_3166_2]);
			return { name: key, itemStyle: {
				areaColor: highlighted ? (selected ? "#fcd34d" : "#bfdbfe") : "#e9eef5",
				borderColor: highlighted ? (selected ? "#b45309" : "#2563eb") : "#9aa9ba",
				borderWidth: highlighted ? 2 : 0.8,
			} };
		});
		this.mapChart.setOption({ geo: { regions }, series: [{ data: cities }] });
	}

	changeMapSetting(event) {
		const { mapSetting } = event.currentTarget.dataset;
		const checked = event.currentTarget.checked;
		if (mapSetting === "country" && this.activeCountry) {
			const key = String(this.activeCountry.properties.NE_ID);
			if (checked) {
				this.holidayCountries.set(key, this.activeCountry.properties.name);
				this.defaultHolidayCountry ||= key;
			} else {
				this.holidayCountries.delete(key);
				if (this.defaultHolidayCountry === key) {
					this.defaultHolidayCountry = this.holidayCountries.keys().next().value || null;
				}
			}
		} else if ((mapSetting === "weather" || mapSetting === "time") && this.activeCountry) {
			const cityKey = event.currentTarget.dataset.cityKey;
			const city = this.cityLookup.get(cityKey);
			const region = this.regionLookup.get(this.cityRegionKeys.get(cityKey));
			if (!city || !region) return;
			const key = `${this.activeCountry.properties.NE_ID}:${cityKey}`;
			const selectedCities = mapSetting === "weather" ? this.weatherCities : this.timeCities;
			if (checked) {
				selectedCities.set(key, {
					country: this.activeCountry.properties.name,
					region: region.properties.name,
					city: city.properties.name,
					...(mapSetting === "time" ? {
						timeZone: city.properties.TIMEZONE || "",
						countryCode: this.activeCountry.properties.ISO_A2 || "",
					} : {}),
				});
				if (mapSetting === "weather") this.defaultWeatherCity ||= key;
				if (mapSetting === "time") this.defaultTimeCity ||= key;
			} else {
				selectedCities.delete(key);
				if (mapSetting === "weather" && this.defaultWeatherCity === key) {
					this.defaultWeatherCity = selectedCities.keys().next().value || null;
				}
				if (mapSetting === "time" && this.defaultTimeCity === key) {
					this.defaultTimeCity = selectedCities.keys().next().value || null;
				}
			}
			if (mapSetting === "weather") {
				this.weatherViewCity = this.defaultWeatherCity;
				this.weatherViewActive = Boolean(this.weatherViewCity);
				this.container.classList.toggle("fjtc-weather-mode", this.weatherViewActive);
			}
			this.selectedRegion = region;
			this.selectedCity = city;
		}
		this.renderSettingsPanel();
		this.updateMapHighlights();
		this.scheduleMapSettingsSave();
	}

	getRegionSelectableCities(region) {
		if (!region) return [];
		const regionKey = String(region.properties.ne_id);
		const keys = [
			this.plottedCities.find((city) => this.cityRegionKeys.get(city.cityKey) === regionKey)?.cityKey,
			...(FENGJING_CALENDAR_EXTRA_WEATHER_CITIES[region.properties.iso_3166_2] || []),
			...[...this.weatherCities.keys()].filter((key) => key.startsWith(`${this.activeCountry?.properties.NE_ID}:`))
				.map((key) => key.split(":")[1])
				.filter((key) => this.cityRegionKeys.get(key) === regionKey),
			...[...this.timeCities.keys()].filter((key) => key.startsWith(`${this.activeCountry?.properties.NE_ID}:`))
				.map((key) => key.split(":")[1])
				.filter((key) => this.cityRegionKeys.get(key) === regionKey),
			this.selectedCity && this.cityRegionKeys.get(String(this.selectedCity.properties.NE_ID)) === regionKey
				? String(this.selectedCity.properties.NE_ID) : null,
		];
		return [...new Set(keys.filter(Boolean))]
			.map((key) => this.cityLookup.get(key))
			.filter(Boolean);
	}

	formatCitySelection(country, region, city, shownRegions) {
		const regionName = String(region || "").replace(/省$/, "");
		if (!regionName) return String(city || "");
		const regionKey = `${country || ""}:${regionName}`;
		if (shownRegions.has(regionKey)) return String(city || "");
		shownRegions.add(regionKey);
		return `${regionName}·${city}`;
	}

	formatMapCityLabel(city) {
		const regionName = String(city.regionName || "")
			.replace(/(特别行政区|维吾尔自治区|壮族自治区|回族自治区|自治区|省|市|州|区)$/, "");
		return regionName && !city.isExtraCity ? `${regionName}·${city.name}` : city.name;
	}

	renderCitySelectionChips(selections, defaultKey, kind) {
		const shownRegions = new Set();
		const labelType = kind === "weather" ? "天气" : "时间";
		return [...selections].map(([key, location]) => {
			const selected = defaultKey === key;
			const label = this.formatCitySelection(location.country, location.region, location.city, shownRegions);
			const fullName = [location.country, location.region, location.city].filter(Boolean).join(" · ");
			return `<span class="fjtc-map-selection-chip" data-map-order-kind="${kind}" data-map-order-key="${this.escapeHtml(key)}" title="${this.escapeHtml(fullName)} · 在同一行拖拽排序">
				<span class="fjtc-map-drag-handle" aria-hidden="true">⋮⋮</span>
				<button type="button" class="fjtc-map-default-btn${selected ? " is-default" : ""}" data-map-default-${kind}="${this.escapeHtml(key)}" aria-pressed="${selected}" title="设为默认${labelType}城市">默认</button>
				<span>${this.escapeHtml(label)}</span>
				<button type="button" class="fjtc-map-remove-btn" data-map-remove-${kind}="${this.escapeHtml(key)}" aria-label="移除${this.escapeHtml(fullName)}">×</button>
			</span>`;
		});
	}

	renderMapSelections() {
		const holidays = [...this.holidayCountries].map(([key, name]) => {
			const selected = this.defaultHolidayCountry === key;
			return `<span class="fjtc-map-selection-chip" data-map-order-kind="holiday" data-map-order-key="${this.escapeHtml(key)}" title="${this.escapeHtml(name)} · 在同一行拖拽排序">
				<span class="fjtc-map-drag-handle" aria-hidden="true">⋮⋮</span>
				<button type="button" class="fjtc-map-default-btn${selected ? " is-default" : ""}" data-map-default-holiday="${this.escapeHtml(key)}" aria-pressed="${selected}" title="设为默认节日国家">默认</button>
				<span>${this.escapeHtml(name)}</span>
				<button type="button" class="fjtc-map-remove-btn" data-map-remove-holiday="${this.escapeHtml(key)}" aria-label="移除${this.escapeHtml(name)}">×</button>
			</span>`;
		});
		const weather = this.renderCitySelectionChips(this.weatherCities, this.defaultWeatherCity, "weather");
		const time = this.renderCitySelectionChips(this.timeCities, this.defaultTimeCity, "time");
		this.$main.find(".fjtc-map-selections")
			.html(`<div class="fjtc-map-selection-row" data-map-order-row="holiday"><strong>国家节日</strong>${holidays.join("") || "<span>未选择</span>"}</div>
				<div class="fjtc-map-selection-row" data-map-order-row="weather"><strong>城市天气</strong>${weather.join("") || "<span>未选择</span>"}</div>
				<div class="fjtc-map-selection-row" data-map-order-row="time"><strong>城市时间</strong>${time.join("") || "<span>未选择</span>"}</div>`);
	}

	mapSelectionCollection(kind) {
		return { holiday: this.holidayCountries, weather: this.weatherCities, time: this.timeCities }[kind];
	}

	startMapSelectionPointer(event) {
		const pointer = event.originalEvent;
		const chip = event.currentTarget;
		if (pointer.button !== 0 ||
			this.mapSelectionCollection(chip.dataset.mapOrderKind)?.size < 2) return;
		this.cancelMapSelectionPointer();
		const drag = {
			kind: chip.dataset.mapOrderKind,
			key: chip.dataset.mapOrderKey,
			pointerId: pointer.pointerId,
			startX: pointer.clientX,
			startY: pointer.clientY,
			startedOnButton: !!event.target.closest("button"),
			source: chip,
			active: false,
			target: null,
		};
		drag.onMove = (move) => this.moveMapSelectionPointer(move);
		drag.onUp = (up) => this.finishMapSelectionPointer(up);
		drag.onCancel = () => this.cancelMapSelectionPointer();
		this.mapPointerDrag = drag;
		window.addEventListener("pointermove", drag.onMove);
		window.addEventListener("pointerup", drag.onUp);
		window.addEventListener("pointercancel", drag.onCancel);
		window.addEventListener("blur", drag.onCancel);
		if (!drag.startedOnButton) event.preventDefault();
	}

	moveMapSelectionPointer(event) {
		const drag = this.mapPointerDrag;
		if (!drag || event.pointerId !== drag.pointerId) return;
		if (!drag.active) {
			if (Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < 4) return;
			drag.active = true;
			drag.source.classList.add("is-dragging");
			drag.ghost = drag.source.cloneNode(true);
			drag.ghost.classList.remove("is-dragging");
			drag.ghost.classList.add("is-drag-ghost");
			drag.ghost.style.width = `${drag.source.getBoundingClientRect().width}px`;
			document.body.appendChild(drag.ghost);
			document.body.classList.add("fjtc-map-reordering");
		}
		drag.ghost.style.left = `${event.clientX + 12}px`;
		drag.ghost.style.top = `${event.clientY + 12}px`;
		const element = document.elementFromPoint(event.clientX, event.clientY);
		const row = element?.closest(".fjtc-map-selection-row");
		const directChip = element?.closest(".fjtc-map-selection-chip");
		const chip = row?.dataset.mapOrderRow === drag.kind && directChip !== drag.source
			? directChip || this.mapSelectionTargetForRow(row, event.clientX) : null;
		this.$main.find(".fjtc-map-selection-chip.is-drop-before, .fjtc-map-selection-chip.is-drop-after")
			.removeClass("is-drop-before is-drop-after");
		drag.target = chip;
		if (chip) {
			drag.before = this.mapSelectionDropBefore(chip, event.clientX, drag);
			chip.classList.add(drag.before ? "is-drop-before" : "is-drop-after");
		}
		event.preventDefault();
	}

	mapSelectionTargetForRow(row, clientX) {
		const chips = [...row.querySelectorAll(".fjtc-map-selection-chip")]
			.filter((chip) => chip.dataset.mapOrderKey !== this.mapPointerDrag?.key);
		return chips.reduce((nearest, chip) => {
			const rect = chip.getBoundingClientRect();
			const distance = Math.max(rect.left - clientX, 0, clientX - rect.right);
			return !nearest || distance < nearest.distance ? { chip, distance } : nearest;
		}, null)?.chip || null;
	}

	mapSelectionDropBefore(chip, clientX, drag) {
		const rect = chip.getBoundingClientRect();
		const position = (clientX - rect.left) / rect.width;
		if (position < 0.35) return true;
		if (position > 0.65) return false;
		const keys = [...this.mapSelectionCollection(drag.kind).keys()];
		return keys.indexOf(drag.key) > keys.indexOf(chip.dataset.mapOrderKey);
	}

	finishMapSelectionPointer(event) {
		const drag = this.mapPointerDrag;
		if (!drag || event.pointerId !== drag.pointerId) return;
		if (drag.active) this.moveMapSelectionPointer(event);
		const targetKey = drag.target?.dataset.mapOrderKey;
		const { kind, key, before, active } = drag;
		if (active) this.mapSelectionClickSuppressedUntil = Date.now() + 250;
		this.cancelMapSelectionPointer();
		if (!active || !targetKey) return;
		const collection = this.mapSelectionCollection(kind);
		const entries = [...collection.entries()];
		const previousOrder = entries.map(([entryKey]) => entryKey);
		const from = entries.findIndex(([entryKey]) => entryKey === key);
		if (from < 0 || targetKey === key) return;
		const [moved] = entries.splice(from, 1);
		const target = entries.findIndex(([entryKey]) => entryKey === targetKey);
		if (target < 0) return;
		entries.splice(target + (before ? 0 : 1), 0, moved);
		if (entries.every(([entryKey], index) => entryKey === previousOrder[index])) return;
		collection.clear();
		for (const [entryKey, value] of entries) collection.set(entryKey, value);
		this.renderMapSelections();
		this.scheduleMapSettingsSave();
	}

	cancelMapSelectionPointer() {
		const drag = this.mapPointerDrag;
		if (!drag) return;
		window.removeEventListener("pointermove", drag.onMove);
		window.removeEventListener("pointerup", drag.onUp);
		window.removeEventListener("pointercancel", drag.onCancel);
		window.removeEventListener("blur", drag.onCancel);
		drag.ghost?.remove();
		document.body.classList.remove("fjtc-map-reordering");
		this.$main.find(".fjtc-map-selection-chip.is-dragging, .fjtc-map-selection-chip.is-drop-before, .fjtc-map-selection-chip.is-drop-after")
			.removeClass("is-dragging is-drop-before is-drop-after");
		this.mapPointerDrag = null;
	}

	renderSettingCityRows(cities, country, region, kind) {
		const shownRegions = new Set();
		const labelType = kind === "weather" ? "城市天气" : "城市时间";
		const selectedCities = kind === "weather" ? this.weatherCities : this.timeCities;
		return cities.map((city) => {
			const cityKey = String(city.properties.NE_ID);
			const label = this.formatCitySelection(country?.properties.name, region.properties.name,
				city.properties.name, shownRegions);
			return `<label class="fjtc-setting-row"><span><strong>${this.escapeHtml(label)}</strong><small>${labelType}</small></span><input type="checkbox" data-map-setting="${kind}" data-city-key="${this.escapeHtml(cityKey)}" ${selectedCities.has(`${country.properties.NE_ID}:${cityKey}`) ? "checked" : ""}></label>`;
		}).join("");
	}

	renderSettingsPanel() {
		const country = this.activeCountry;
		const region = this.selectedRegion;
		const escaped = (value) => this.escapeHtml(value);
		const countryKey = country && String(country.properties.NE_ID);
		const selectableCities = this.getRegionSelectableCities(region);
		this.$main.find(".fjtc-settings-panel").html(`
			<div class="fjtc-weather-credentials">
				<a href="https://console.qweather.com/" target="_blank" rel="noopener noreferrer">和风天气</a>
				<label>API Host <input type="text" class="form-control" data-weather-credential="qweather_api_host" placeholder="在控制台「设置」查看，形如 xxx.qweatherapi.com" autocomplete="off"></label>
				<label>凭据ID <input type="text" class="form-control" data-weather-credential="qweather_credential_id" autocomplete="off"></label>
				<label>API秘钥 <input type="text" class="form-control" data-weather-credential="qweather_api_key" autocomplete="off"></label>
				<small>API Host 是账号专属的接口域名，在和风天气控制台「设置」中查看。备用请求使用 API Host 和 API 秘钥；凭据ID仅供记录。</small>
			</div>
			<h3>选择地区</h3>
			<label class="fjtc-setting-row"><span><strong>${country ? escaped(country.properties.name) : "国家节假日"}</strong><small>${country ? "国家节假日" : "请先在地图中选择国家"}</small></span><input type="checkbox" data-map-setting="country" ${this.holidayCountries.has(countryKey) ? "checked" : ""} ${country ? "" : "disabled"}></label>
			<div class="fjtc-setting-groups">
				<div class="fjtc-setting-group-title">城市天气</div>
				${region && country ? this.renderSettingCityRows(selectableCities, country, region, "weather") : ""}
				<div class="fjtc-setting-group-title">城市时间</div>
				${region && country ? this.renderSettingCityRows(selectableCities, country, region, "time") : ""}
			</div>
			${country ? `<div class="fjtc-city-picker"><label for="fjtc-city-search">搜索省州或城市</label><input id="fjtc-city-search" type="search" class="form-control fjtc-city-search" placeholder="输入省州或城市名称" value="${escaped(this.searchQuery)}"><div class="fjtc-city-results"></div></div>` : ""}
		`);
		this.$main.find('[data-weather-credential="qweather_api_host"]').val(this.qweatherApiHost);
		this.$main.find('[data-weather-credential="qweather_credential_id"]').val(this.qweatherCredentialId);
		this.$main.find('[data-weather-credential="qweather_api_key"]').val(this.qweatherApiKey);
		this.renderMapSelections();
		if (country) this.renderCityResults(this.searchQuery);
	}

	renderCityResults(query) {
		const term = String(query || "").trim().toLocaleLowerCase();
		this.searchQuery = String(query || "");
		const regions = term ? [...this.regionLookup].filter(([, region]) =>
			[region.properties.name, region.properties.name_en, region.properties.iso_3166_2]
				.some((name) => String(name || "").toLocaleLowerCase().includes(term))) : [];
		const preferredCityOrder = this.selectedRegion && !term
			? new Map(this.getRegionSelectableCities(this.selectedRegion)
				.map((city, index) => [String(city.properties.NE_ID), index]))
			: null;
		const cities = [...this.cityLookup.values()]
			.filter((city) => !preferredCityOrder || preferredCityOrder.has(String(city.properties.NE_ID)))
			.filter((city) => !term || [city.properties.name, city.properties.NAME_EN]
				.some((name) => String(name || "").toLocaleLowerCase().includes(term)))
			.sort((a, b) => preferredCityOrder
				? preferredCityOrder.get(String(a.properties.NE_ID)) - preferredCityOrder.get(String(b.properties.NE_ID))
				: (Number(b.properties.POP_MAX) || 0) - (Number(a.properties.POP_MAX) || 0))
			.slice(0, Math.max(0, 12 - regions.length));
		const shownRegions = new Set();
		const results = [
			...regions.slice(0, 12).map(([key, region]) => `<button type="button" class="fjtc-city-result${String(this.selectedRegion?.properties.ne_id) === key ? " is-selected" : ""}" data-region-key="${key}">${this.escapeHtml(region.properties.name)}<small>省州</small></button>`),
			...cities.map((city) => {
				const cityKey = String(city.properties.NE_ID);
				const region = this.regionLookup.get(this.cityRegionKeys.get(cityKey));
				const label = this.formatCitySelection(this.activeCountry?.properties.name,
					region?.properties.name || city.properties.ADM1NAME, city.properties.name, shownRegions);
				return `<button type="button" class="fjtc-city-result${String(this.selectedCity?.properties.NE_ID) === cityKey ? " is-selected" : ""}" data-city-key="${this.escapeHtml(cityKey)}">${this.escapeHtml(label)}<small>${this.escapeHtml(city.properties.NAME_EN || "")}</small></button>`;
			}),
		];
		this.$main.find(".fjtc-city-results").html(results.length ? results.join("")
			: `<span class="fjtc-city-empty">${this.countryMapLoaded ? "没有找到省州或城市" : "正在加载城市…"}</span>`);
		this.updateMapHighlights();
	}

	setMapStatus(message) {
		this.$main.find(".fjtc-map-status").text(message).toggle(Boolean(message));
	}

	escapeHtml(value) {
		return frappe.utils.escape_html(String(value ?? ""));
	}

	createCalendar(library) {
		return new library.Calendar(this.container, {
			plugins: [library.classicThemePlugin],
			themeSystem: "classic",
			locale: library.zhCnLocale,
			initialView: "dayGridMonth",
			height: "100%",
			firstDay: 1,
			nowIndicator: true,
			navLinks: true,
			selectable: true,
			selectMirror: true,
			editable: true,
			eventStartEditable: true,
			eventDurationEditable: true,
			dayMaxEvents: true,
			displayEventEnd: true,
			buttonText: {
				today: "今天",
				month: "月",
				week: "周",
				day: "日",
				list: "列表",
			},
			headerToolbar: {
				left: "prev,next today weatherPreview",
				center: "title",
				right: "cityClocks dayGridMonth,timeGridWeek,timeGridDay,listMonth",
			},
			toolbarElements: {
				weatherPreview: () => ({ html: '<span class="fjtc-weather-preview" aria-label="城市天气预览"></span>' }),
				cityClocks: () => ({ html: '<span class="fjtc-city-clocks" aria-label="城市时间"></span>' }),
			},
			toolbarTitleClass: "fjtc-toolbar-title",
			headerToolbarClass: "fjtc-toolbar",
			toolbarSectionClass: ({ name }) => name === "center" ? "fjtc-toolbar-center" : "",
			dayCellTopInnerClass: (info) => {
				if (info.view.type !== "dayGridMonth") return "";
				return `fjtc-day-label${[0, 6].includes(info.date.getDay()) ? " fjtc-weekend-day" : ""}`;
			},
			datesSet: (info) => {
				this.formatCalendarMonthTitle(info);
				this.loadHolidays(info);
				this.renderCityClocks();
				this.renderWeatherPreview();
				this.renderDailyWeather();
			},
			events: (info, success, failure) => this.loadEvents(info, success, failure),
			select: (info) => this.openEventDialog(null, this.selectionDefaults(info)),
			eventClick: (info) => this.openEventDialog(info.event.extendedProps.event_name),
			eventDrop: (info) => this.persistCalendarMove(info),
			eventResize: (info) => this.persistCalendarMove(info),
			eventDidMount: (info) => {
				const props = info.event.extendedProps;
				info.el.title = props.repeat_this_event
					? `${info.event.title} · 重复日程需在完整表单中修改`
					: info.event.title;
			},
		});
	}

	formatCalendarMonthTitle(info) {
		const title = this.container.querySelector(".fjtc-toolbar-title");
		if (title) {
			const today = document.createElement("span");
			today.className = "fjtc-beijing-date";
			const windy = document.createElement("iframe");
			windy.className = "fjtc-windy-map";
			windy.title = "Windy 风场地图";
			windy.loading = "lazy";
			windy.setAttribute("frameborder", "0");
			windy.src = "https://embed.windy.com/embed.html?type=map&location=coordinates&metricRain=mm&metricTemp=°C&metricWind=km/h&zoom=3&overlay=wind&product=ecmwf&level=surface&lat=39.749&lon=-99.876";
			const preview = document.createElement("span");
			preview.className = "fjtc-windy-preview";
			const expand = document.createElement("button");
			expand.type = "button";
			expand.className = "fjtc-windy-expand";
			expand.setAttribute("aria-label", "放大 Windy 风场地图");
			expand.title = "点击放大风场地图";
			expand.textContent = "⤢";
			preview.append(windy, expand);
			const maps = document.createElement("div");
			maps.className = "fjtc-map-previews";
			maps.append(preview);
			title.replaceChildren(today, maps);
			this.updateBeijingDate();
		}
	}

	openWindyMap(trigger) {
		const overlay = this.$main.find(".fjtc-windy-overlay")[0];
		if (!overlay) return;
		this.windyTrigger = trigger;
		overlay.querySelector(".fjtc-windy-expanded-map").src = "https://embed.windy.com/embed.html?type=map&location=coordinates&metricRain=mm&metricTemp=°C&metricWind=km/h&zoom=3&overlay=wind&product=ecmwf&level=surface&lat=28.15&lon=-94.395&detailLat=41.085&detailLon=-94.401&detail=true&pressure=false&message=true";
		overlay.hidden = false;
		overlay.querySelector(".fjtc-windy-close").focus();
	}

	closeWindyMap() {
		const overlay = this.$main?.find(".fjtc-windy-overlay")[0];
		if (!overlay || overlay.hidden) return;
		overlay.hidden = true;
		overlay.querySelector(".fjtc-windy-expanded-map").removeAttribute("src");
		if (this.windyTrigger?.isConnected && this.pageVisible && this.viewMode === "calendar") {
			this.windyTrigger.focus();
		}
		this.windyTrigger = null;
	}

	updateBeijingDate() {
		const date = this.container.querySelector(".fjtc-beijing-date");
		if (!date) return;
		const parts = Object.fromEntries(new Intl.DateTimeFormat("zh-CN", {
			timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
		}).formatToParts(new Date()).map(({ type, value }) => [type, value]));
		date.textContent = `${parts.year}年${parts.month}月${parts.day}日`;
	}

	renderWeatherPreview() {
		const holder = this.container.querySelector(".fjtc-weather-preview");
		if (!holder) return;
		holder.replaceChildren();
		const locations = this.weatherCities.size ? [...this.weatherCities] : [[null, null]];
		for (const [key, location] of locations) {
			const card = document.createElement("span");
			card.className = "fjtc-weather-city";
			const weather = this.weatherByCity.get(key);
			const today = weather?.days?.find((day) => day.date === weather.current?.date);
			const icon = document.createElement("span");
			icon.className = "fjtc-weather-icon";
			icon.textContent = fengjingWeatherIcon(weather?.current?.weather_code ?? today?.weather_code,
				weather?.current ? Number(weather.current.is_day) === 1 : true);
			icon.setAttribute("aria-hidden", "true");
			const details = document.createElement("span");
			details.className = "fjtc-weather-details";
			const place = document.createElement("span");
			place.className = "fjtc-weather-place";
			place.textContent = location ? [location.region, location.city].filter(Boolean).join("·") : "请选择天气城市";
			const state = document.createElement("span");
			state.className = "fjtc-weather-state";
			const temperature = weather?.current?.temperature_c;
			state.textContent = temperature == null ? (location ? (this.weatherLoaded ? "当前天气暂不可用" : "正在获取天气") : "天气待接入")
				: `${Math.round(Number(temperature))}°C · ${weather.current.description || "当前天气"}`;
			details.append(place, state);
			if (weather?.current?.wind_speed_kmh != null) {
				const wind = document.createElement("span");
				wind.className = "fjtc-weather-wind";
				wind.textContent = `风速 ${weather.current.wind_speed_kmh} km/h`;
				details.appendChild(wind);
			}
			if (weather?.current?.source_name === "QWeather") {
				const credit = document.createElement("a");
				credit.className = "fjtc-weather-credit";
				credit.href = "https://developer.qweather.com/attribution.html";
				credit.target = "_blank";
				credit.rel = "noopener noreferrer";
				credit.textContent = "和风天气";
				details.appendChild(credit);
			}
			if (location) {
				const toggle = document.createElement("button");
				toggle.type = "button";
				toggle.className = "fjtc-weather-view-toggle";
				toggle.dataset.cityKey = key;
				toggle.setAttribute("aria-pressed", String(this.weatherViewActive && this.weatherViewCity === key));
				toggle.textContent = "当前天气";
				details.appendChild(toggle);
			}
			card.append(icon, details);
			card.title = location ? [location.country, place.textContent,
				today?.description,
				today ? `最高 ${today.temperature_max_c}°C / 最低 ${today.temperature_min_c}°C` : "",
				today?.precipitation_probability_pct != null ? `降水概率 ${today.precipitation_probability_pct}%` : "",
				weather?.current?.wind_speed_kmh != null ? `当前风速 ${weather.current.wind_speed_kmh} km/h` : "",
				`数据：${weather?.current?.source_name || today?.source_name || "天气服务"}`,
			].filter(Boolean).join(" · ") : "请先在地区设置中选择天气城市";
			holder.appendChild(card);
		}
	}

	async loadWeather(refresh = false, attempt = 0) {
		if (!this.weatherCities.size || !this.pageVisible || this.viewMode !== "calendar") return;
		const token = ++this.weatherRequestToken;
		if (this.weatherPollTimer) window.clearTimeout(this.weatherPollTimer);
		this.weatherPollTimer = null;
		try {
			await this.mapSettingsSaveQueue;
			const response = await frappe.call({
				method: `${FENGJING_CALENDAR_METHOD}.get_calendar_weather`,
				args: { refresh: refresh ? 1 : 0 },
			});
			if (token !== this.weatherRequestToken || !this.pageVisible || this.viewMode !== "calendar") return;
			const cities = response.message?.cities || [];
			this.weatherByCity = new Map(cities.map((city) => [city.city_key, city]));
			this.weatherLoaded = true;
			this.renderWeatherPreview();
			this.renderDailyWeather();
			if (attempt < 6 && cities.some((city) => !city.current)) {
				this.weatherPollTimer = window.setTimeout(() => this.loadWeather(false, attempt + 1), 5000);
			}
		} catch (error) {
			if (token === this.weatherRequestToken) this.showError("天气读取失败", error);
		}
	}

	renderDailyWeather() {
		if (!this.container) return;
		this.container.querySelectorAll(".fjtc-day-weather").forEach((node) => node.remove());
		if (this.calendar?.view.type !== "dayGridMonth" || !this.defaultWeatherCity) return;
		const cityKey = this.weatherViewActive ? this.weatherViewCity : this.defaultWeatherCity;
		const weather = this.weatherByCity.get(cityKey);
		if (!weather) return;
		const location = this.weatherCities.get(cityKey);
		const place = [location?.country, location?.region, location?.city].filter(Boolean).join(" · ");
		const byDate = new Map((weather.days || []).map((day) => [day.date, day]));
		for (const label of this.container.querySelectorAll(".fjtc-day-label")) {
			const day = byDate.get(label.closest("[data-date]")?.dataset.date);
			if (!day) continue;
			const badge = document.createElement("span");
			badge.className = "fjtc-day-weather";
			const icon = document.createElement("span");
			icon.className = "fjtc-day-weather-icon";
			icon.textContent = fengjingWeatherIcon(day.weather_code);
			const temperature = document.createElement("span");
			temperature.textContent = `${Math.round(Number(day.temperature_min_c))}～${Math.round(Number(day.temperature_max_c))}°`;
			badge.append(icon, temperature);
			badge.title = [place, day.kind === "history" ? "历史天气" : "天气预报", day.description,
				`最低 ${day.temperature_min_c}°C / 最高 ${day.temperature_max_c}°C`,
				day.precipitation_probability_pct != null ? `降水概率 ${day.precipitation_probability_pct}%` : "",
				day.wind_speed_max_kmh != null ? `最大风速 ${day.wind_speed_max_kmh} km/h` : "",
				`数据：${day.source_name || "天气服务"}`,
			].filter(Boolean).join(" · ");
			label.insertBefore(badge, label.lastChild);
		}
	}

	renderCityClocks() {
		const holder = this.container.querySelector(".fjtc-city-clocks");
		if (!holder) return;
		holder.replaceChildren();
		holder.hidden = this.timeCities.size === 0;
		for (const [key, location] of this.timeCities) {
			const clock = document.createElement("span");
			clock.className = "fjtc-city-clock";
			clock.dataset.cityKey = key;
			clock.dataset.timeZone = location.timeZone || "";
			clock.dataset.fullName = [location.country, location.region, location.city].filter(Boolean).join(" · ");
			const face = document.createElement("span");
			face.className = "fjtc-clock-face";
			face.setAttribute("aria-hidden", "true");
			for (let number = 1; number <= 12; number++) {
				const marker = document.createElement("span");
				const angle = number * Math.PI / 6;
				marker.className = "fjtc-clock-hour-number";
				marker.textContent = String(number);
				marker.style.left = `${50 + 35 * Math.sin(angle)}%`;
				marker.style.top = `${50 - 35 * Math.cos(angle)}%`;
				face.appendChild(marker);
			}
			for (const hand of ["hour", "minute", "second"]) {
				const node = document.createElement("span");
				node.className = `fjtc-clock-${hand}`;
				face.appendChild(node);
			}
			const dial = document.createElement("span");
			dial.className = "fjtc-clock-dial";
			const city = document.createElement("span");
			city.className = "fjtc-clock-city";
			const cityName = [
				String(location.region || "").replace(/(省|州)$/, ""), location.city,
			].filter(Boolean).join("·");
			const flag = fengjingCountryFlag(location.countryCode, location.country);
			if (flag) city.appendChild(flag);
			const cityLabel = document.createElement("span");
			cityLabel.className = "fjtc-clock-city-name";
			cityLabel.textContent = cityName;
			city.appendChild(cityLabel);
			const details = document.createElement("span");
			details.className = "fjtc-clock-details";
			const date = document.createElement("span");
			date.className = "fjtc-clock-date";
			date.textContent = "---- -- --";
			const time = document.createElement("span");
			time.className = "fjtc-clock-time";
			time.textContent = "-- --:--:--";
			dial.appendChild(face);
			details.append(time, date, city);
			clock.append(dial, details);
			holder.appendChild(clock);
		}
		this.updateCityClocks();
	}

	updateCityClocks() {
		this.updateBeijingDate();
		const now = new Date();
		for (const clock of this.container.querySelectorAll(".fjtc-city-clock")) {
			const zone = clock.dataset.timeZone;
			const date = clock.querySelector(".fjtc-clock-date");
			const time = clock.querySelector(".fjtc-clock-time");
			if (!zone) {
				clock.title = `${clock.dataset.fullName} · 未找到时区`;
				continue;
			}
			try {
				if (!this.clockFormatters.has(zone)) {
					this.clockFormatters.set(zone, new Intl.DateTimeFormat("zh-CN", {
						timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit", weekday: "short",
						hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
					}));
				}
				const parts = Object.fromEntries(this.clockFormatters.get(zone).formatToParts(now)
					.map(({ type, value }) => [type, value]));
				const hour = Number(parts.hour);
				const minute = Number(parts.minute);
				const second = Number(parts.second);
				const period = hour < 6 ? "凌晨" : hour < 12 ? "上午" : hour < 18 ? "下午" : "晚上";
				const weekday = String(parts.weekday || "").replace(/^(星期|礼拜)/, "周");
				date.textContent = `${parts.year}-${parts.month}-${parts.day} ${weekday}`;
				date.classList.toggle("is-weekend", weekday === "周六" || weekday === "周日");
				time.textContent = `${period} ${String(hour % 12 || 12).padStart(2, "0")}:${parts.minute}:${parts.second}`;
				clock.title = `${clock.dataset.fullName} · ${date.textContent} ${time.textContent} (${zone})`;
				clock.querySelector(".fjtc-clock-hour").style.transform =
					`rotate(${(hour % 12) * 30 + minute * 0.5 + second / 120}deg)`;
				clock.querySelector(".fjtc-clock-minute").style.transform =
					`rotate(${minute * 6 + second * 0.1}deg)`;
				clock.querySelector(".fjtc-clock-second").style.transform = `rotate(${second * 6}deg)`;
			} catch (error) {
				date.textContent = "---- -- --";
				date.classList.remove("is-weekend");
				time.textContent = "-- --:--:--";
				clock.title = `${clock.dataset.fullName} · 时区无效：${zone}`;
			}
		}
	}


	startCityClock() {
		if (this.clockTimer || !this.pageVisible || this.viewMode !== "calendar") return;
		this.clockTimer = window.setInterval(() => this.updateCityClocks(), 1000);
	}

	stopCityClock() {
		if (this.clockTimer) window.clearInterval(this.clockTimer);
		this.clockTimer = null;
	}

	refreshVisibleHolidays(attempt = 0) {
		const view = this.calendar?.view;
		if (view?.activeStart && view?.activeEnd) {
			this.loadHolidays({ view, start: view.activeStart, end: view.activeEnd }, attempt);
		}
	}

	async loadHolidays(info, attempt = 0) {
		const requestToken = ++this.holidayRequestToken;
		if (this.holidayPollTimer) window.clearTimeout(this.holidayPollTimer);
		this.holidayPollTimer = null;
		this.container.querySelectorAll(".fjtc-holiday-badges").forEach((node) => node.remove());
		if (this.viewMode !== "calendar" || info.view.type !== "dayGridMonth") return;
		try {
			// A setting changed immediately before returning from the map must be saved first.
			await this.mapSettingsSaveQueue;
			const response = await frappe.call({
				method: `${FENGJING_CALENDAR_METHOD}.get_calendar_holidays`,
				args: { start: this.dateOnly(info.start), end: this.dateOnly(info.end) },
			});
			if (requestToken !== this.holidayRequestToken || this.viewMode !== "calendar" ||
				this.calendar?.view.type !== "dayGridMonth") return;
			const result = response.message || {};
			this.renderHolidayBadges(result);
			if (result.sync_pending?.length && attempt < 18 && this.pageVisible) {
				this.holidayPollTimer = window.setTimeout(() => this.refreshVisibleHolidays(attempt + 1), 10000);
			}
		} catch (error) {
			if (requestToken === this.holidayRequestToken) this.showError("节假日读取失败", error);
		}
	}

	renderHolidayBadges(result) {
		const holidaysByDate = new Map();
		const countryOrder = new Map((result.countries || []).map((code, index) => [code, index]));
		for (const holiday of result.holidays || []) {
			const date = holiday.holiday_date;
			if (!holidaysByDate.has(date)) holidaysByDate.set(date, []);
			holidaysByDate.get(date).push(holiday);
		}
		for (const holidays of holidaysByDate.values()) {
			holidays.sort((left, right) =>
				(countryOrder.get(left.country_code) ?? Infinity) -
				(countryOrder.get(right.country_code) ?? Infinity));
		}
		for (const label of this.container.querySelectorAll(".fjtc-day-label")) {
			const holidays = holidaysByDate.get(label.closest("[data-date]")?.dataset.date);
			if (!holidays?.length) continue;
			const badges = document.createElement("span");
			badges.className = "fjtc-holiday-badges";
			for (const holiday of holidays) {
				const badge = document.createElement("span");
				badge.className = `fjtc-holiday-badge${holiday.day_type === "补班工作日" ? " is-makeup" : ""}`;
				const country = holiday.country_code === "CN" ? "中国" : holiday.country_name || holiday.country_code;
				const holidayName = holiday.country_code === "US"
					? FENGJING_US_HOLIDAY_NAMES[holiday.holiday_name] || holiday.holiday_name
					: holiday.holiday_name;
				const holidayLabel = holiday.day_type === "补班工作日"
					? `${holidayName}补班` : holidayName;
				const flag = fengjingCountryFlag(holiday.country_code, country);
				if (flag) badge.appendChild(flag);
				const text = document.createElement("span");
				text.className = "fjtc-holiday-badge-text";
				text.textContent = holidayLabel;
				badge.appendChild(text);
				badge.title = `${country} · ${holidayLabel}${holidayName !== holiday.holiday_name ? `（${holiday.holiday_name}）` : ""}`;
				badges.appendChild(badge);
			}
			label.prepend(badges);
		}
	}

	async loadEvents(info, success, failure) {
		try {
			const endInclusive = new Date(info.end.getTime() - 1);
			const response = await frappe.call({
				method: `${FENGJING_CALENDAR_METHOD}.get_calendar_events`,
				args: {
					start: this.dateOnly(info.start),
					end: this.dateOnly(endInclusive),
				},
			});
			success(response.message || []);
		} catch (error) {
			failure(error);
			this.showError("日程读取失败", error);
		}
	}

	selectionDefaults(info) {
		let end = info.end;
		if (info.allDay) end = new Date(info.end.getTime() - 1000);
		return {
			starts_on: this.dateTime(info.start),
			ends_on: this.dateTime(end),
			all_day: info.allDay ? 1 : 0,
		};
	}

	async openEventDialog(name = null, defaults = {}) {
		let event = {
			subject: "",
			starts_on: defaults.starts_on || this.dateTime(new Date()),
			ends_on: defaults.ends_on || this.dateTime(new Date(Date.now() + 60 * 60 * 1000)),
			all_day: defaults.all_day || 0,
			event_type: "Private",
			event_category: "Event",
			color: "",
			description: "",
			status: "Open",
			send_reminder: 1,
			custom_店铺: "",
			custom_物料: "",
			can_write: true,
			can_delete: false,
		};

		if (name) {
			try {
				const response = await frappe.call({
					method: `${FENGJING_CALENDAR_METHOD}.get_event`,
					args: { name },
				});
				event = { ...event, ...(response.message || {}) };
			} catch (error) {
				this.showError("日程读取失败", error);
				return;
			}
		}

		const readOnly = Boolean(name && !event.can_write);
		const dialog = new frappe.ui.Dialog({
			title: name ? "编辑日程" : "新建日程",
			size: "large",
			fields: [
				{ fieldname: "subject", fieldtype: "Small Text", label: "日程标题", reqd: 1, read_only: readOnly },
				{ fieldname: "custom_店铺", fieldtype: "Link", label: "店铺", options: "Cost Center", read_only: readOnly },
				{ fieldname: "custom_物料", fieldtype: "Link", label: "物料", options: "Item", read_only: readOnly },
				{ fieldname: "starts_on", fieldtype: "Datetime", label: "开始时间", reqd: 1, read_only: readOnly },
				{ fieldname: "ends_on", fieldtype: "Datetime", label: "结束时间", read_only: readOnly },
				{ fieldname: "time_column", fieldtype: "Column Break" },
				{ fieldname: "all_day", fieldtype: "Check", label: "全天日程", read_only: readOnly },
				{ fieldname: "send_reminder", fieldtype: "Check", label: "当天早上发送邮件提醒", read_only: readOnly },
				{ fieldname: "event_type", fieldtype: "Select", label: "可见范围", options: ["Private", "Public"], reqd: 1, read_only: readOnly },
				{ fieldname: "event_category", fieldtype: "Select", label: "日程类型", options: ["Event", "Meeting", "Call", "Sent/Received Email", "Other"], read_only: readOnly },
				{ fieldname: "status", fieldtype: "Select", label: "状态", options: ["Open", "Completed", "Closed", "Cancelled"], read_only: readOnly },
				{ fieldname: "color", fieldtype: "Color", label: "显示颜色", read_only: readOnly },
				{ fieldname: "description_section", fieldtype: "Section Break" },
				{ fieldname: "description", fieldtype: "Text Editor", label: "日程说明", read_only: readOnly },
			],
			primary_action_label: readOnly ? "关闭" : "保存",
			primary_action: async (values) => {
				if (readOnly) {
					dialog.hide();
					return;
				}
				try {
					// Frappe omits optional empty fields, so send an explicit blank to clear a saved color.
					const eventValues = { ...values, color: dialog.get_value("color") || "" };
					await frappe.call({
						method: `${FENGJING_CALENDAR_METHOD}.${name ? "update_event" : "create_event"}`,
						args: name ? { name, values: eventValues } : { values: eventValues },
						freeze: true,
						freeze_message: "正在保存日程…",
					});
					dialog.hide();
					this.calendar?.unselect();
					this.calendar?.refetchEvents();
					frappe.show_alert({ message: "日程已保存", indicator: "green" });
				} catch (error) {
					this.showError("日程保存失败", error);
				}
			},
			secondary_action_label: name ? "打开完整日程" : null,
			secondary_action: name
				? () => {
					dialog.hide();
					frappe.set_route("Form", "Event", name);
				}
				: null,
		});
		dialog.show();
		await dialog.set_values(event);

		if (name && event.can_delete) {
			const $delete = $('<button type="button" class="btn btn-danger btn-sm fjtc-delete-event">删除日程</button>');
			dialog.$wrapper.find(".modal-footer").prepend($delete);
			$delete.on("click", () => {
				frappe.confirm(`确定删除日程“${frappe.utils.escape_html(event.subject)}”吗？`, async () => {
					try {
						await frappe.call({
							method: `${FENGJING_CALENDAR_METHOD}.delete_event`,
							args: { name },
							freeze: true,
							freeze_message: "正在删除日程…",
						});
						dialog.hide();
						this.calendar?.refetchEvents();
						frappe.show_alert({ message: "日程已删除", indicator: "green" });
					} catch (error) {
						this.showError("日程删除失败", error);
					}
				});
			});
		}

		if (event.repeat_this_event) {
			dialog.set_message("这是重复日程。修改重复规则请打开完整日程表单。");
		}
	}

	async persistCalendarMove(info) {
		const event = info.event;
		try {
			await frappe.call({
				method: `${FENGJING_CALENDAR_METHOD}.move_event`,
				args: {
					name: event.extendedProps.event_name,
					...this.calendarEventTimes(event),
				},
				freeze: true,
				freeze_message: "正在更新日程时间…",
			});
			frappe.show_alert({ message: "日程时间已更新", indicator: "green" });
		} catch (error) {
			info.revert();
			this.showError("日程时间更新失败", error);
		}
	}

	calendarEventTimes(event) {
		let end = event.end;
		if (event.allDay) {
			end = new Date((end || new Date(event.start.getTime() + 24 * 60 * 60 * 1000)).getTime() - 1000);
		}
		return {
			starts_on: this.dateTime(event.start),
			ends_on: end ? this.dateTime(end) : null,
			all_day: event.allDay ? 1 : 0,
		};
	}

	dateOnly(date) {
		return `${date.getFullYear()}-${this.pad(date.getMonth() + 1)}-${this.pad(date.getDate())}`;
	}

	dateTime(date) {
		return `${this.dateOnly(date)} ${this.pad(date.getHours())}:${this.pad(date.getMinutes())}:${this.pad(date.getSeconds())}`;
	}

	pad(value) {
		return String(value).padStart(2, "0");
	}

	showError(title, error) {
		console.error(error);
		frappe.msgprint({
			title,
			message: error?.message || error?._server_messages || "请稍后重试或查看错误日志。",
			indicator: "red",
		});
	}
}
