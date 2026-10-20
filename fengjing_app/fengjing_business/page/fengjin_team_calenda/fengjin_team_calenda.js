const FENGJING_CALENDAR_METHOD =
	"fengjing_app.fengjing_business.page.fengjin_team_calenda.fengjin_team_calenda";
const FENGJING_CALENDAR_MAP_FILES = {
	countries: "/assets/fengjing_app/data/Vector_map/国家边界ne_10m_admin_0_countries_chn/ne_10m_admin_0_countries_chn.geojson",
	regions: "/assets/fengjing_app/data/Vector_map/省州边界ne_10m_admin_1_states_provinces/ne_10m_admin_1_states_provinces.geojson",
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
const FENGJING_CALENDAR_CHINA_SPECIAL_REGIONS = {
	HKG: { key: "CN-HK", name: "香港特别行政区", name_en: "Hong Kong" },
	MAC: { key: "CN-MO", name: "澳门特别行政区", name_en: "Macau" },
};

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
		this.weatherEnabled = false;
		this.weatherCity = null;
		this.activeCountry = null;
		this.selectedRegion = null;
		this.selectedCity = null;
		this.countryLoadToken = 0;
		this.countryMapLoaded = false;
		this.searchQuery = "";
		this.plottedCities = [];
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
				<div class="fjtc-settings" aria-label="地区设置" hidden>
					<div class="fjtc-settings-head">
						<h2>地区设置</h2>
						<button type="button" class="btn btn-default btn-sm" data-map-action="calendar">返回日历</button>
					</div>
					<div class="fjtc-settings-layout">
						<div class="fjtc-map-column">
							<div class="fjtc-map-toolbar">
								<button type="button" class="btn btn-default btn-sm" data-map-action="world">世界地图</button>
								<span class="fjtc-map-breadcrumb">世界</span>
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
		this.$main.on("click", "[data-map-action='world']", () => this.renderWorldMap());
		this.$main.on("click", "[data-city-key]", (event) => this.selectCity($(event.currentTarget).attr("data-city-key"), true));
		this.$main.on("click", "[data-region-key]", (event) => this.selectRegion($(event.currentTarget).attr("data-region-key"), true));
		this.$main.on("change", "[data-map-setting]", (event) => this.changeMapSetting(event));
		this.$main.on("input", ".fjtc-city-search", (event) => this.renderCityResults(event.currentTarget.value));
		this.renderSettingsPanel();
	}

	addActions() {
		this.page.set_primary_action("新建日程", () => this.openEventDialog(), "add");
		this.page.add_inner_button("刷新", () => this.calendar?.refetchEvents());
		this.page.add_inner_button("日程列表", () => frappe.set_route("List", "Event"));
		this.page.add_inner_button("设置", () => this.openSettings());
	}

	async show() {
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
		} catch (error) {
			this.showError("日历加载失败", error);
		}
	}

	hide() {
		document.body.classList.remove("fjtc-full-height-page");
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
		this.viewMode = "settings";
		this.$main.find(".fjtc-shell").addClass("fjtc-shell--settings");
		this.$main.find(".fjtc-calendar, .fjtc-loading").hide();
		this.container.style.setProperty("display", "none", "important");
		this.$main.find(".fjtc-settings").prop("hidden", false);
		this.setMapStatus("正在加载离线地图…");
		try {
			await this.renderWorldMap();
		} catch (error) {
			this.setMapStatus("地图加载失败，请检查地图文件是否已发布到 /assets/fengjing_app/data/Vector_map。");
			this.showError("地图加载失败", error);
		}
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
		} catch (error) {
			this.showError("日历加载失败", error);
		}
	}

	loadMapData(kind) {
		if (!this.mapDataPromises[kind]) {
			this.mapDataPromises[kind] = fetch(encodeURI(FENGJING_CALENDAR_MAP_FILES[kind]), {
				credentials: "same-origin",
			}).then((response) => {
				if (!response.ok) throw new Error(`${kind} 地图文件读取失败（HTTP ${response.status}）`);
				return response.json();
			}).catch((error) => {
				delete this.mapDataPromises[kind];
				throw error;
			});
		}
		return this.mapDataPromises[kind];
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
		this.countryLoadToken += 1;
		this.activeCountry = null;
		this.selectedRegion = null;
		this.selectedCity = null;
		this.weatherCity = null;
		this.weatherEnabled = false;
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
		const bounds = index.reduce((result, polygon) => ({
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
		if (Number.isFinite(zoom) && zoom > 0) this.mapChart.setOption({ geo: { zoom } });
	}

	async openCountry(country) {
		if (!country) return;
		const token = ++this.countryLoadToken;
		this.activeCountry = country;
		this.selectedRegion = null;
		this.selectedCity = null;
		this.weatherCity = null;
		this.weatherEnabled = false;
		this.searchQuery = "";
		this.countryMapLoaded = false;
		this.regionLookup.clear();
		this.cityLookup.clear();
		this.cityRegionKeys.clear();
		this.plottedCities = [];
		this.renderSettingsPanel();
		this.$main.find(".fjtc-map-breadcrumb").text(country.properties.name);
		this.setMapStatus(`正在加载${country.properties.name}的省州和城市…`);
		try {
			const isChina = country.properties.ADM0_A3 === "CHN";
			const [regions, cities, taiwanProvince] = await Promise.all([
				this.loadMapData("regions"),
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
					labelLayout: { hideOverlap: true },
					z: 20, symbolSize: 9,
					itemStyle: { color: "#eab308", borderColor: "#fff", borderWidth: 1.5 },
					label: { show: true, position: "right", color: "#713f12", fontSize: 12,
						fontWeight: 600, textBorderColor: "#fff", textBorderWidth: 3,
						formatter: (params) => [params.data.regionName, params.data.name].filter(Boolean).join(" · ") },
					emphasis: { scale: 1.5 },
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
		this.selectedRegion = region;
		this.selectedCity = null;
		this.weatherCity = null;
		this.weatherEnabled = false;
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
			this.weatherCity = null;
			this.weatherEnabled = false;
			if (!preserveSearch) this.searchQuery = "";
			this.renderSettingsPanel();
			this.updateMapHighlights();
			return;
		}
		this.selectedCity = city;
		this.selectedRegion = this.regionLookup.get(this.cityRegionKeys.get(String(key))) || null;
		if (!preserveSearch) this.searchQuery = "";
		this.weatherCity = {
			key: String(city.properties.NE_ID),
			name: city.properties.name,
			country: this.activeCountry.properties.name,
		};
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
			if (!plotted.has(cityKey)) plotted.set(cityKey, {
				name: city.properties.name, cityKey,
				regionName: this.regionLookup.get(this.cityRegionKeys.get(cityKey))?.properties.name || "",
				value: [Number(city.properties.LONGITUDE), Number(city.properties.LATITUDE)],
			});
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
			if (checked) this.holidayCountries.set(key, this.activeCountry.properties.name);
			else this.holidayCountries.delete(key);
		} else if (mapSetting === "weather") {
			this.weatherEnabled = checked;
		}
		this.renderSettingsPanel();
	}

	renderSettingsPanel() {
		const country = this.activeCountry;
		const region = this.selectedRegion;
		const escaped = (value) => this.escapeHtml(value);
		const countryKey = country && String(country.properties.NE_ID);
		const weatherLocation = this.selectedCity
			? `${region?.properties.name || country?.properties.name || ""} / ${this.selectedCity.properties.name}`
			: "";
		this.$main.find(".fjtc-settings-panel").html(`
			<h3>选择地区</h3>
			<label class="fjtc-setting-row"><span><strong>${country ? escaped(country.properties.name) : "国家节假日"}</strong><small>${country ? "国家节假日" : "请先在地图中选择国家"}</small></span><input type="checkbox" data-map-setting="country" ${this.holidayCountries.has(countryKey) ? "checked" : ""} ${country ? "" : "disabled"}></label>
			${this.selectedCity ? `<label class="fjtc-setting-row"><span><strong>省州 / 城市天气</strong><small>${escaped(weatherLocation)}</small></span><input type="checkbox" data-map-setting="weather" ${this.weatherEnabled ? "checked" : ""}></label>` : ""}
			${country ? `<div class="fjtc-city-picker"><label for="fjtc-city-search">搜索省州或城市</label><input id="fjtc-city-search" type="search" class="form-control fjtc-city-search" placeholder="输入省州或城市名称" value="${escaped(this.searchQuery)}"><div class="fjtc-city-results"></div></div>` : ""}
		`);
		if (country) this.renderCityResults(this.searchQuery);
	}

	renderCityResults(query) {
		const term = String(query || "").trim().toLocaleLowerCase();
		this.searchQuery = String(query || "");
		const regions = term ? [...this.regionLookup].filter(([, region]) =>
			[region.properties.name, region.properties.name_en, region.properties.iso_3166_2]
				.some((name) => String(name || "").toLocaleLowerCase().includes(term))) : [];
		const cities = [...this.cityLookup.values()]
			.filter((city) => term || !this.selectedRegion ||
				this.cityRegionKeys.get(String(city.properties.NE_ID)) === String(this.selectedRegion.properties.ne_id))
			.filter((city) => !term || [city.properties.name, city.properties.NAME_EN]
				.some((name) => String(name || "").toLocaleLowerCase().includes(term)))
			.sort((a, b) => (Number(b.properties.POP_MAX) || 0) - (Number(a.properties.POP_MAX) || 0))
			.slice(0, Math.max(0, 12 - regions.length));
		const results = [
			...regions.slice(0, 12).map(([key, region]) => `<button type="button" class="fjtc-city-result${String(this.selectedRegion?.properties.ne_id) === key ? " is-selected" : ""}" data-region-key="${key}">${this.escapeHtml(region.properties.name)}<small>省州</small></button>`),
			...cities.map((city) => `<button type="button" class="fjtc-city-result${String(this.selectedCity?.properties.NE_ID) === String(city.properties.NE_ID) ? " is-selected" : ""}" data-city-key="${String(city.properties.NE_ID)}">${this.escapeHtml(city.properties.name)}<small>${this.escapeHtml(city.properties.NAME_EN || "")}</small></button>`),
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
			height: "auto",
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
				left: "prev,next today",
				center: "title",
				right: "dayGridMonth,timeGridWeek,timeGridDay,listMonth",
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
					await frappe.call({
						method: `${FENGJING_CALENDAR_METHOD}.${name ? "update_event" : "create_event"}`,
						args: name ? { name, values } : { values },
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
