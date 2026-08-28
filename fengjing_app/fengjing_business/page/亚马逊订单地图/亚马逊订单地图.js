frappe.pages['亚马逊订单地图'].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: '亚马逊全球订单 3D 态势图',
		single_column: true
	});

	var myChart;
	var texture_list = [];
	var real_order_data = []; // 用于存放 180 万条订单聚合后的坐标点
	var current_index = 0;

	// 1. 初始化 DOM 结构
	$(wrapper).find('.layout-main-section').empty().append(
		'<div id="globe-container" style="position: relative; height: 85vh; width: 100%; background: #000; border-radius: 8px; overflow: hidden;">' +
		'<div id="hud-title" style="position: absolute; top: 20px; left: 20px; z-index: 100; pointer-events: none;">' +
		'<div class="hud-main" style="color: #00f2ff; font-family: monospace; font-size: 18px; font-weight: bold; letter-spacing: 2px; text-transform: uppercase;">系统已启动</div>' +
		'<div class="hud-sub" style="color: rgba(0,242,255,0.7); font-family: monospace; font-size: 12px; margin-top: 4px; border-left: 2px solid #00f2ff; padding-left: 8px;">数据加载中...</div>' +
		'</div>' +
		'<div id="echarts-3d-globe" style="height: 100%; width: 100%;"></div>' +
		'<div id="map-prev" style="position:absolute; left:20px; top:50%; z-index:10; cursor:pointer; color:#fff; font-size:40px; opacity:0.3;">❮</div>' +
		'<div id="map-next" style="position:absolute; right:20px; top:50%; z-index:10; cursor:pointer; color:#fff; font-size:40px; opacity:0.3;">❯</div>' +
		'<div id="map-info" style="position:absolute; bottom:20px; right:20px; z-index:10; color:rgba(255,255,255,0.4); font-family:monospace; font-size:10px;">[←/→ 切换]</div>' +
		'</div>'
	);

	// 2. 并行获取数据：蒙皮列表 + 180万订单统计
	// 这样可以节省加载时间
	frappe.run_serially([
		() => {
			return frappe.call({
				method: "fengjing_app.fengjing_business.page.亚马逊订单地图.亚马逊订单地图.get_map_textures",
				callback: (r) => { if (r.message) texture_list = r.message; }
			});
		},
		() => {
			return frappe.call({
				method: "fengjing_app.fengjing_business.page.亚马逊订单地图.亚马逊订单地图.get_map_data",
				callback: (r) => { if (r.message) real_order_data = r.message; }
			});
		},
		() => {
			if (texture_list.length > 0) {
				preload_textures(texture_list);
				init_chart();
			}
		}
	]);

	function preload_textures(list) {
		list.forEach(function (item) {
			var img = new Image();
			img.src = item.path;
		});
		var bg = new Image();
		bg.src = '/assets/fengjing_app/js/8K太阳系星空.jpg';
	}

	function init_chart() {
		frappe.require([
			"assets/fengjing_app/js/主体-echarts.js",
			"assets/fengjing_app/js/3D引擎echarts-gl.js"
		], function () {
			var chartDom = document.getElementById('echarts-3d-globe');
			myChart = echarts.init(chartDom);
			update_map(0);

			window.addEventListener('resize', function () { myChart.resize(); });

			$(window).off('keydown').on('keydown', function (e) {
				if (e.keyCode === 37) change_map(-1);
				else if (e.keyCode === 39) change_map(1);
			});

			$('#map-prev').click(function () { change_map(-1); });
			$('#map-next').click(function () { change_map(1); });
		});
	}

	function change_map(step) {
		current_index = (current_index + step + texture_list.length) % texture_list.length;
		update_map(current_index);
	}

	function update_map(index) {
		var map = texture_list[index];

		// 预加载邻近图片
		var next_idx = (index + 1) % texture_list.length;
		var img = new Image(); img.src = texture_list[next_idx].path;

		// 【优化】传入真实数据 real_order_data 替换 mockData
		render_globe(myChart, real_order_data, map.path);

		var isNight = map.name.indexOf("夜") > -1 || map.name.toLowerCase().indexOf("night") > -1;
		var hudColor = isNight ? "#00f2ff" : "#ff9800";
		var $hud = $('#hud-title');

		$hud.find('.hud-main')
			.css('color', hudColor)
			.html('<span style="opacity:0.6; font-size:12px;">状态:</span> ' + (isNight ? "夜间行动开启" : "日光监测中"));

		$hud.find('.hud-sub')
			.css({ 'color': hudColor, 'border-left-color': hudColor, 'opacity': 0.9, 'background': 'rgba(0,0,0,0.3)', 'padding': '2px 10px' })
			.text("纹理载入: " + map.name.toUpperCase() + " [" + (index + 1) + "/" + texture_list.length + "]");

		$('#map-info')
			.css('color', isNight ? 'rgba(0,242,255,0.5)' : 'rgba(255,152,0,0.5)')
			.text("点位总数: " + real_order_data.length + " | 蒙皮: " + map.name + " | 系统就绪");
	}
};

