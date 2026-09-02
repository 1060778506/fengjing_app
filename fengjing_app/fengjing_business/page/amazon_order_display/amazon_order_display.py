import json
import os

import frappe
import requests
from frappe.utils import add_days, getdate, nowdate


DOCTYPE = "Amazon order synchronization"

CURRENCY_BY_MARKETPLACE = {
    "ATVPDKIKX0DER": "USD", "A2EUQ1WTGCTBG2": "CAD",
    "A1AM78C64UM0Y8": "MXN", "A2Q3Y263D00KWC": "BRL",
    "A1F83G8C2ARO7P": "GBP", "A1PA6795UKMFR9": "EUR",
    "A1VC38T7YXB528": "JPY",
}

AMAZON_DOMAIN_BY_MARKETPLACE = {
    "ATVPDKIKX0DER": "amazon.com", "A2EUQ1WTGCTBG2": "amazon.ca",
    "A1AM78C64UM0Y8": "amazon.com.mx", "A2Q3Y263D00KWC": "amazon.com.br",
    "A1F83G8C2ARO7P": "amazon.co.uk", "A1PA6795UKMFR9": "amazon.de",
    "A1VC38T7YXB528": "amazon.co.jp",
}

COUNTRY_CODE_BY_MARKETPLACE = {
    "ATVPDKIKX0DER": "US", "A2EUQ1WTGCTBG2": "CA",
    "A1AM78C64UM0Y8": "MX", "A2Q3Y263D00KWC": "BR",
    "A1F83G8C2ARO7P": "GB", "A1PA6795UKMFR9": "DE",
    "A1VC38T7YXB528": "JP",
}

