import frappe
from frappe.utils import add_days, cint, nowdate


DOCTYPE = "Amazon Rank SKU Log"

MARKETPLACE_COUNTRIES = {
    "ATVPDKIKX0DER": "美国",
    "A2EUQ1WTGCTBG2": "加拿大",
    "A1AM78C64UM0Y8": "墨西哥",
    "A2Q3Y263D00KWC": "巴西",
    "A28R8C7NBKEWEA": "爱尔兰",
    "A1RKKUPIHCS9HS": "西班牙",
    "A1F83G8C2ARO7P": "英国",
    "A13V1IB3VIYZZH": "法国",
    "AMEN7PMS3EDWL": "比利时",
    "A1805IZSGTT6HS": "荷兰",
    "A1PA6795UKMFR9": "德国",
    "APJ6JRA9NG5V4": "意大利",
    "A2NODRKZP88ZB9": "瑞典",
    "AE08WJ6YKNBMC": "南非",
    "A1C3SOZRARQ6R3": "波兰",
    "ARBP9OOSHTCHU": "埃及",
    "A33AVAJ2PDY3EV": "土耳其",
    "A17E79C6D8DWNP": "沙特阿拉伯",
    "A2VIGQ35RCS4UG": "阿联酋",
    "A21TJRUUN4KGV": "印度",
    "A19VAU5U5O7RUS": "新加坡",
    "A39IBJ37TRP1C6": "澳大利亚",
    "A1VC38T7YXB528": "日本",
}


@frappe.whitelist()
def get_rank_dashboard_data(filters=None):
    """Return read-only Amazon rank history and filter choices."""
    frappe.has_permission(DOCTYPE, "read", throw=True)
    filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
    date_from = filters.get("date_from") or add_days(nowdate(), -30)
    date_to = filters.get("date_to") or nowdate()

    query_filters = [
        ["抓取数据的时间", ">=", f"{date_from} 00:00:00"],
        ["抓取数据的时间", "<=", f"{date_to} 23:59:59"],
    ]
    mapping = {
        "marketplace": "商品列表api_站点id",
        "asin": "商品列表api_asin",
        "sku": "商品列表api_sku",
    }
    for key, fieldname in mapping.items():
        if filters.get(key):
            query_filters.append([fieldname, "=", filters[key]])

    fields = [
        "name", "抓取数据的时间", "属于哪个店铺", "是否同行",
        "商品列表api_asin", "商品列表api_sku",
        "绑定的物料", "物料名称", "商品列表api_站点id", "商品列表api_商品标题",
        "商品列表api_产品类型", "商品列表api_状态", "商品列表api_主图链接",
        "商品列表api_最后更新时间", "排名api_主类目排名", "排名api_主类目名称",
        "排名api_主类目链接", "排名api_细分类目排名", "排名api_细分类目名称",
        "排名api_细分类目链接", "排名api_商品名称", "排名api_品牌", "排名api_制造商", "排名api_型号",
        "排名api_颜色", "排名api_尺寸", "排名api_浏览节点id",
    ]
    rows = _get_sampled_rank_rows(query_filters, fields)

    store_map, marketplace_store_map = _get_store_maps()
    asin_item_map, active_asins = _get_asin_item_map()
    stored_store_ids = {str(row.get("属于哪个店铺")) for row in rows if row.get("属于哪个店铺")}
    stored_store_labels = {}
    if stored_store_ids:
        stored_store_labels = {
            str(row.name): str(row.cost_center_name or row.name)
            for row in frappe.get_all(
                "Cost Center",
                filters={"name": ["in", list(stored_store_ids)]},
                fields=["name", "cost_center_name"],
                limit_page_length=0,
            )
        }
    skus = {str(row.get("商品列表api_sku")) for row in rows if row.get("商品列表api_sku")}
    sku_item_map = {}
    if skus:
        for mapping_row in frappe.get_all(
            "Fengjing - Product Corresponding Platform - Main Table",
            filters={"平台sku": ["in", list(skus)]},
            fields=["平台sku", "物料id"],
            limit_page_length=10000,
        ):
            if mapping_row.get("平台sku") and mapping_row.get("物料id"):
                sku_item_map[str(mapping_row.get("平台sku"))] = str(mapping_row.get("物料id"))

    effective_item_codes = {
        str(row.get("绑定的物料") or
            asin_item_map.get((
                str(row.get("商品列表api_asin") or ""),
                str(row.get("属于哪个店铺") or "")
                or marketplace_store_map.get(str(row.get("商品列表api_站点id") or ""), ""),
            )) or
            sku_item_map.get(str(row.get("商品列表api_sku") or "")))
        for row in rows
        if (row.get("绑定的物料") or
            asin_item_map.get((
                str(row.get("商品列表api_asin") or ""),
                str(row.get("属于哪个店铺") or "")
                or marketplace_store_map.get(str(row.get("商品列表api_站点id") or ""), ""),
            )) or
            sku_item_map.get(str(row.get("商品列表api_sku") or "")))
    }
    item_details = {}
    if effective_item_codes:
        item_details = {
            item.name: item
            for item in frappe.get_all(
                "Item",
                filters={"name": ["in", list(effective_item_codes)]},
                fields=["name", "item_name", "image"],
                limit_page_length=10000,
            )
        }

    store_filter = filters.get("store")
    item_filter = filters.get("item")
    result = []
    for row in rows:
        data = dict(row)
        marketplace = data.get("商品列表api_站点id") or ""
        store_id = str(data.get("属于哪个店铺") or "") or marketplace_store_map.get(str(marketplace), "")
        data["店铺"] = stored_store_labels.get(store_id) or store_map.get(marketplace, "")
        if store_filter and store_id != store_filter:
            continue
        asin = str(data.get("商品列表api_asin") or "")
        sku = str(data.get("商品列表api_sku") or "")
        item = data.get("绑定的物料") or asin_item_map.get((asin, store_id)) or sku_item_map.get(sku) or ""
        data["绑定的物料"] = item
        details = item_details.get(item)
        if details:
            data["物料名称"] = details.item_name or ""
            data["物料图片"] = details.image or ""
        else:
            data["物料图片"] = ""
        data["亚马逊商品已删除"] = bool(asin and (asin, store_id) not in active_asins)
        if item_filter and item != item_filter:
            continue
        data["排名api_主类目排名"] = cint(data.get("排名api_主类目排名")) or None
        data["排名api_细分类目排名"] = cint(data.get("排名api_细分类目排名")) or None
        result.append(data)

    return {
        "rows": result,
        "options": _get_options(
            store_map,
            effective_item_codes,
            [
                {"value": store_id, "label": label}
                for store_id, label in sorted(
                    stored_store_labels.items(), key=lambda item: item[1]
                )
            ],
        ),
        "range": {"date_from": date_from, "date_to": date_to},
    }


