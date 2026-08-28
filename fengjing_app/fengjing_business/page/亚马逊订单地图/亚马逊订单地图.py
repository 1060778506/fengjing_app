import os
import frappe
import json

@frappe.whitelist()
def get_map_textures():
    # 路径保持不变
    base_path = "/home/frappe/frappe-bench/apps/fengjing_app/fengjing_app/public/js/地图蒙皮"
    
    if not os.path.exists(base_path):
        return []

    try:
        # 定义需要支持的图片后缀元组
        extensions = ('.jpg', '.jpeg', '.png', '.webp')
        
        # 过滤文件
        files = [f for f in os.listdir(base_path) if f.lower().endswith(extensions)]
        
        result = []
        for f in sorted(files):
            # 使用 splitext 安全地分离文件名和后缀
            file_name, ext = os.path.splitext(f)
            
            result.append({
                "name": file_name, 
                "path": f"/assets/fengjing_app/js/地图蒙皮/{f}"
            })
        return result
    except Exception:
        frappe.log_error("读取地图蒙皮目录失败")
        return []

@frappe.whitelist()
def get_map_data():
    # 1. 定位 JSON 路径
    json_path = frappe.get_app_path("fengjing_app", "public", "js", "全球国家邮编纬度经度.json")
    
    if not os.path.exists(json_path):
        frappe.log_error(f"地图 JSON 缺失: {json_path}", "Map Data Error")
        return []

    # 2. 从内存加载并重组坐标库 (核心：将 List 转为 Dict 提高查询速度)
    cache_key = "global_zip_coords_lookup_dict"
    zip_lookup = frappe.cache().get_value(cache_key)

    if not zip_lookup:
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                raw_list = json.load(f)
            
            # 将 [["US","99553",54.1, -165.7], ...] 转换为 {"US-99553": [lon, lat], ...}
            zip_lookup = {}
            for r in raw_list:
                if len(r) < 4: continue
                # 清洗 JSON 里的邮编，确保格式统一
                # r[1] 是邮编，处理掉空格和横杠，取前5位
                clean_json_zip = str(r[1]).replace(" ", "").replace("-", "")[:5]
                # 构造唯一 Key: 国家-邮编 (如 US-99553)
                combined_key = f"{str(r[0]).upper()}-{clean_json_zip}"
                
                if combined_key not in zip_lookup:
                    # 存入坐标：ECharts 3D 通常要求 [经度, 纬度, 高度]
                    # r[3] 是经度, r[2] 是纬度
                    try:
                        zip_lookup[combined_key] = [float(r[3]), float(r[2])]
                    except (ValueError, TypeError):
                        continue
            
            # 缓存 24 小时，避免频繁读取 50MB 的文件
            frappe.cache().set_value(cache_key, zip_lookup, expires_in_sec=86400)
        except Exception as e:
            frappe.log_error(f"加载坐标库失败: {str(e)}", "Map Data Error")
            return []

    # 3. 提取订单数据 (带上国家字段)
    orders = frappe.get_all("Amazon Order Summary", 
                            fields=["目的地邮编", "目的地国家"], 
                            filters={
                                "目的地邮编": ["not in", ["", None]],
                                "目的地国家": ["not in", ["", None]]
                            })

    # 4. 在内存中完成聚合计算
    # 4. 在 Python 内存中完成聚合计算
    results_map = {}
    for o in orders:
        country = str(o.get("目的地国家") or "").strip().upper()
        raw_zip = str(o.get("目的地邮编") or "").strip()
        
        # 清洗：只去掉空格和横杠，先不截取长度，保留原始 6 位
        clean_zip_full = raw_zip.replace(" ", "").replace("-", "")
        
        # --- 策略：严格分层匹配 ---
        coords = None

        # A 层：【国家 + 原始邮编】精确匹配 (如 CO-760501)
        exact_key = f"{country}-{clean_zip_full}"
        coords = zip_lookup.get(exact_key)

        # B 层：如果 A 失败，尝试【国家 + 前5位】匹配 (如 CO-76050)
        if not coords and len(clean_zip_full) > 5:
            short_key = f"{country}-{clean_zip_full[:5]}"
            coords = zip_lookup.get(short_key)

        # C 层：如果国家内怎么都找不到，才启动【跨国兜底】
        if not coords:
            # 仅取前5位进行全局模糊搜索
            short_zip = clean_zip_full[:5]
            fallback_keys = [k for k in zip_lookup.keys() if k.endswith(f"-{short_zip}")]
            
            if fallback_keys:
                # 只有当你确信是填错国家时才兜底（比如邮编是 95122 这种标志性的美国邮编）
                # 我们给美国和阿联酋极高的权重
                if f"US-{short_zip}" in fallback_keys:
                    coords = zip_lookup.get(f"US-{short_zip}")
                elif f"AE-{short_zip}" in fallback_keys:
                    coords = zip_lookup.get(f"AE-{short_zip}")
                else:
                    # 如果不是美/阿，且国家不匹配，为了地图准确性，我们宁愿不显示
                    # 这样哥伦比亚的订单就不会因为“长得像”而飞到欧洲去
                    continue 


        # 5. 如果找到坐标，按坐标聚合数量
        if coords:
            key = tuple(coords) # 坐标作为 key: (lon, lat)
            results_map[key] = results_map.get(key, 0) + 1

    # 6. 格式化为 ECharts 3D 数组: [[经度, 纬度, 数量], ...]
    return [[float(lon), float(lat), int(count)] for (lon, lat), count in results_map.items()]