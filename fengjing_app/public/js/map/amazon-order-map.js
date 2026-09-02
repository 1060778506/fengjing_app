(function () {
	const countryCodes = { 美国: "US", 加拿大: "CA", 墨西哥: "MX", 巴西: "BR", 英国: "GB", 德国: "DE", 日本: "JP" };
	const countryFlag = country => {
		const code = countryCodes[country] || "";
		const shapes = {
			US: '<rect width="36" height="24" fill="#fff"/><path d="M0 0h36v3H0zm0 6h36v3H0zm0 6h36v3H0zm0 6h36v3H0" fill="#d8293d"/><rect width="16" height="12" fill="#23488f"/><g fill="#fff"><circle cx="3" cy="3" r="1"/><circle cx="8" cy="3" r="1"/><circle cx="13" cy="3" r="1"/><circle cx="5.5" cy="7" r="1"/><circle cx="10.5" cy="7" r="1"/></g>',
			CA: '<rect width="36" height="24" fill="#fff"/><path d="M0 0h9v24H0zm27 0h9v24h-9z" fill="#d8293d"/><path d="m18 5 2 4 3-1-2 4 3 2-5 1 1 5h-4l1-5-5-1 3-2-2-4 3 1z" fill="#d8293d"/>',
			MX: '<path d="M0 0h12v24H0z" fill="#168653"/><path d="M12 0h12v24H12z" fill="#fff"/><path d="M24 0h12v24H24z" fill="#d62b3f"/><circle cx="18" cy="12" r="2.3" fill="#9a7835"/>',
			BR: '<rect width="36" height="24" fill="#168b50"/><path d="m18 3 14 9-14 9L4 12z" fill="#f6d34a"/><circle cx="18" cy="12" r="5" fill="#24539a"/><path d="M13 11c4-1 7 0 10 2" stroke="#fff" fill="none"/>',
			GB: '<rect width="36" height="24" fill="#24498c"/><path d="M0 0l36 24M36 0 0 24" stroke="#fff" stroke-width="5"/><path d="M0 0l36 24M36 0 0 24" stroke="#d8293d" stroke-width="2"/><path d="M18 0v24M0 12h36" stroke="#fff" stroke-width="7"/><path d="M18 0v24M0 12h36" stroke="#d8293d" stroke-width="4"/>',
			DE: '<path d="M0 0h36v8H0z" fill="#181818"/><path d="M0 8h36v8H0z" fill="#d8293d"/><path d="M0 16h36v8H0z" fill="#f5cf45"/>',
			JP: '<rect width="36" height="24" fill="#fff"/><circle cx="18" cy="12" r="6" fill="#d8293d"/>',
		};
		return shapes[code] ? `<span class="fom-country-flag"><svg viewBox="0 0 36 24" aria-label="${code}">${shapes[code]}</svg></span>` : `<span class="fom-country-code">${frappe.utils.escape_html(code || String(country || "--").slice(0, 2))}</span>`;
	};
	window.fengjingCountryFlag = countryFlag;

	class FengjingOrderMap {
		constructor(root, filters) {
			this.root = root;
			this.filters = filters || {};
			this.mode = "online";
			this.textureIndex = 0;
			this.points = [];
			this.textures = [];
			this.chart = null;
			this.viewer = null;
			this.warehouses = [];
			this.warehousesLoaded = false;
			this.orderEntities = [];
			this.warehouseEntities = [];
			this.onlineLayers = {};
			this.textureImages = new Map();
			this.textureCacheName = "fengjing-offline-globe-v1";
			this.destroyed = false;
			this.renderShell();
		}

		async load() {
			this.setStatus("正在匹配订单邮编与地图坐标…");
			const result = await frappe.call({
				method: "fengjing_app.fengjing_business.page.amazon_order_display.amazon_order_display.get_order_map_data",
				args: { filters: this.filters },
			});
			const data = result.message || {};
			this.points = data.points || [];
			this.textures = data.textures || [];
			this.mapStats = data;
			this.root.querySelector("[data-map-count]").textContent = `${data.mapped_orders || 0}/${data.total_orders || 0} 单 · ${this.points.length} 个地点`;
			this.renderSummaryCards(data);
			this.upgradeMoneyFlags();
			this.warmTextureCache();
			await this.showOnline();
		}

		renderShell() {
			this.root.innerHTML = `
				<div class="fom-toolbar">
					<div class="fom-modes"><button data-mode="online" class="active">在线地图</button><button data-mode="offline">离线地球</button></div>
					<div class="fom-controls"><select data-texture data-offline></select><button data-prev data-offline title="也可按键盘左方向键">上一款</button><button data-next data-offline title="也可按键盘右方向键">下一款</button><label><input data-orders type="checkbox" checked> 订单柱</label><label><input data-warehouses type="checkbox"> 亚马逊仓库</label><label data-offline><input data-rotate type="checkbox" checked> 自动旋转</label><span class="fom-overlay-title" data-online>叠加信息</span><label data-online><input data-overlay="weather" type="checkbox"> 天气雷达</label><label data-online><input data-overlay="places" type="checkbox"> 地名</label><label data-online><input data-overlay="transport" type="checkbox"> 道路交通</label><label data-online><input data-overlay="boundaries" type="checkbox"> 行政边界</label><label data-online><input data-overlay="alternate" type="checkbox"> 备用标注</label><button data-street data-online>街景工具</button><button data-fullscreen title="在当前页面全屏">⛶ 全屏</button></div>
					<div class="fom-status"><i></i><span data-map-status>准备地图</span><b data-map-count>0 个地理点</b></div>
				</div>
				<div class="fom-stage"><div class="fom-offline"></div><div class="fom-online"></div><div class="fom-hud"><b>AMAZON GLOBAL ORDERS</b><span>订单地理态势</span></div><aside class="fom-map-popup" data-map-popup><header><b data-popup-title>地图信息</b><button data-popup-close type="button" aria-label="关闭">×</button></header><div data-popup-content></div></aside></div>`;
			this.bind();
		}

		bind() {
			this.root.querySelectorAll("[data-mode]").forEach(button => button.addEventListener("click", async () => {
				this.root.querySelectorAll("[data-mode]").forEach(item => item.classList.remove("active"));
				button.classList.add("active");
				if (button.dataset.mode === "online") await this.showOnline(); else await this.showOffline();
			}));
			this.root.querySelector("[data-prev]").addEventListener("click", () => this.changeTexture(-1));
			this.root.querySelector("[data-next]").addEventListener("click", () => this.changeTexture(1));
			this.root.querySelector("[data-texture]").addEventListener("change", event => { this.textureIndex = +event.target.value; this.drawOffline(); });
			this.root.querySelector("[data-orders]").addEventListener("change", event => this.toggleOrders(event.target.checked));
			this.root.querySelector("[data-warehouses]").addEventListener("change", event => this.toggleWarehouses(event.target.checked));
			this.root.querySelectorAll("[data-overlay]").forEach(input => input.addEventListener("change", event => this.toggleOverlay(input.dataset.overlay, event.target.checked)));
			this.root.querySelector("[data-rotate]").addEventListener("change", event => this.toggleRotation(event.target.checked));
			this.root.querySelector("[data-street]").addEventListener("click", () => this.enableStreetView());
			this.root.querySelector("[data-fullscreen]").addEventListener("click", () => this.toggleFullscreen());
			this.root.querySelector("[data-popup-close]").addEventListener("click", () => this.hideMapPopup());
			this.keyHandler = event => {
				if (this.mode !== "offline" || !this.root.isConnected || this.root.offsetParent === null) return;
				if (/^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) || event.target.isContentEditable) return;
				if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
					event.preventDefault();
					this.changeTexture(event.key === "ArrowLeft" ? -1 : 1);
				}
			};
			document.addEventListener("keydown", this.keyHandler);
			this.fullscreenHandler = () => { const button=this.root.querySelector("[data-fullscreen]");if(button)button.textContent=document.fullscreenElement?"⛶ 退出全屏":"⛶ 全屏";setTimeout(() => { if(this.chart)this.chart.resize();if(this.viewer)this.viewer.resize(); }, 120); };
			document.addEventListener("fullscreenchange", this.fullscreenHandler);
		}

		async showOffline() {
			this.mode = "offline";
			this.hideMapPopup();
			this.root.querySelectorAll("[data-offline]").forEach(element => element.style.display = "");
			this.root.querySelectorAll("[data-online]").forEach(element => element.style.display = "none");
			this.root.querySelector(".fom-offline").style.display = "block";
			this.root.querySelector(".fom-online").style.display = "none";
			this.root.querySelector("[data-texture]").disabled = false;
			if (!this.textures.length) { this.setStatus("没有找到本地地球蒙皮"); return; }
			const select = this.root.querySelector("[data-texture]");
			if (!select.options.length) select.innerHTML = this.textures.map((item, index) => `<option value="${index}">${frappe.utils.escape_html(item.name)}</option>`).join("");
			await this.ensureEchartsGl();
			await this.drawOffline();
		}

		async ensureEchartsGl() {
			if (window.__fengjing_echarts_gl_loaded) return;
			await new Promise(resolve => frappe.require("assets/fengjing_app/js/map/engines/echarts-gl.js", resolve));
			window.__fengjing_echarts_gl_loaded = true;
		}

		async drawOffline() {
			if (this.mode !== "offline" || !this.textures.length) return;
			const dom = this.root.querySelector(".fom-offline");
			const texture = this.textures[this.textureIndex] || this.textures[0];
			const requestedIndex = this.textureIndex;
			this.setStatus(`正在加载离线地球 · ${texture.name}`);
			const textureImage = await this.preloadTexture(requestedIndex);
			if (this.mode !== "offline" || requestedIndex !== this.textureIndex || !textureImage) return;
			if (this.chart) this.chart.dispose();
			this.chart = echarts.init(dom);
			const night = /夜|night/i.test(texture.name);
			const maxOrders = Math.max(1, ...this.points.map(point => Number(point.orders) || 0));
			const heightFor = orders => 1.35 + (Math.log1p(Number(orders) || 0) / Math.log1p(maxOrders)) * 1.65;
			const data = this.points.map(point => [point.longitude, point.latitude, heightFor(point.orders), point.orders, point]);
			this.offlineData = data;
			const warehouseData = this.warehouses.map(point => [point.longitude, point.latitude, 1.8, 1, { ...point, _kind: "warehouse" }]);
			this.offlinePointData = this.points.map(point => [point.longitude, point.latitude, 1, point.orders, point]);
			this.offlineWarehouseData = warehouseData;
			const ordersVisible = this.root.querySelector("[data-orders]").checked;
			const warehousesVisible = this.root.querySelector("[data-warehouses]").checked;
			this.chart.setOption({
				backgroundColor: "/assets/fengjing_app/js/map/images/space-8k.jpg",
				tooltip: { formatter: p => { const d = p.value[4]; return d?._kind === "warehouse" ? this.warehouseDetailsHTML(d) : `<b>${frappe.utils.escape_html(d.country || "")}</b><br>${frappe.utils.escape_html([d.region,d.city].filter(Boolean).join(" · "))}<br>${frappe.utils.escape_html(d.precision || "地图定位")}${d.postal_code?` · 邮编 ${frappe.utils.escape_html(d.postal_code)}`:""}<br>订单：${d.orders}`; } },
				globe: { baseTexture: textureImage, environment: "/assets/fengjing_app/js/map/images/space-8k.jpg", shading: "realistic", realisticMaterial: { roughness: .58 }, light: { main: { intensity: 1.2, shadow: true }, ambient: { intensity: .72 } }, atmosphere: { show: true, color: night ? "#071b4b" : "#4aa5d8" }, viewControl: { autoRotate: this.root.querySelector("[data-rotate]").checked, distance: 195, minDistance: 70, maxDistance: 460 } },
				series: [
					{ id: "orders-bars", name: "订单光柱", type: "bar3D", coordinateSystem: "globe", data: ordersVisible ? data : [], barSize: .42, bevelSize: .08, bevelSmoothness: 4, shading: "realistic", realisticMaterial: { roughness: .25, metalness: .15 }, itemStyle: { color: night ? "#ff4f91" : "#15d6e8", opacity: .84 }, emphasis: { itemStyle: { color: "#ffe66d", opacity: 1 }, label: { show: true } }, label: { show: false, formatter: p => `${p.value[3]} 单`, textStyle: { color: "#fff", fontSize: 11, backgroundColor: "rgba(5,12,30,.82)", padding: [4,7], borderRadius: 5 } } },
					{ id: "orders-points", name: "订单光点", type: "scatter3D", coordinateSystem: "globe", data: ordersVisible ? this.offlinePointData : [], symbol: "circle", symbolSize: value => 7 + Math.min(12, Math.sqrt(value[3] || 1) * 1.8), itemStyle: { color: night ? "#ff80b5" : "#a7fbff", opacity: .96, borderColor: "#fff", borderWidth: 1.5 } },
					{ id: "amazon-warehouses", name: "亚马逊仓库", type: "scatter3D", coordinateSystem: "globe", data: warehousesVisible ? warehouseData : [], symbol: "circle", symbolSize: 11, itemStyle: { color: "#ffb020", opacity: .98, borderColor: "#fff", borderWidth: 2 }, emphasis: { itemStyle: { color: "#ffe16a" }, label: { show: true, formatter: p => p.value[4].name || "Amazon 仓库", textStyle: { color: "#fff", backgroundColor: "rgba(27,19,4,.88)", padding: [5,8], borderRadius: 5 } } } },
				],
			}, true);
			this.chart.on("click", params => { const point=params.value&&params.value[4];if(point?._kind === "warehouse")this.showWarehouseDetails(point);else if(point)this.showPointDetails(point); });
			this.root.querySelector("[data-texture]").value = String(this.textureIndex);
			this.setStatus(`离线地球 · ${texture.name}`);
			this.preloadAdjacentTextures();
		}

		async changeTexture(step) {
			if (!this.textures.length) return;
			this.hideMapPopup();
			this.textureIndex = (this.textureIndex + step + this.textures.length) % this.textures.length;
			await this.drawOffline();
		}

		async cacheTextureFile(index) {
			const texture = this.textures[(index + this.textures.length) % this.textures.length];
			if (!texture) return null;
			if (!("caches" in window)) return fetch(texture.path, { cache: "force-cache" });
			const cache = await caches.open(this.textureCacheName);
			let response = await cache.match(texture.path);
			if (!response) {
				response = await fetch(texture.path, { cache: "force-cache" });
				if (response.ok) await cache.put(texture.path, response.clone());
			}
			return response;
		}

		async preloadTexture(index) {
			if (!this.textures.length) return null;
			index = (index + this.textures.length) % this.textures.length;
			if (this.textureImages.has(index)) return this.textureImages.get(index).promise;
			const record = { url: "", promise: null };
			record.promise = (async () => {
				try {
					const response = await this.cacheTextureFile(index);
					if (!response || !response.ok) throw new Error("地图蒙皮读取失败");
					const blob = await response.blob();
					record.url = URL.createObjectURL(blob);
					const image = new Image();
					image.decoding = "async";
					image.src = record.url;
					if (image.decode) await image.decode(); else await new Promise((resolve, reject) => { image.onload=resolve;image.onerror=reject; });
					return image;
				} catch (error) {
					console.warn("离线地图蒙皮预加载失败", error);
					return null;
				}
			})();
			this.textureImages.set(index, record);
			return record.promise;
		}

		preloadAdjacentTextures() {
			if (!this.textures.length) return;
			const keep = new Set([
				this.textureIndex,
				(this.textureIndex - 1 + this.textures.length) % this.textures.length,
				(this.textureIndex + 1) % this.textures.length,
			]);
			keep.forEach(index => this.preloadTexture(index));
			for (const [index, record] of this.textureImages) {
				if (!keep.has(index)) {
					if (record.url) URL.revokeObjectURL(record.url);
					this.textureImages.delete(index);
				}
			}
		}

		warmTextureCache() {
			if (!this.textures.length) return;
			this.preloadAdjacentTextures();
			const indexes = this.textures.map((_, index) => index).filter(index => ![0, 1, this.textures.length - 1].includes(index));
			setTimeout(async () => {
				for (const index of indexes) {
					if (this.destroyed) return;
					try { await this.cacheTextureFile(index); } catch (error) { console.warn("离线地图后台缓存失败", error); }
					await new Promise(resolve => setTimeout(resolve, 120));
				}
			}, 1200);
		}

		renderSummaryCards(data) {
			const page = this.root.closest(".page-container") || document;
			const render = (selector, rows, kind) => {
				const panel = page.querySelector(selector);
				if (!panel) return;
				const max = Math.max(1, ...(rows || []).map(row => +row.orders || 0));
				panel.innerHTML = `<div class="aod-dist">${(rows || []).map((row, index) => `<div class="aod-dist-row"><i>${kind === "country" ? countryFlag(row.name) : String(index + 1).padStart(2, "0")}</i><span><b title="${frappe.utils.escape_html(row.name || "")}">${frappe.utils.escape_html(row.name || "")}</b><small>${(+row.orders || 0).toLocaleString("zh-CN")} 个订单 · ${(+row.items || 0).toLocaleString("zh-CN")} 件商品</small><em><u style="width:${(+row.orders || 0) / max * 100}%"></u></em></span></div>`).join("") || '<div class="aod-empty">当前条件没有数据</div>'}</div>`;
				const note = panel.closest("article")?.querySelector("header span");
				if (note) note.textContent = `${(rows || []).length} 项 · ${(rows || []).reduce((sum, row) => sum + (+row.orders || 0), 0).toLocaleString("zh-CN")} 个订单`;
			};
			render("#aod-country", data.country_summary, "country");
			render("#aod-store", data.store_summary, "store");
		}

		upgradeMoneyFlags() {
			const page = this.root.closest(".page-container") || document;
			page.querySelectorAll(".aod-money-row").forEach(row => {
				const country = row.querySelector(".aod-money-country b")?.textContent?.trim();
				const icon = row.querySelector(".aod-money-country > i");
				if (country && icon) icon.innerHTML = countryFlag(country);
			});
		}

		pointDetailsHTML(point) {
			const esc = value => frappe.utils.escape_html(String(value || ""));
			const statusMap = { Shipped: "已发货", Unshipped: "待发货", Pending: "待处理", Canceled: "已取消", Cancelled: "已取消", PartiallyShipped: "部分发货" };
			const productMap = new Map();
			(point.order_samples || []).forEach(order => (order.product_lines || []).forEach(line => {
				const key = line.sku || line.asin || line.item_code || line.product_name || "未识别商品";
				const product = productMap.get(key) || { ...line, quantity: 0 };
				product.quantity += Number(line.quantity || 0);
				productMap.set(key, product);
			}));
			const products = [...productMap.values()].sort((a, b) => b.quantity - a.quantity), maxQuantity = Math.max(1, ...products.map(item => item.quantity));
			const productChart = products.map(product => `<div style="display:grid;grid-template-columns:42px minmax(0,1fr) 52px;align-items:center;gap:8px;margin:7px 0">${product.item_image ? `<img src="${esc(product.item_image)}" alt="" style="width:40px;height:40px;border-radius:8px;object-fit:cover;background:#fff">` : '<i style="display:grid;place-items:center;width:40px;height:40px;border-radius:8px;background:#edf3ff;color:#3567c9;font-style:normal">物</i>'}<span style="min-width:0"><b style="display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(product.item_name || product.product_name || product.sku || "未绑定物料")}</b><small style="display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">SKU ${esc(product.sku || "—")} · ${esc(product.item_code || "未绑定ERPNext物料")}</small><em style="display:block;height:6px;margin-top:5px;border-radius:6px;background:#233657;overflow:hidden"><u style="display:block;width:${Math.max(3, product.quantity / maxQuantity * 100)}%;height:100%;border-radius:6px;background:linear-gradient(90deg,#34d5d0,#5b8cff)"></u></em></span><b style="text-align:right;color:#63e8dc">${Number(product.quantity).toLocaleString("zh-CN")} 件</b></div>`).join("");
			const samples = (point.order_samples || []).map(order => {
				const productLines = (order.product_lines || []).map(line => `<span style="display:flex;align-items:center;gap:7px;min-width:0;max-width:100%">${line.item_image ? `<img src="${esc(line.item_image)}" alt="" style="flex:0 0 34px;width:34px;height:34px;border-radius:7px;object-fit:cover;background:#fff">` : '<i style="display:grid;place-items:center;flex:0 0 34px;width:34px;height:34px;border-radius:7px;background:#edf3ff;color:#3567c9;font-style:normal">物</i>'}<span style="min-width:0;word-break:break-word"><b>${esc(line.item_name || line.product_name || line.sku || "未绑定物料")}</b><small style="display:block">SKU ${esc(line.sku || "—")} · ASIN ${esc(line.asin || "—")} · ${Number(line.quantity || 0).toLocaleString("zh-CN")} 件</small></span></span>`).join("") || '<span style="color:#91a0b7">未返回商品明细</span>';
				return `<article style="padding:9px 0;border-bottom:1px solid rgba(132,161,204,.2);overflow:hidden"><div style="display:flex;align-items:center;justify-content:space-between;gap:10px"><span style="min-width:0"><b style="word-break:break-all">${esc(order.amazon_order_id)}</b><small style="display:block">${esc(order.store || "—")}</small></span><span style="flex:0 0 auto;text-align:right"><b>${esc(statusMap[order.status] || order.status || "未知")}</b><small style="display:block">${esc(order.currency || "")} ${Number(order.amount || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</small></span></div><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:8px;margin-top:7px">${productLines}</div></article>`;
			}).join("");
			return `<div style="width:100%;max-width:100%;overflow:hidden"><b>${esc(point.country || "订单位置")}</b><p>${esc([point.region, point.city].filter(Boolean).join(" · "))}<br>${esc(point.precision || "地图定位")}${point.postal_code ? ` · 邮编 ${esc(point.postal_code)}` : ""}<br>店铺：${esc((point.stores || []).join("、") || "—")} · 共 ${+point.orders || 0} 个订单</p>${productChart ? `<section style="padding:10px 12px;margin:10px 0;border-radius:10px;background:rgba(31,54,90,.72)"><b style="color:#fff">该地点商品销量</b>${productChart}</section>` : ""}<section style="max-height:430px;overflow-y:auto;overflow-x:hidden">${samples}</section></div>`;
		}

		showPointDetails(point) {
			if (this.mode === "offline") {
				this.showMapPopup(`${point.country || "地图位置"} · ${point.orders || 0} 个订单`, this.pointDetailsHTML(point));
				return;
			}
			frappe.msgprint({ title: `${point.country || "地图位置"} · ${point.orders || 0} 个订单`, message: this.pointDetailsHTML(point), wide: true });
		}

		warehouseDetailsHTML(point) {
			const esc = value => frappe.utils.escape_html(String(value || ""));
			return `<div style="min-width:280px"><b style="color:#d78400">${esc(point.name || "Amazon 仓库")}</b><p>运营方：${esc(point.operator || "Amazon")}<br>编号：${esc(point.reference || "—")}<br>位置：${esc([point.city, point.region, point.country_code].filter(Boolean).join(" · ") || "公开地图未提供")}<br>邮编：${esc(point.postcode || "—")}</p><small>位置来源：OpenStreetMap / Overpass，可能并非Amazon完整官方仓库清单。</small></div>`;
		}

		showWarehouseDetails(point) {
			if (this.mode === "offline") {
				this.showMapPopup(point.name || "Amazon 仓库", this.warehouseDetailsHTML(point));
				return;
			}
			frappe.msgprint({ title: point.name || "Amazon 仓库", message: this.warehouseDetailsHTML(point), wide: true });
		}

		showMapPopup(title, html) {
			const popup = this.root.querySelector("[data-map-popup]");
			popup.querySelector("[data-popup-title]").textContent = title;
			popup.querySelector("[data-popup-content]").innerHTML = html;
			popup.classList.add("is-open");
		}

		hideMapPopup() {
			this.root.querySelector("[data-map-popup]")?.classList.remove("is-open");
		}

		async showOnline() {
			this.mode = "online";
			this.hideMapPopup();
			this.root.querySelectorAll("[data-offline]").forEach(element => element.style.display = "none");
			this.root.querySelectorAll("[data-online]").forEach(element => element.style.display = "");
			this.root.querySelector(".fom-offline").style.display = "none";
			this.root.querySelector(".fom-online").style.display = "block";
			this.root.querySelector("[data-texture]").disabled = true;
			try {
				await this.ensureCesium();
				if (!this.viewer) this.initCesium();
				this.setStatus("在线卫星地图 · ArcGIS / OpenStreetMap");
			} catch (error) {
				this.setStatus("外网地图加载失败，已切回离线地球");
				this.root.querySelector('[data-mode="offline"]').click();
			}
		}

		ensureCesium() {
			if (window.Cesium) return Promise.resolve();
			window.CESIUM_BASE_URL = "https://cesium.com/downloads/cesiumjs/releases/1.115/Build/Cesium/";
			if (!document.querySelector("link[data-fom-cesium]")) { const link=document.createElement("link"); link.rel="stylesheet"; link.dataset.fomCesium="1"; link.href=window.CESIUM_BASE_URL+"Widgets/widgets.css"; document.head.appendChild(link); }
			return new Promise((resolve,reject)=>{const existing=document.querySelector("script[data-fom-cesium]");if(existing){existing.addEventListener("load",resolve,{once:true});existing.addEventListener("error",reject,{once:true});return;}const script=document.createElement("script");script.dataset.fomCesium="1";script.src=window.CESIUM_BASE_URL+"Cesium.js";script.onload=resolve;script.onerror=reject;document.head.appendChild(script);});
		}

		initCesium() {
			const Cesium = window.Cesium, container = this.root.querySelector(".fom-online");
			const providerIcon = (background, label) => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="${background}"/><circle cx="32" cy="31" r="20" fill="none" stroke="white" stroke-width="3"/><path d="M12 31h40M32 11c7 7 10 13 10 20s-3 14-10 20M32 11c-7 7-10 13-10 20s3 14 10 20" fill="none" stroke="white" stroke-width="2"/><text x="32" y="37" fill="white" font-size="15" font-family="sans-serif" font-weight="700" text-anchor="middle">${label}</text></svg>`)}`;
			const customImageryModels = [
				new Cesium.ProviderViewModel({
					name: "ArcGIS 世界影像",
					tooltip: "Esri World Imagery 卫星影像",
					iconUrl: providerIcon("#1868a8", "卫"),
					creationFunction: () => new Cesium.UrlTemplateImageryProvider({ url: "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", maximumLevel: 19 }),
				}),
				new Cesium.ProviderViewModel({
					name: "OpenStreetMap",
					tooltip: "OpenStreetMap 标准地图",
					iconUrl: providerIcon("#238b57", "图"),
					creationFunction: () => new Cesium.UrlTemplateImageryProvider({ url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", maximumLevel: 19, credit: "© OpenStreetMap contributors" }),
				}),
			];
			let defaultImageryModels = [];
			let terrainModels = [];
			try {
				defaultImageryModels = Cesium.createDefaultImageryProviderViewModels ? Cesium.createDefaultImageryProviderViewModels() : [];
				terrainModels = Cesium.createDefaultTerrainProviderViewModels ? Cesium.createDefaultTerrainProviderViewModels() : [];
			} catch (error) {
				console.warn("Cesium 默认图层列表读取失败", error);
			}
			const imageryProviderViewModels = [
				...customImageryModels,
				...defaultImageryModels.filter(model => !/ArcGIS|Open.?StreetMap/i.test(model.name || "")),
			];
			const viewerOptions = { baseLayerPicker: true, imageryProviderViewModels, selectedImageryProviderViewModel: imageryProviderViewModels[0], geocoder: false, timeline: false, animation: false, sceneModePicker: true, homeButton: true, navigationHelpButton: false, fullscreenButton: false, infoBox: true, selectionIndicator: true };
			if (terrainModels.length) {
				viewerOptions.terrainProviderViewModels = terrainModels;
				viewerOptions.selectedTerrainProviderViewModel = terrainModels[0];
			}
			this.viewer = new Cesium.Viewer(container, viewerOptions);
			this.viewer.scene.globe.enableLighting = true;
			this.viewer.scene.globe.depthTestAgainstTerrain = true;
			this.orderEntities = [];
			this.points.forEach(point => {
				const height = 85000 + Math.log2(point.orders + 1) * 110000;
				const groundGap = 45000;
				const entity = this.viewer.entities.add({
					name: `${point.country} · ${point.orders} 单`,
					description: this.pointDetailsHTML(point),
					position: Cesium.Cartesian3.fromDegrees(point.longitude, point.latitude, groundGap + height / 2),
					cylinder: { length: height, topRadius: 5200, bottomRadius: 9000, material: Cesium.Color.fromCssColorString("#21dbea").withAlpha(.82), outline: true, outlineColor: Cesium.Color.WHITE.withAlpha(.78) },
					point: { pixelSize: 9, color: Cesium.Color.YELLOW, outlineColor: Cesium.Color.WHITE, outlineWidth: 2 },
					label: { text: `${point.orders} 单`, font: "14px sans-serif", fillColor: Cesium.Color.YELLOW, outlineColor: Cesium.Color.BLACK, outlineWidth: 3, style: Cesium.LabelStyle.FILL_AND_OUTLINE, verticalOrigin: Cesium.VerticalOrigin.BOTTOM, pixelOffset: new Cesium.Cartesian2(0, -18), distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 9000000) },
				});
				entity.show = this.root.querySelector("[data-orders]").checked;
				this.orderEntities.push(entity);
			});
			if (this.warehousesLoaded) this.createWarehouseEntities();
			if(this.points.length)this.viewer.camera.flyTo({destination:Cesium.Cartesian3.fromDegrees(this.points[0].longitude,this.points[0].latitude,9000000),duration:1.5});
		}

		toggleOrders(show) {
			if (this.mode === "offline" && this.chart) this.chart.setOption({ series: [
				{ id: "orders-bars", data: show ? (this.offlineData || []) : [] },
				{ id: "orders-points", data: show ? (this.offlinePointData || []) : [] },
			] });
			this.orderEntities.forEach(entity => { entity.show = show; });
		}

		async toggleWarehouses(show) {
			const input = this.root.querySelector("[data-warehouses]");
			if (show && !this.warehousesLoaded) {
				input.disabled = true;
				this.setStatus("正在读取亚马逊仓库公开位置…");
				try {
					const result = await frappe.call({
						method: "fengjing_app.fengjing_business.page.amazon_order_display.amazon_order_display.get_amazon_warehouses",
					});
					this.warehouses = result.message?.warehouses || [];
					this.warehousesLoaded = true;
					this.offlineWarehouseData = this.warehouses.map(point => [point.longitude, point.latitude, 1.8, 1, { ...point, _kind: "warehouse" }]);
				} catch (error) {
					input.checked = false;
					frappe.show_alert({ message: "亚马逊仓库位置读取失败，请稍后再试", indicator: "red" });
					this.setStatus("亚马逊仓库位置读取失败");
					return;
				} finally {
					input.disabled = false;
				}
			}
			if (this.viewer && this.warehousesLoaded && !this.warehouseEntities.length) this.createWarehouseEntities();
			this.warehouseEntities.forEach(entity => { entity.show = show; });
			if (this.mode === "offline" && this.chart) this.chart.setOption({ series: [{ id: "amazon-warehouses", data: show ? (this.offlineWarehouseData || []) : [] }] });
			this.setStatus(show ? `已展示 ${this.warehouses.length} 个亚马逊仓库公开位置` : (this.mode === "online" ? "在线卫星地图" : "离线地球"));
		}

		createWarehouseEntities() {
			if (!this.viewer || this.warehouseEntities.length) return;
			const Cesium = window.Cesium;
			this.warehouses.forEach(point => {
				const entity = this.viewer.entities.add({
					name: point.name || "Amazon 仓库",
					description: this.warehouseDetailsHTML(point),
					position: Cesium.Cartesian3.fromDegrees(point.longitude, point.latitude, 45000),
					point: { pixelSize: 13, color: Cesium.Color.fromCssColorString("#ffb020"), outlineColor: Cesium.Color.WHITE, outlineWidth: 2 },
					label: { text: point.reference || "AMZ", font: "bold 10px sans-serif", fillColor: Cesium.Color.fromCssColorString("#ffe08a"), outlineColor: Cesium.Color.BLACK, outlineWidth: 3, style: Cesium.LabelStyle.FILL_AND_OUTLINE, verticalOrigin: Cesium.VerticalOrigin.TOP, pixelOffset: new Cesium.Cartesian2(0, 8), distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 3000000) },
				});
				entity.show = this.root.querySelector("[data-warehouses]").checked;
				this.warehouseEntities.push(entity);
			});
		}

		async toggleOverlay(name, show) {
			const input = this.root.querySelector(`[data-overlay="${name}"]`);
			if (!show) {
				if (this.onlineLayers[name]) this.onlineLayers[name].show = false;
				return;
			}
			if (!this.viewer) return;
			input.disabled = true;
			this.setStatus(`正在加载${input.parentElement.textContent.trim()}图层…`);
			try {
				if (!this.onlineLayers[name]) this.onlineLayers[name] = name === "weather" ? await this.createWeatherLayer() : this.createReferenceLayer(name);
				this.onlineLayers[name].show = true;
				this.setStatus(`${input.parentElement.textContent.trim()}图层已开启`);
			} catch (error) {
				console.error("在线叠加图层加载失败", name, error);
				input.checked = false;
				frappe.show_alert({ message: `${input.parentElement.textContent.trim()}图层暂时无法读取`, indicator: "red" });
				this.setStatus("在线叠加图层加载失败");
			} finally {
				input.disabled = false;
			}
		}

		createReferenceLayer(name) {
			const Cesium = window.Cesium;
			const services = {
				places: "World_Boundaries_and_Places",
				transport: "World_Transportation",
				boundaries: "World_Reference_Overlay",
				alternate: "World_Boundaries_and_Places_Alternate",
			};
			const service = services[name];
			if (!service) throw new Error("未知参考图层");
			const provider = new Cesium.UrlTemplateImageryProvider({
				url: `https://services.arcgisonline.com/ArcGIS/rest/services/Reference/${service}/MapServer/tile/{z}/{y}/{x}`,
				maximumLevel: 19,
				credit: "© Esri",
			});
			const layer = new Cesium.ImageryLayer(provider);
			layer.alpha = name === "transport" ? .82 : .92;
			this.viewer.imageryLayers.add(layer);
			return layer;
		}

		async createWeatherLayer() {
			const Cesium = window.Cesium;
			const response = await fetch("https://api.rainviewer.com/public/weather-maps.json", { cache: "no-store" });
			if (!response.ok) throw new Error(`天气服务返回 ${response.status}`);
			const data = await response.json();
			const frames = data.radar?.past;
			const frame = frames?.[frames.length - 1];
			if (!frame?.path || !data.host) throw new Error("天气服务没有可用图层");
			const provider = new Cesium.UrlTemplateImageryProvider({
				url: `${data.host}${frame.path}/256/{z}/{x}/{y}/2/1_1.png`,
				maximumLevel: 7,
				credit: "Weather data © RainViewer",
			});
			const layer = new Cesium.ImageryLayer(provider);
			layer.alpha = .62;
			this.viewer.imageryLayers.add(layer);
			return layer;
		}

		toggleRotation(rotate) {
			if (this.mode === "offline" && this.chart) this.chart.setOption({ globe: { viewControl: { autoRotate: rotate } } });
			if (this.viewer) this.viewer.clock.shouldAnimate = rotate;
		}

		enableStreetView() {
			if (this.mode !== "online" || !this.viewer) { frappe.show_alert({message:"请先切换到在线地图",indicator:"orange"}); return; }
			const Cesium=window.Cesium,handler=new Cesium.ScreenSpaceEventHandler(this.viewer.scene.canvas);this.setStatus("请在地球上点击一个位置打开地图和街景");handler.setInputAction(click=>{const cartesian=this.viewer.camera.pickEllipsoid(click.position,this.viewer.scene.globe.ellipsoid);if(!cartesian)return;const geo=Cesium.Cartographic.fromCartesian(cartesian),lon=Cesium.Math.toDegrees(geo.longitude),lat=Cesium.Math.toDegrees(geo.latitude);window.open(`https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${lat},${lon}`,"_blank");handler.destroy();this.setStatus("在线卫星地图");},Cesium.ScreenSpaceEventType.LEFT_CLICK);
		}

		async toggleFullscreen() {
			const card = this.root.closest(".aod-map-card"), button = this.root.querySelector("[data-fullscreen]");
			if (!document.fullscreenElement) { await card.requestFullscreen(); button.textContent = "⛶ 退出全屏"; }
			else { await document.exitFullscreen(); button.textContent = "⛶ 全屏"; }
		}

		setStatus(text) { const element=this.root.querySelector("[data-map-status]"); if(element) element.textContent=text; }
		destroy() {
			this.destroyed = true;
			if(this.chart)this.chart.dispose();
			if(this.viewer)this.viewer.destroy();
			if(this.fullscreenHandler)document.removeEventListener("fullscreenchange",this.fullscreenHandler);
			if(this.keyHandler)document.removeEventListener("keydown",this.keyHandler);
			for(const record of this.textureImages.values())if(record.url)URL.revokeObjectURL(record.url);
			this.textureImages.clear();
		}
	}

	window.FengjingOrderMap = FengjingOrderMap;
})();