# Offline regional centroids are used only when Amazon returns city/state but omits postal code.
US_STATE_CODES = {
    "ALABAMA":"AL","ALASKA":"AK","ARIZONA":"AZ","ARKANSAS":"AR","CALIFORNIA":"CA","COLORADO":"CO",
    "CONNECTICUT":"CT","DELAWARE":"DE","FLORIDA":"FL","GEORGIA":"GA","HAWAII":"HI","IDAHO":"ID",
    "ILLINOIS":"IL","INDIANA":"IN","IOWA":"IA","KANSAS":"KS","KENTUCKY":"KY","LOUISIANA":"LA",
    "MAINE":"ME","MARYLAND":"MD","MASSACHUSETTS":"MA","MICHIGAN":"MI","MINNESOTA":"MN",
    "MISSISSIPPI":"MS","MISSOURI":"MO","MONTANA":"MT","NEBRASKA":"NE","NEVADA":"NV",
    "NEW HAMPSHIRE":"NH","NEW JERSEY":"NJ","NEW MEXICO":"NM","NEW YORK":"NY","NORTH CAROLINA":"NC",
    "NORTH DAKOTA":"ND","OHIO":"OH","OKLAHOMA":"OK","OREGON":"OR","PENNSYLVANIA":"PA",
    "RHODE ISLAND":"RI","SOUTH CAROLINA":"SC","SOUTH DAKOTA":"SD","TENNESSEE":"TN","TEXAS":"TX",
    "UTAH":"UT","VERMONT":"VT","VIRGINIA":"VA","WASHINGTON":"WA","WEST VIRGINIA":"WV",
    "WISCONSIN":"WI","WYOMING":"WY","DISTRICT OF COLUMBIA":"DC","PUERTO RICO":"PR","PA.":"PA",
}
REGION_CENTROIDS = {
    "US": {"AL":(-86.8,32.8),"AK":(-152.4,64.2),"AZ":(-111.9,34.3),"AR":(-92.4,34.9),"CA":(-119.7,36.8),"CO":(-105.5,39.0),"CT":(-72.7,41.6),"DE":(-75.5,39.0),"DC":(-77.0,38.9),"FL":(-81.5,27.8),"GA":(-83.5,32.7),"HI":(-157.5,20.9),"ID":(-114.6,44.2),"IL":(-89.2,40.0),"IN":(-86.1,40.0),"IA":(-93.5,42.0),"KS":(-98.4,38.5),"KY":(-85.3,37.5),"LA":(-91.9,31.0),"ME":(-69.0,45.3),"MD":(-76.6,39.0),"MA":(-71.8,42.3),"MI":(-84.5,44.3),"MN":(-94.6,46.3),"MS":(-89.7,32.7),"MO":(-92.6,38.5),"MT":(-110.4,47.0),"NE":(-99.9,41.5),"NV":(-116.4,39.3),"NH":(-71.6,43.7),"NJ":(-74.5,40.1),"NM":(-106.0,34.5),"NY":(-75.5,43.0),"NC":(-79.0,35.5),"ND":(-100.5,47.5),"OH":(-82.8,40.3),"OK":(-97.5,35.6),"OR":(-120.6,44.0),"PA":(-77.2,41.0),"PR":(-66.5,18.2),"RI":(-71.5,41.7),"SC":(-80.9,33.8),"SD":(-100.2,44.4),"TN":(-86.4,35.8),"TX":(-99.3,31.5),"UT":(-111.7,39.3),"VT":(-72.7,44.0),"VA":(-78.7,37.5),"WA":(-120.7,47.4),"WV":(-80.6,38.6),"WI":(-89.6,44.6),"WY":(-107.6,43.0)},
    "MX": {"AGUASCALIENTES":(-102.3,21.9),"BAJA CALIFORNIA":(-115.1,30.0),"CHIAPAS":(-92.6,16.8),"CIUDAD DE MEXICO":(-99.1,19.4),"DF":(-99.1,19.4),"COAHUILA DE ZARAGOZA":(-101.7,27.3),"GUANAJUATO":(-101.0,21.0),"HIDALGO":(-98.9,20.5),"JALISCO":(-103.6,20.6),"JAL":(-103.6,20.6),"MEXICO":(-99.7,19.3),"MICHOACAN DE OCAMPO":(-101.9,19.2),"MORELOS":(-99.0,18.7),"NUEVO LEON":(-99.8,25.6),"OAXACA":(-96.7,17.1),"PUEBLA":(-98.0,19.0),"QUERETARO":(-100.4,20.6),"QUINTANA ROO":(-87.1,19.6),"SAN LUIS POTOSI":(-100.4,22.2),"SINALOA":(-107.5,25.0),"SONORA":(-110.7,29.3),"TAMAULIPAS":(-98.7,24.3),"VERACRUZ DE IGNACIO DE LA LLAVE":(-96.4,19.2),"YUCATAN":(-89.0,20.7)},
    "BR": {"PR":(-51.6,-24.9),"RJ":(-42.7,-22.3)},
    "CA": {"QUEBEC":(-71.8,52.0)},
    "CO": {"VALLE DEL CAUCA":(-76.5,3.8),"CUNDINAMARCA":(-74.1,4.7),"QUINDIO":(-75.7,4.5)},
    "PR": {"PUERTO RICO":(-66.5,18.2)},
}

MAP_ASSET_ROOT = "map"

AMAZON_WAREHOUSE_QUERY = """
[out:json][timeout:45];
nwr["building"="warehouse"]["operator"~"Amazon",i];
out center tags;
"""


