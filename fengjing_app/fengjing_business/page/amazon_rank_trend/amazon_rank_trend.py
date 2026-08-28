import frappe
from frappe.utils import add_days, cint, nowdate


DOCTYPE = "Amazon Rank SKU Log"


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
        "item": "绑定的物料",
    }
    for key, fieldname in mapping.items():
        if filters.get(key):
            query_filters.append([fieldname, "=", filters[key]])

    fields = [
        "name", "抓取数据的时间", "商品列表api_asin", "商品列表api_sku",
        "绑定的物料", "物料名称", "商品列表api_站点id", "商品列表api_商品标题",
        "商品列表api_产品类型", "商品列表api_状态", "商品列表api_主图链接",
        "商品列表api_最后更新时间", "排名api_主类目排名", "排名api_主类目名称",
        "排名api_主类目链接", "排名api_细分类目排名", "排名api_细分类目名称",
        "排名api_细分类目链接", "排名api_品牌", "排名api_制造商", "排名api_型号",
        "排名api_颜色", "排名api_尺寸", "排名api_浏览节点id",
    ]
    rows = frappe.get_list(
        DOCTYPE, filters=query_filters, fields=fields,
        order_by="抓取数据的时间 asc", limit_page_length=5000,
    )

    store_map = _get_store_map()
    store_filter = filters.get("store")
    result = []
    item_names = {}
    for row in rows:
        data = dict(row)
        marketplace = data.get("商品列表api_站点id") or ""
        data["店铺"] = store_map.get(marketplace, "")
        if store_filter and data["店铺"] != store_filter:
            continue
        item = data.get("绑定的物料") or ""
        if item and not data.get("物料名称"):
            if item not in item_names:
                item_names[item] = frappe.db.get_value("Item", item, "item_name") or ""
            data["物料名称"] = item_names[item]
        data["排名api_主类目排名"] = cint(data.get("排名api_主类目排名")) or None
        data["排名api_细分类目排名"] = cint(data.get("排名api_细分类目排名")) or None
        result.append(data)

    return {
        "rows": result,
        "options": _get_options(store_map),
        "range": {"date_from": date_from, "date_to": date_to},
    }


def _get_store_map():
    """Map marketplace ID to store name without exposing credentials."""
    result = {}
    try:
        parent = frappe.get_single("Fengjing - Product Corresponding Platform - Configuration")
        for row in parent.get("亚马逊api") or []:
            marketplace = row.get("站点id") or row.get("marketplace_id")
            store = row.get("店铺选项") or row.get("卖家记号")
            if marketplace and store:
                result[str(marketplace)] = str(store)
    except Exception:
        pass
    return result


def _get_options(store_map):
    fields = ["商品列表api_站点id", "商品列表api_asin", "商品列表api_sku", "绑定的物料"]
    rows = frappe.get_all(DOCTYPE, fields=fields, limit_page_length=10000)
    def unique(field):
        return sorted({str(row.get(field)) for row in rows if row.get(field)})
    return {
        "stores": sorted(set(store_map.values())),
        "marketplaces": unique("商品列表api_站点id"),
        "asins": unique("商品列表api_asin"),
        "skus": unique("商品列表api_sku"),
        "items": unique("绑定的物料"),
    }
