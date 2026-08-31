import frappe
from frappe.utils import add_days, cint, getdate, nowdate


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
    rows, sampling = _get_sampled_rank_rows(date_from, date_to, filters, fields)

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
    # 平台映射表优先，ASIN抓取配置子表作为回退。两者都使用店铺成本中心区分。
    platform_asin_sku_item_map = {}
    platform_asin_item_map = {}
    platform_sku_item_map = {}
    for mapping_row in frappe.get_all(
        "Fengjing - Product Corresponding Platform - Main Table",
        filters={"启用": 1},
        fields=["店铺", "站点id", "平台asin", "平台sku", "物料id"],
        limit_page_length=0,
    ):
        store_id = str(mapping_row.get("店铺") or "")
        marketplace = str(mapping_row.get("站点id") or "").upper()
        item = str(mapping_row.get("物料id") or "")
        asin = str(mapping_row.get("平台asin") or "").upper()
        sku = str(mapping_row.get("平台sku") or "")
        if store_id and asin and sku and item:
            platform_asin_sku_item_map[(store_id, marketplace, asin, sku)] = item
        if store_id and asin and not sku and item:
            platform_asin_item_map[(store_id, marketplace, asin)] = item
        if store_id and sku and item:
            platform_sku_item_map[(store_id, marketplace, sku)] = item

    def resolve_item(row):
        marketplace = str(row.get("商品列表api_站点id") or "").upper()
        store_id = (
            str(row.get("属于哪个店铺") or "")
            or marketplace_store_map.get(marketplace, "")
        )
        asin = str(row.get("商品列表api_asin") or "").upper()
        sku = str(row.get("商品列表api_sku") or "")
        return (
            platform_asin_sku_item_map.get((store_id, marketplace, asin, sku))
            or platform_asin_item_map.get((store_id, marketplace, asin))
            or platform_sku_item_map.get((store_id, marketplace, sku))
            or asin_item_map.get((asin, store_id))
            or row.get("绑定的物料")
            or ""
        )

    effective_item_codes = {
        str(item) for item in (resolve_item(row) for row in rows) if item
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
        item = resolve_item(data)
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
        "sampling": sampling,
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


def _get_sampled_rank_rows(date_from, date_to, filters, fields):
    """在数据库中按商品和时间桶取最新点，避免把完整历史范围读入 Python。"""
    days = max((getdate(date_to) - getdate(date_from)).days + 1, 1)
    if days <= 31:
        bucket_hours, label = 1, "逐小时"
    elif days <= 90:
        bucket_hours, label = 3, "每3小时"
    elif days <= 366:
        bucket_hours, label = 24, "按天"
    elif days <= 730:
        bucket_hours, label = 48, "每2天"
    else:
        bucket_hours, label = 168, "按周"

    conditions = [
        "`抓取数据的时间` >= %s",
        "`抓取数据的时间` <= %s",
    ]
    values = [f"{date_from} 00:00:00", f"{date_to} 23:59:59"]
    mapping = {
        "store": "属于哪个店铺",
        "marketplace": "商品列表api_站点id",
        "asin": "商品列表api_asin",
        "sku": "商品列表api_sku",
    }
    for key, fieldname in mapping.items():
        if filters.get(key):
            conditions.append(f"`{fieldname}` = %s")
            values.append(filters[key])

    selected_fields = ", ".join(f"`{fieldname}`" for fieldname in fields)
    product_partition = """
        COALESCE(`属于哪个店铺`, ''),
        COALESCE(`商品列表api_站点id`, ''),
        COALESCE(NULLIF(`商品列表api_asin`, ''), NULLIF(`商品列表api_sku`, ''), `name`)
    """
    bucket_seconds = bucket_hours * 60 * 60
    rows = frappe.db.sql(
        f"""
        SELECT
            {selected_fields},
            `_group_record_count`, `_group_best_rank`,
            `_latest_rank`, `_previous_rank`, `_latest_row_number`
        FROM (
            SELECT
                {selected_fields},
                COUNT(*) OVER (
                    PARTITION BY {product_partition}
                ) AS `_group_record_count`,
                MIN(NULLIF(`排名api_主类目排名`, 0)) OVER (
                    PARTITION BY {product_partition}
                ) AS `_group_best_rank`,
                FIRST_VALUE(NULLIF(`排名api_主类目排名`, 0)) OVER (
                    PARTITION BY {product_partition}
                    ORDER BY `抓取数据的时间` DESC, `name` DESC
                ) AS `_latest_rank`,
                LEAD(NULLIF(`排名api_主类目排名`, 0), 1) OVER (
                    PARTITION BY {product_partition}
                    ORDER BY `抓取数据的时间` DESC, `name` DESC
                ) AS `_previous_rank`,
                ROW_NUMBER() OVER (
                    PARTITION BY {product_partition}
                    ORDER BY `抓取数据的时间` DESC, `name` DESC
                ) AS `_latest_row_number`,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        {product_partition},
                        FLOOR(UNIX_TIMESTAMP(`抓取数据的时间`) / %s)
                    ORDER BY `抓取数据的时间` DESC, `name` DESC
                ) AS `_sample_row`
            FROM `tabAmazon Rank SKU Log`
            WHERE {' AND '.join(conditions)}
        ) AS sampled
        WHERE `_sample_row` = 1
        ORDER BY `抓取数据的时间` ASC, `name` ASC
        """,
        [bucket_seconds, *values],
        as_dict=True,
    )
    return rows, {
        "label": label,
        "bucket_hours": bucket_hours,
        "days": days,
        "returned_points": len(rows),
    }


def _get_asin_item_map():
    """Return bindings and active products keyed by (ASIN, Cost Center)."""
    mapping = {}
    active_asins = set()
    try:
        parent = frappe.get_single("Fengjing - Product Corresponding Platform - Configuration")
        for row in parent.get("抓取asin配置的子表") or []:
            asin = str(row.get("需要抓取数据的asin") or "").upper()
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