@frappe.whitelist()
def get_amazon_warehouses():
    """Return public Amazon warehouse locations for the optional map overlay."""
    frappe.has_permission(DOCTYPE, "read", throw=True)
    cache_key = "fengjing_amazon_warehouse_overlay_v3"
    cached = frappe.cache().get_value(cache_key)
    if cached:
        return {"warehouses": cached, "source": "OpenStreetMap / Overpass", "cached": True}

    try:
        response = requests.get(
            "https://overpass-api.de/api/interpreter",
            params={"data": AMAZON_WAREHOUSE_QUERY},
            headers={"User-Agent": "Fengjing-ERPNext-Amazon-Order-Map/1.0"},
            timeout=(10, 60),
        )
        response.raise_for_status()
        elements = response.json().get("elements") or []
    except (requests.RequestException, ValueError) as error:
        frappe.log_error(frappe.get_traceback(), "Amazon warehouse map overlay")
        frappe.throw(f"亚马逊仓库公开地图数据暂时无法读取：{error}")

    warehouses = []
    seen = set()
    for element in elements:
        tags = element.get("tags") or {}
        center = element.get("center") or {}
        latitude = element.get("lat", center.get("lat"))
        longitude = element.get("lon", center.get("lon"))
        try:
            latitude, longitude = float(latitude), float(longitude)
        except (TypeError, ValueError):
            continue
        identity = (round(longitude, 5), round(latitude, 5))
        if identity in seen:
            continue
        seen.add(identity)
        warehouses.append({
            "longitude": longitude,
            "latitude": latitude,
            "name": tags.get("name") or tags.get("ref") or "Amazon 仓库",
            "reference": tags.get("ref") or "",
            "operator": tags.get("operator") or tags.get("brand") or "Amazon",
            "city": tags.get("addr:city") or tags.get("addr:place") or "",
            "region": tags.get("addr:state") or tags.get("addr:province") or "",
            "country_code": tags.get("addr:country") or "",
            "postcode": tags.get("addr:postcode") or "",
        })

    warehouses.sort(key=lambda item: (item["country_code"], item["region"], item["name"]))
    frappe.cache().set_value(cache_key, warehouses, expires_in_sec=7 * 86400)
    return {"warehouses": warehouses, "source": "OpenStreetMap / Overpass", "cached": False}