def _get_store_maps():
    """Map marketplace IDs to store labels and Cost Center document names."""
    labels = {}
    store_ids = {}
    try:
        parent = frappe.get_single("Fengjing - Product Corresponding Platform - Configuration")
        for row in parent.get("亚马逊api") or []:
            marketplace = row.get("站点id") or row.get("marketplace_id")
            store_id = row.get("店铺选项") or ""
            store_label = store_id or row.get("卖家记号")
            if store_id:
                store_label = frappe.db.get_value(
                    "Cost Center", store_id, "cost_center_name"
                ) or store_label
            if marketplace and store_label:
                labels[str(marketplace)] = str(store_label)
            if marketplace and store_id:
                store_ids[str(marketplace)] = str(store_id)
    except Exception:
        pass
    return labels, store_ids


def _get_sampled_rank_rows(query_filters, fields, batch_size=2000, max_points=600):
    """
    分批读取完整日期范围，不再把全部商品合计截断为5000条。
    每个“店铺 + 站点 + ASIN/SKU”最多保留约 max_points 个历史点，
    并保留最早和最新记录，避免多年数据一次塞进浏览器。
    """
    groups = {}
    start = 0
    while True:
        batch = frappe.get_list(
            DOCTYPE,
            filters=query_filters,
            fields=fields,
            order_by="抓取数据的时间 asc, name asc",
            limit_start=start,
            limit_page_length=batch_size,
        )
        if not batch:
            break
        for row in batch:
            store_id = str(row.get("属于哪个店铺") or "")
            marketplace = str(row.get("商品列表api_站点id") or "")
            product = str(
                row.get("商品列表api_asin")
                or row.get("商品列表api_sku")
                or row.get("name")
            )
            points = groups.setdefault((store_id, marketplace, product), [])
            points.append(row)
            if len(points) > max_points:
                points[:] = [points[0], *points[1:-1:2], points[-1]]
        start += len(batch)
        if len(batch) < batch_size:
            break

    rows = [row for points in groups.values() for row in points]
    rows.sort(key=lambda row: (row.get("抓取数据的时间"), row.get("name")))
    return rows


def _get_asin_item_map():
    """Return bindings and active products keyed by (ASIN, Cost Center)."""
    mapping = {}
    active_asins = set()
    try:
        parent = frappe.get_single("Fengjing - Product Corresponding Platform - Configuration")
        for row in parent.get("抓取asin配置的子表") or []:
            asin = str(row.get("需要抓取数据的asin") or "")
            store_id = str(row.get("属于哪个店铺") or "")
            item = str(row.get("asin对应物料") or "")
            if asin and store_id:
                key = (asin, store_id)
                active_asins.add(key)
                if item:
                    mapping[key] = item
    except Exception:
        pass
    return mapping, active_asins


def _get_options(store_map, effective_item_codes=None, store_options=None):
    def unique(field):
        return [
            str(value) for value in frappe.get_all(
                DOCTYPE,
                filters=[[field, "is", "set"]],
                pluck=field,
                group_by=field,
                order_by=field,
                limit_page_length=0,
            )
            if value
        ]
    return {
        "stores": store_options or sorted(set(store_map.values())),
        "marketplaces": [
            {
                "value": marketplace,
                "label": " | ".join(filter(None, [
                    store_map.get(marketplace, "未匹配店铺"),
                    MARKETPLACE_COUNTRIES.get(marketplace, "未知国家"),
                    marketplace,
                ])),
            }
            for marketplace in unique("商品列表api_站点id")
        ],
        "asins": unique("商品列表api_asin"),
        "skus": unique("商品列表api_sku"),
        "items": sorted(set(effective_item_codes or unique("绑定的物料"))),
    }
