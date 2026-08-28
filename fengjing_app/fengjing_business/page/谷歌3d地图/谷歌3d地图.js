frappe.pages['谷歌3d地图'].on_page_load = function (wrapper) {
	let page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('亚马逊全球订单 3D 态势图'),
		single_column: true
	});

	window.CESIUM_BASE_URL = 'https://cesium.com/downloads/cesiumjs/releases/1.115/Build/Cesium/';

	if (!window.Cesium) {
		const link = document.createElement('link');
		link.rel = 'stylesheet';
		link.href = window.CESIUM_BASE_URL + 'Widgets/widgets.css';
		document.head.appendChild(link);
		const script = document.createElement('script');
		script.src = window.CESIUM_BASE_URL + 'Cesium.js';
		script.onload = () => init_viewer();
		document.head.appendChild(script);
	} else {
		init_viewer();
	}

	async function init_viewer() {
		const $container = $(wrapper).find('.layout-main-section');
		// 增加了一个 #layer-control 的样式和 HTML
		$container.empty().append(`
            <div id="cesium-container" style="height: 85vh; width: 100%; background: #000; position: relative;">
                <div id="layer-control" style="
                    position: absolute; 
                    top: 20px; 
                    left: 20px; 
                    z-index: 999; 
                    background: rgba(25, 25, 25, 0.85); 
                    backdrop-filter: blur(6px); 
                    padding: 12px; 
                    border-radius: 8px; 
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    color: white; 
                    font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; 
                    font-size: 12px;
                    box-shadow: 0 8px 32px rgba(0,0,0,0.5);
                    width: 145px; /* 严格限制宽度 */
                ">
                    <div style="margin-bottom: 8px; font-weight: bold; color: #00ff00; border-left: 3px solid #00ff00; padding-left: 6px;">
                        数据视图
                    </div>
                    <label style="display: flex; align-items: center; margin-bottom: 6px; cursor: pointer;">
                        <input type="checkbox" id="chk-order" checked style="margin-right: 6px; accent-color: #00ff00;"> 订单柱子
                    </label>
                    <label style="display: flex; align-items: center; margin-bottom: 12px; cursor: pointer;">
                        <input type="checkbox" id="chk-clouds" style="margin-right: 6px; accent-color: #00ff00;"> 天气云图
                    </label>

                    <div style="margin-bottom: 8px; font-weight: bold; color: #f1c40f; border-left: 3px solid #f1c40f; padding-left: 6px;">
                        地理辅助
                    </div>

                    <label style="display: flex; align-items: center; margin-bottom: 5px; cursor: pointer;">
                        <input type="checkbox" id="chk-label" checked style="margin-right: 6px; accent-color: #f1c40f;"> 地名
                    </label>
                    <label style="display: flex; align-items: center; margin-bottom: 5px; cursor: pointer;">
                        <input type="checkbox" id="chk-labe0" style="margin-right: 6px; accent-color: #f1c40f;"> 地图
                    </label>
                    <label style="display: flex; align-items: center; margin-bottom: 5px; cursor: pointer;">
                        <input type="checkbox" id="chk-transport" style="margin-right: 6px; accent-color: #f1c40f;"> 交通
                    </label>
                    <label style="display: flex; align-items: center; margin-bottom: 5px; cursor: pointer;">
                        <input type="checkbox" id="chk-overlay" style="margin-right: 6px; accent-color: #f1c40f;"> 边界
                    </label>
                    <label style="display: flex; align-items: center; margin-bottom: 12px; cursor: pointer;">
                        <input type="checkbox" id="chk-alternate" style="margin-right: 6px; accent-color: #f1c40f;"> 标注
                    </label>

                    <div style="margin-bottom: 8px; font-weight: bold; color: #3498db; border-left: 3px solid #3498db; padding-left: 6px;">
                        时空穿越
                    </div>
                    <div style="padding: 0 4px;">
                        <input type="range" id="year-slider" min="0" max="3" step="1" value="3" style="
                            width: 100%;
                            height: 4px; 
                            cursor: pointer; 
                            accent-color: #3498db;
                        ">

<div id="year-display" style="
    text-align: center; 
    margin-top: 6px; 
    color: #3498db; 
    background: rgba(52, 152, 219, 0.15); 
    border-radius: 4px; 
    padding: 4px 0; /* 增加上下内边距，容纳两行字 */
    line-height: 1.3; /* 控制行间距 */
">
    <span style="font-size: 10px; opacity: 0.8;">当前显示</span><br>现代卫星
</div>
<div style="margin-top:8px;text-align:center;">
    <button id="btn-streetview" style="
        width:100%;
        padding:6px;
        background:#e74c3c;
        border:none;
        border-radius:4px;
        color:white;
        cursor:pointer;
        font-size:12px;">
        街景工具
    </button>
</div>
                    </div>
                </div>
            </div>
        `);

		const base_layer = new Cesium.ImageryLayer(
			new Cesium.UrlTemplateImageryProvider({
				url: 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
				maximumLevel: 19
			})
		);

		const viewer = new Cesium.Viewer('cesium-container', {
			baseLayer: base_layer,
			baseLayerPicker: false,
			geocoder: false,
			timeline: false,
			animation: false,
			sceneModePicker: true,
			infoBox: true,
			selectionIndicator: true
		});

		viewer.scene.globe.enableLighting = true;
		viewer.clock.multiplier = 3600;

		// --- 核心修改：定义图层变量以便控制 ---
		let cloudLayer;
		let streetViewHandler;
		let streetViewMode = false;
		setTimeout(() => {

			// --- 定义四个不同时代的代表图层 ---

			// 时代 0: 复古/地形模式 (World Physical Map)
			const layerVintage = new Cesium.ImageryLayer(new Cesium.UrlTemplateImageryProvider({
				url: 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Physical_Map/MapServer/tile/{z}/{y}/{x}'
			}));

			// 时代 1: 2004年左右存档风格 (DeLorme World)
			const layer2004 = new Cesium.ImageryLayer(new Cesium.UrlTemplateImageryProvider({
				url: 'https://services.arcgisonline.com/ArcGIS/rest/services/Specialty/DeLorme_World_Base_Map/MapServer/tile/{z}/{y}/{x}'
			}));

			// 时代 2: 2015年左右存档 (World Street Map 旧版风格)
			const layer2015 = new Cesium.ImageryLayer(new Cesium.UrlTemplateImageryProvider({
				url: 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}'
			}));

			// 将它们全部加入 viewer，但初始隐藏
			const historyLayers = [layerVintage, layer2004, layer2015];
			historyLayers.forEach(l => {
				l.show = false;
				viewer.imageryLayers.add(l);
			});



			// 2. 天气云图图层
			const labelLayer = new Cesium.ImageryLayer(
				new Cesium.UrlTemplateImageryProvider({
					url: 'https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/nexrad-n0q-900913/{z}/{x}/{y}.png',
				})
			);
			labelLayer.show = false; // 初始关闭，避免地图太乱
			viewer.imageryLayers.add(labelLayer);


			const layer0 = new Cesium.ImageryLayer(
				new Cesium.UrlTemplateImageryProvider({
					url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
				})
			);
			layer0.show = false;
			viewer.imageryLayers.add(layer0);

			// 1. World Boundaries and Places (基础地名与界线)
			const layer1 = new Cesium.ImageryLayer(
				new Cesium.UrlTemplateImageryProvider({
					url: 'https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
				})
			);
			viewer.imageryLayers.add(layer1);

			// 2. World Transportation (交通枢纽/道路)
			const layer2 = new Cesium.ImageryLayer(
				new Cesium.UrlTemplateImageryProvider({
					url: 'https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}',
				})
			);
			layer2.show = false; // 初始关闭，避免地图太乱
			viewer.imageryLayers.add(layer2);

			// 3. World Reference Overlay (综合行政边界覆盖层)
			const layer3 = new Cesium.ImageryLayer(
				new Cesium.UrlTemplateImageryProvider({
					url: 'https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Reference_Overlay/MapServer/tile/{z}/{y}/{x}',
				})
			);
			layer3.show = false; // 初始关闭
			viewer.imageryLayers.add(layer3);

			// 4. World Boundaries and Places Alternate (备选标注/多语言)
			const layer4 = new Cesium.ImageryLayer(
				new Cesium.UrlTemplateImageryProvider({
					url: 'https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places_Alternate/MapServer/tile/{z}/{y}/{x}',
				})
			);
			layer4.show = false; // 初始关闭
			viewer.imageryLayers.add(layer4);



			$('#chk-labe0').on('change', function () { layer0.show = this.checked; });
			$('#chk-label').on('change', function () { layer1.show = this.checked; });
			$('#chk-transport').on('change', function () { layer2.show = this.checked; });
			$('#chk-overlay').on('change', function () { layer3.show = this.checked; });
			$('#chk-alternate').on('change', function () { layer4.show = this.checked; });


			// --- 绑定拉杆事件 ---
			// --- 历史时间轴拉杆逻辑 ---
			$('#year-slider').on('input', function () {
				const val = parseInt($(this).val());
				const labels = ["复古模式", "2004存档", "2015存档", "现代卫星"];

				// 1. 更新文字显示：使用 .html() 实现换行
				// 💡 提示：这里加了一点样式，让“当前显示”显得小巧，让模式名称显得突出
				$('#year-display').html(
					'<span style="font-size: 10px; opacity: 0.8; font-weight: normal;">当前显示</span><br>' +
					'<span style="font-size: 12px;">' + labels[val] + '</span>'
				);

				// 2. 原有的图层切换逻辑 (layerVintage, layer2004, layer2015)
				historyLayers.forEach(l => l.show = false);

				if (val < 3) {
					const activeLayer = historyLayers[val];
					activeLayer.show = true;
					activeLayer.alpha = 1.0;
					viewer.imageryLayers.raiseToTop(activeLayer);
				}

				// 确保中文地名层始终在最顶端
				if (typeof layer1 !== 'undefined') viewer.imageryLayers.raiseToTop(layer1);
			});





			// 绑定开关事件
			$('#chk-clouds').on('change', function () { labelLayer.show = this.checked; });
			// 街景工具按钮
			$('#btn-streetview').on('click', function () {

				streetViewMode = !streetViewMode;

				if (streetViewMode) {
					$(this).text("退出街景");
					viewer.container.style.cursor = "crosshair";

					enableStreetViewClick();
				} else {
					$(this).text("街景工具");
					viewer.container.style.cursor = "default";

					if (streetViewHandler) {
						streetViewHandler.destroy();
					}
				}
			});

			$('#chk-order').on('change', function () {
				// 订单柱子是 Entity，需要遍历控制
				const entities = viewer.entities.values;
				for (let i = 0; i < entities.length; i++) {
					entities[i].show = this.checked;
				}
			});
		}, 500);

		frappe.call({
			method: "fengjing_app.fengjing_business.page.亚马逊订单地图.亚马逊订单地图.get_map_data",
			callback: function (r) {
				if (r.message && r.message.length > 0) {
					setTimeout(() => {
						render_real_data(viewer, r.message);
						// 如果初始开关是关着的，确保刚生成的柱子也遵循状态
						if (!$('#chk-order').is(':checked')) {
							viewer.entities.values.forEach(e => e.show = false);
						}
					}, 500);
				}
			}
		});



		function enableStreetViewClick() {

			streetViewHandler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);

			streetViewHandler.setInputAction(function (click) {

				const cartesian = viewer.camera.pickEllipsoid(
					click.position,
					viewer.scene.globe.ellipsoid
				);

				if (!cartesian) return;

				const cartographic = Cesium.Cartographic.fromCartesian(cartesian);

				const lon = Cesium.Math.toDegrees(cartographic.longitude);
				const lat = Cesium.Math.toDegrees(cartographic.latitude);

				openStreetViewPopup(lat, lon);

				// 自动关闭工具
				streetViewMode = false;
				$('#btn-streetview').text("街景工具");
				viewer.container.style.cursor = "default";

				if (streetViewHandler) {
					streetViewHandler.destroy();
				}

			}, Cesium.ScreenSpaceEventType.LEFT_CLICK);
		}


		function openStreetViewPopup(lat, lon) {

			const url = `https://www.google.com/maps?q=${lat},${lon}&t=k&z=16`;

			window.open(url, "_blank");
			const streetUrl = `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${lat},${lon}`;
			window.open(streetUrl, "_blank");
		}

		wrapper.cesium_viewer = viewer;
	}

	function render_real_data(viewer, data_list) {
		// 比例尺建议：如果订单数很大，把这个值调小，比如 10.0 或 1.0
		// 你设为了 100000.0，说明 1 个订单 = 100 公里高，适合看小散户
		const scaleFactor = 100000.0;

		data_list.forEach(item => {
			const [lon, lat, count] = item;
			const height = count * scaleFactor;

			if (lon && lat) {
				const floatLon = parseFloat(lon);
				const floatLat = parseFloat(lat);

				// 🚀 1. 先在【地面】加一个黄色的点，作为地理坐标中心
				viewer.entities.add({
					name: `坐标点: ${count} 单`,
					position: Cesium.Cartesian3.fromDegrees(floatLon, floatLat, 0), // 高度强制为 0
					point: {
						pixelSize: 10,
						color: Cesium.Color.YELLOW,
						outlineColor: Cesium.Color.BLACK,
						outlineWidth: 2,
						heightReference: Cesium.HeightReference.CLAMP_TO_GROUND, // 强制贴地
						disableDepthTestDistance: Number.POSITIVE_INFINITY
					}
				});

				// 🚀 2. 再加【柱子和文字】，它的中心在 height / 2
				viewer.entities.add({
					name: `订单汇总: ${count} 单`,
					description: `经度: ${lon}<br>纬度: ${lat}<br>订单量: ${count}`,
					position: Cesium.Cartesian3.fromDegrees(floatLon, floatLat, height / 2),

					cylinder: {
						length: height,
						topRadius: 5000.0,
						bottomRadius: 5000.0,
						material: Cesium.Color.AQUA.withAlpha(0.6),
						outline: true,
						outlineColor: Cesium.Color.WHITE
					},

					label: {
						text: count.toLocaleString(),
						font: '20px monospace',
						fillColor: Cesium.Color.YELLOW,
						outlineColor: Cesium.Color.BLACK,
						outlineWidth: 2,
						style: Cesium.LabelStyle.FILL_AND_OUTLINE,
						// 相对于 position(height/2) 向上推到顶端
						eyeOffset: new Cesium.Cartesian3(0, 0, -height / 2 - 10000),
						horizontalOrigin: Cesium.HorizontalOrigin.CENTER,
						verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
						distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0.0, 10000000.0)
					}
				});
			}
		});

		// 飞向第一个点
		if (data_list.length > 0) {
			viewer.camera.flyTo({
				destination: Cesium.Cartesian3.fromDegrees(parseFloat(data_list[0][0]), parseFloat(data_list[0][1]), 8000000.0),
				duration: 2
			});
		}
	}
};