@frappe.whitelist()
def get_order_map_data(filters=None):
    """Return textures and aggregated order coordinates for the dashboard map."""
    frappe.has_permission(DOCTYPE, "read", throw=True)
    filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
    date_from = filters.get("date_from") or add_days(nowdate(), -30)
    date_to = filters.get("date_to") or nowdate()
    conditions = ["purchase_date >= %s", "purchase_date <= %s"]
    values = [f"{date_from} 00:00:00", f"{date_to} 23:59:59"]
    for field in ("store", "marketplace_id", "country"):
        value = str(filters.get(field) or "").strip()
        if value:
            conditions.append(f"`{field}` = %s")
            values.append(value)
    total_orders = frappe.db.sql(
        f"SELECT COUNT(DISTINCT CONCAT(COALESCE(store, ''), '|', amazon_order_id)) FROM `tab{DOCTYPE}` WHERE {' AND '.join(conditions)}",
        values,
    )[0][0]
    address_conditions = [*conditions, "(COALESCE(shipping_postal_code, '') != '' OR COALESCE(shipping_state_or_region, '') != '')"]
    orders = frappe.db.sql(
        f"""
        SELECT shipping_country_code AS country_code,
               shipping_postal_code AS postal_code, marketplace_id,
               MAX(country) AS country, MAX(shipping_state_or_region) AS region,
               MAX(shipping_city) AS city, store, amazon_order_id,
               MAX(order_status) AS order_status, MAX(currency_code) AS currency_code,
               MAX(order_total) AS order_total, SUM(COALESCE(quantity_ordered, 0)) AS item_count,
               GROUP_CONCAT(DISTINCT NULLIF(sku, '') ORDER BY sku SEPARATOR ', ') AS skus,
               GROUP_CONCAT(DISTINCT NULLIF(asin, '') ORDER BY asin SEPARATOR ', ') AS asins
        FROM `tab{DOCTYPE}`
        WHERE {' AND '.join(address_conditions)}
        GROUP BY shipping_country_code, shipping_postal_code, marketplace_id,
                 shipping_state_or_region, shipping_city, store, amazon_order_id
        """,
        values,
        as_dict=True,
    )
    order_lines = frappe.db.sql(
        f"""
        SELECT store, marketplace_id, amazon_order_id, sku, asin,
               MAX(product_name) AS product_name,
               SUM(COALESCE(quantity_ordered, 0)) AS quantity
        FROM `tab{DOCTYPE}`
        WHERE {' AND '.join(address_conditions)}
        GROUP BY store, marketplace_id, amazon_order_id, sku, asin
        """,
        values,
        as_dict=True,
    )
    summary_rows = frappe.db.sql(
        f"""
        SELECT country, store,
               COUNT(DISTINCT CONCAT(COALESCE(store, ''), '|', amazon_order_id)) AS order_count,
               SUM(COALESCE(quantity_ordered, 0)) AS item_count
        FROM `tab{DOCTYPE}`
        WHERE {' AND '.join(conditions)}
        GROUP BY country, store
        """,
        values,
        as_dict=True,
    )

    mapping_groups = {}
    mapped_item_codes = set()
    for mapping in frappe.get_all(
        "Fengjing - Product Corresponding Platform - Main Table",
        filters={"启用": 1},
        fields=["店铺", "站点id", "平台asin", "平台sku", "物料id"],
        limit_page_length=0,
    ):
        key = (str(mapping.店铺 or ""), str(mapping.站点id or "").upper())
        mapping_groups.setdefault(key, []).append({
            "asin": str(mapping.平台asin or "").strip().upper(),
            "sku": str(mapping.平台sku or "").strip().upper(),
            "item_code": str(mapping.物料id or "").strip(),
        })

    for order in orders:
        skus = {value.strip().upper() for value in str(order.skus or "").split(",") if value.strip()}
        asins = {value.strip().upper() for value in str(order.asins or "").split(",") if value.strip()}
        key = (str(order.store or ""), str(order.marketplace_id or "").upper())
        item_codes = []
        for mapping in mapping_groups.get(key, []):
            if not mapping["item_code"]:
                continue
            if (mapping["asin"] and mapping["asin"] in asins) or (mapping["sku"] and mapping["sku"] in skus):
                if mapping["item_code"] not in item_codes:
                    item_codes.append(mapping["item_code"])
                    mapped_item_codes.add(mapping["item_code"])
        order["mapped_item_codes"] = item_codes

    order_line_groups = {}
    for line in order_lines:
        sku = str(line.sku or "").strip().upper()
        asin = str(line.asin or "").strip().upper()
        key = (str(line.store or ""), str(line.marketplace_id or "").upper())
        mappings = mapping_groups.get(key, [])
        matched = next(
            (mapping for mapping in mappings if mapping["asin"] == asin and mapping["sku"] == sku and asin and sku),
            None,
        ) or next(
            (mapping for mapping in mappings if mapping["asin"] == asin and asin),
            None,
        ) or next(
            (mapping for mapping in mappings if mapping["sku"] == sku and sku),
            None,
        )
        line["item_code"] = matched["item_code"] if matched else ""
        if line.item_code:
            mapped_item_codes.add(line.item_code)
        order_line_groups.setdefault((str(line.store or ""), str(line.amazon_order_id or "")), []).append(line)

    item_details = {
        item.name: item for item in frappe.get_all(
            "Item",
            filters={"name": ["in", list(mapped_item_codes)]},
            fields=["name", "item_name", "image"],
            limit_page_length=0,
        )
    } if mapped_item_codes else {}

    lookup = _get_postcode_lookup()
    point_groups = {}
    for order in orders:
        country_code = str(order.country_code or "").strip().upper() or COUNTRY_CODE_BY_MARKETPLACE.get(str(order.marketplace_id or "").upper(), "")
        postal = _clean_postcode(order.postal_code)
        coords = lookup.get(f"{country_code}-{postal}") if postal else None
        if not coords and len(postal) > 5:
            coords = lookup.get(f"{country_code}-{postal[:5]}")
        precision = "邮编定位"
        if not coords:
            region = str(order.region or "").strip().upper()
            if country_code == "US":
                region = US_STATE_CODES.get(region, region)
            coords = REGION_CENTROIDS.get(country_code, {}).get(region)
            precision = "州/地区中心定位"
        if not coords:
            continue
        key = (round(float(coords[0]), 4), round(float(coords[1]), 4), precision)
        point = point_groups.setdefault(key, {
            "longitude": coords[0], "latitude": coords[1], "orders": 0,
            "country": order.country or country_code, "region": order.region or "",
            "city": order.city or "", "postal_code": order.postal_code or "", "precision": precision,
            "stores": [], "order_samples": [],
        })
        point["orders"] += 1
        if order.store and order.store not in point["stores"]:
            point["stores"].append(order.store)
        product_lines = []
        for line in order_line_groups.get((str(order.store or ""), str(order.amazon_order_id or "")), []):
            item = item_details.get(line.item_code)
            product_lines.append({
                "sku": line.sku or "",
                "asin": line.asin or "",
                "product_name": line.product_name or "",
                "quantity": float(line.quantity or 0),
                "item_code": line.item_code or "",
                "item_name": item.item_name if item else "",
                "item_image": item.image if item else "",
            })
        point["order_samples"].append({
            "amazon_order_id": order.amazon_order_id or "",
            "status": order.order_status or "",
            "store": order.store or "",
            "currency": order.currency_code or "",
            "amount": float(order.order_total or 0),
            "quantity": float(order.item_count or 0),
            "skus": order.skus or "",
            "asins": order.asins or "",
            "product_lines": product_lines,
            "erp_items": [
                {
                    "item_code": code,
                    "item_name": item_details[code].item_name or code,
                    "image": item_details[code].image or "",
                }
                for code in order.mapped_item_codes
                if code in item_details
            ],
        })
    points = list(point_groups.values())

    def build_summary(fieldname):
        groups = {}
        for row in summary_rows:
            label = str(row.get(fieldname) or "").strip()
            if not label:
                continue
            group = groups.setdefault(label, {"name": label, "orders": 0, "items": 0})
            group["orders"] += int(row.order_count or 0)
            group["items"] += float(row.item_count or 0)
        return sorted(groups.values(), key=lambda item: item["orders"], reverse=True)

    country_summary = build_summary("country")
    store_summary = build_summary("store")
    texture_dir = frappe.get_app_path("fengjing_app", "public", "js", MAP_ASSET_ROOT, "textures")
    textures = []
    if os.path.isdir(texture_dir):
        for filename in sorted(os.listdir(texture_dir)):
            if filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                textures.append({
                    "name": os.path.splitext(filename)[0],
                    "path": f"/assets/fengjing_app/js/{MAP_ASSET_ROOT}/textures/{filename}",
                })
    return {
        "points": points, "textures": textures, "matched_locations": len(points),
        "country_summary": country_summary, "store_summary": store_summary,
        "total_orders": int(total_orders or 0),
        "orders_with_postal": sum(1 for order in orders if order.postal_code),
        "orders_with_region": len(orders),
        "mapped_orders": sum(point["orders"] for point in points),
    }