function render_globe(myChart, raw_data, texture_path) {
	var bg_path = '/assets/fengjing_app/js/8K太阳系星空.jpg';
	var isNight = texture_path.indexOf("夜") > -1 || texture_path.toLowerCase().indexOf("night") > -1;

	// --- 核心：手动强制计算高度，不让 ECharts 自己缩放 ---
	var processed_data = raw_data.map(function (item) {
		var lon = item[0];
		var lat = item[1];
		var count = item[2];

		// --- 最终平衡方案：起步基础 + 平方根缩放 ---

		// 1. 设置一个基础起步高度（保证 1 个订单也看得见）
		var base_height = 5;

		// 2. 设置一个缩放系数（控制整体高度上限）
		var scale_factor = 2.5;

		// 3. 计算高度 = 5 + (平方根(订单数) * 2.5)
		// 计算结果：
		// 1 个订单高度 = 5 + 1 * 2.5 = 7.5
		// 2 个订单高度 = 5 + 1.41 * 2.5 ≈ 8.5 (差距极小，接近平衡)
		// 10 个订单高度 = 5 + 3.16 * 2.5 ≈ 13
		// 100 个订单高度 = 5 + 10 * 2.5 = 30 (对比之前对数的 100 订单 25 高度，大数值被有效抑制了)
		// 即使有 1000 个订单，高度也只有 5 + 31.6 * 2.5 ≈ 84 (不会冲出屏幕)

		var manual_height = base_height + (Math.sqrt(count) * scale_factor);

		return [lon, lat, manual_height, count];
	});

	var option = {
		backgroundColor: bg_path,
		globe: {
			baseTexture: texture_path,
			environment: bg_path,
			shading: 'realistic',
			realisticMaterial: { roughness: 0.6, metalness: 0.1 },
			// --- 新增/修改光照配置 ---
			light: {
				main: {
					intensity: 1.1,    // 主光源（太阳）强度
					shadow: true       // 是否开启阴影
				},
				ambient: {
					// 这是关键：提高环境光强度（范围 0 到 1）
					// 设置为 0.4 - 0.6 之间，可以让背面清晰可见
					intensity: 0.7
				}
			},
			atmosphere: {
				show: true,
				offset: 0.1,
				color: isNight ? '#001133' : '#003366',
				glowPower: 0.2
			},
			viewControl: {
				autoRotate: true,
				// --- 初始距离 (默认值) ---
				distance: 200,

				// --- 缩放限制设定 ---
				// 1. 滚轮滚到底能看多大 (近景极限)
				// 设置为 120 左右，你可以贴着看美国本土的每个州
				minDistance: 1,

				// 2. 滚轮往后退能退多远 (远景极限)
				// 设置为 400 左右，你可以看到地球在宇宙中变成一个小球
				maxDistance: 500,

				// 阻尼系数（让缩放手感更顺滑，可选）
				//zoomSensitivity: 1
			}
		},
		series: [{
			type: 'bar3D',
			coordinateSystem: 'globe',
			data: processed_data,
			// --- 关键：加粗柱子，让 1 个订单的短柱子更显眼 ---
			barSize: 0.8,
			shading: 'lambert',
			label: {
				show: true,
				distance: 2,
				textStyle: {
					color: '#fff',
					fontSize: 10,
					fontWeight: 'bold',
					backgroundColor: 'rgba(0,0,0,0.5)',
					padding: [1, 3]
				},
				formatter: function (params) {
					// 显示我们存放在第 4 位的真实订单数
					return params.value[3];
				}
			},
			itemStyle: {
				opacity: 1,
				color: isNight ? '#ff0055' : '#ff9800'
			}
		}]
	};

	myChart.setOption(option, true);
}