def _clean_postcode(value):
    return str(value or "").strip().replace(" ", "").replace("-", "").upper()


def _get_postcode_lookup():
    cache_key = "fengjing_amazon_order_map_postcodes_v2"
    lookup = frappe.cache().get_value(cache_key)
    if lookup:
        return lookup
    path = frappe.get_app_path("fengjing_app", "public", "js", MAP_ASSET_ROOT, "data", "global-postcodes.json")
    if not os.path.exists(path):
        frappe.log_error(f"地图坐标库不存在：{path}", "Amazon Order Map")
        return {}
    with open(path, encoding="utf-8") as file:
        raw_rows = json.load(file)
    lookup = {}
    for row in raw_rows:
        if len(row) < 4:
            continue
        try:
            key = f"{str(row[0]).upper()}-{_clean_postcode(row[1])}"
            lookup.setdefault(key, [float(row[3]), float(row[2])])
        except (TypeError, ValueError):
            continue
    frappe.cache().set_value(cache_key, lookup, expires_in_sec=86400)
    return lookup


def sync_asin_item_mappings():
    """Create missing platform mappings from the ASIN ranking configuration."""
    parent = frappe.get_single("Fengjing - Product Corresponding Platform - Configuration")
    marketplace_by_store = {
        str(row.get("店铺选项") or ""): str(row.get("站点id") or "").upper()
        for row in parent.get("亚马逊api") or [] if row.get("店铺选项")
    }
    created = skipped = 0
    for row in parent.get("抓取asin配置的子表") or []:
        store = str(row.get("属于哪个店铺") or "")
        asin = str(row.get("需要抓取数据的asin") or "").upper()
        item = str(row.get("asin对应物料") or "")
        marketplace = marketplace_by_store.get(store, "")
        if not (store and asin and item and marketplace):
            continue
        sku = frappe.db.get_value(
            "Amazon order synchronization",
            {"store": store, "marketplace_id": marketplace, "asin": asin}, "sku",
            order_by="purchase_date desc",
        ) or frappe.db.get_value(
            "Amazon Rank SKU Log",
            {"属于哪个店铺": store, "商品列表api_asin": asin},
            "商品列表api_sku", order_by="抓取数据的时间 desc",
        ) or ""
        duplicate = {"店铺": store, "站点id": marketplace, "平台asin": asin}
        if sku:
            duplicate["平台sku"] = sku
        if frappe.db.exists("Fengjing - Product Corresponding Platform - Main Table", duplicate):
            skipped += 1
            continue
        domain = AMAZON_DOMAIN_BY_MARKETPLACE.get(marketplace, "amazon.com")
        frappe.get_doc({
            "doctype": "Fengjing - Product Corresponding Platform - Main Table",
            "启用": 1, "店铺": store, "站点id": marketplace,
            "平台asin": asin, "平台sku": sku, "物料id": item,
            "平台sku_属性": "Amazon ASIN抓取配置同步",
            "平台链接": f"https://www.{domain}/dp/{asin}",
        }).insert(ignore_permissions=True)
        created += 1
    frappe.db.commit()
    return {"created": created, "skipped": skipped}


@frappe.whitelist()
def get_dashboard_data(filters=None):
    """Return read-only Amazon order rows and filter options."""
    frappe.has_permission(DOCTYPE, "read", throw=True)
    filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
    date_from = filters.get("date_from") or add_days(nowdate(), -30)
    date_to = filters.get("date_to") or nowdate()
    if getdate(date_from) > getdate(date_to):
        frappe.throw("开始日期不能晚于结束日期")

    conditions = ["purchase_date >= %s", "purchase_date <= %s"]
    values = [f"{date_from} 00:00:00", f"{date_to} 23:59:59"]
    field_filters = {
        "store": "store",
        "marketplace_id": "marketplace_id",
        "country": "country",
        "order_status": "order_status",
        "fulfillment_channel": "fulfillment_channel",
    }
    for key, fieldname in field_filters.items():
        value = str(filters.get(key) or "").strip()
        if value:
            conditions.append(f"`{fieldname}` = %s")
            values.append(value)

    currency_filter = str(filters.get("currency_code") or "").strip().upper()
    if currency_filter:
        inferred_marketplaces = [
            marketplace for marketplace, currency in CURRENCY_BY_MARKETPLACE.items()
            if currency == currency_filter
        ]
        if inferred_marketplaces:
            placeholders = ", ".join(["%s"] * len(inferred_marketplaces))
            conditions.append(
                f"(`currency_code` = %s OR (COALESCE(`currency_code`, '') = '' "
                f"AND `marketplace_id` IN ({placeholders})))"
            )
            values.extend([currency_filter, *inferred_marketplaces])
        else:
            conditions.append("`currency_code` = %s")
            values.append(currency_filter)

    search = str(filters.get("search") or "").strip()
    if search:
        conditions.append(
            "(amazon_order_id LIKE %s OR sku LIKE %s OR asin LIKE %s OR "
            "product_name LIKE %s OR shipping_city LIKE %s OR shipping_postal_code LIKE %s)"
        )
        values.extend([f"%{search}%"] * 6)

    fields = [
        "name", "amazon_order_id", "sku", "asin", "product_name", "amazon_order_item_id",
        "marketplace_id", "store", "country", "api_region", "sync_type", "order_status",
        "fulfillment_channel", "sales_channel", "order_type", "is_prime", "is_business_order",
        "is_replacement_order", "purchase_date", "last_update_date", "earliest_ship_date",
        "latest_ship_date", "earliest_delivery_date", "latest_delivery_date", "currency_code",
        "order_total", "item_total", "quantity_ordered", "number_of_items_shipped",
        "number_of_items_unshipped", "ship_service_level", "shipping_country_code",
        "shipping_state_or_region", "shipping_city", "shipping_postal_code", "fetched_at",
        "source_updated_at", "sync_status", "last_error",
    ]
    rows = frappe.db.sql(
        f"""
        SELECT {', '.join(f'`{field}`' for field in fields)}
        FROM `tab{DOCTYPE}`
        WHERE {' AND '.join(conditions)}
        ORDER BY purchase_date DESC, amazon_order_id DESC, sku ASC
        LIMIT 20000
        """,
        values,
        as_dict=True,
    )
    # 取消、待处理和免费替换订单可能不返回金额块，
    # 页面仍可以根据Marketplace正确展示币种，但不虚构金额。
    for row in rows:
        if not row.get("currency_code"):
            row["currency_code"] = CURRENCY_BY_MARKETPLACE.get(
                str(row.get("marketplace_id") or "").upper(), ""
            )

    mappings = frappe.get_all(
        "Fengjing - Product Corresponding Platform - Main Table",
        filters={"启用": 1},
        fields=["店铺", "站点id", "平台asin", "平台sku", "物料id"],
        limit_page_length=0,
    )
    exact_map, asin_map, sku_map = {}, {}, {}
    for mapping in mappings:
        key = (str(mapping.店铺 or ""), str(mapping.站点id or "").upper())
        asin, sku, item = str(mapping.平台asin or "").upper(), str(mapping.平台sku or ""), str(mapping.物料id or "")
        if asin and sku: exact_map[(*key, asin, sku)] = item
        if asin: asin_map[(*key, asin)] = item
        if sku: sku_map[(*key, sku)] = item
    item_codes = set()
    for row in rows:
        base = (str(row.store or ""), str(row.marketplace_id or "").upper())
        item = exact_map.get((*base, str(row.asin or "").upper(), str(row.sku or ""))) or asin_map.get((*base, str(row.asin or "").upper())) or sku_map.get((*base, str(row.sku or ""))) or ""
        row["item_code"] = item
        if item: item_codes.add(item)
    item_details = {
        item.name: item for item in frappe.get_all(
            "Item", filters={"name": ["in", list(item_codes)]},
            fields=["name", "item_name", "image"], limit_page_length=0,
        )
    } if item_codes else {}
    for row in rows:
        item = item_details.get(row.item_code)
        row["item_name"] = item.item_name if item else ""
        row["item_image"] = item.image if item else ""

    option_fields = {
        "stores": "store", "marketplaces": "marketplace_id", "countries": "country",
        "statuses": "order_status", "channels": "fulfillment_channel", "currencies": "currency_code",
    }
    options = {}
    for key, fieldname in option_fields.items():
        options[key] = [
            row[0] for row in frappe.db.sql(
                f"SELECT DISTINCT `{fieldname}` FROM `tab{DOCTYPE}` "
                f"WHERE COALESCE(`{fieldname}`, '') != '' ORDER BY `{fieldname}`"
            )
        ]
    options["currencies"] = sorted(
        set(options["currencies"])
        | {
            CURRENCY_BY_MARKETPLACE[marketplace]
            for marketplace in options["marketplaces"]
            if marketplace in CURRENCY_BY_MARKETPLACE
        }
    )

    currencies = sorted({str(row.currency_code) for row in rows if row.currency_code})
    cny_rates = {"CNY": 1.0}
    try:
        from erpnext.setup.utils import get_exchange_rate
        for currency in currencies:
            if currency != "CNY":
                cny_rates[currency] = get_exchange_rate(currency, "CNY", date_to) or 0
    except Exception:
        for currency in currencies:
            cny_rates.setdefault(currency, 0)

    total_matching = frappe.db.sql(
        f"SELECT COUNT(*) FROM `tab{DOCTYPE}` WHERE {' AND '.join(conditions)}",
        values,
    )[0][0]
    return {
        "rows": rows,
        "options": options,
        "range": {"date_from": date_from, "date_to": date_to},
        "limited": total_matching > len(rows),
        "total_matching": total_matching,
        "cny_rates": cny_rates,
        "rate_date": date_to,
    }
