"""Freight canvas storage. Only ordinary Frappe document permissions are used."""
import json
import hashlib
import math
import re
import frappe
import requests
from frappe.utils import nowdate, flt

DOCTYPE = "Ozon Freight Canvas"

HISTORY = "Ozon Competitor Price History"


def _history_access(canvas, write=False):
    doc = frappe.get_doc(DOCTYPE, canvas)
    doc.check_permission("write" if write else "read")
    frappe.has_permission(HISTORY, "read", throw=True)
    return doc


def _json_value(value):
    return json.loads(value) if isinstance(value, str) else value


def _quote_time(value):
    if not value:
        return None
    from datetime import datetime
    from zoneinfo import ZoneInfo
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(ZoneInfo(frappe.utils.get_system_timezone())).replace(tzinfo=None)


def _quote_offers(raw, product_id):
    from urllib.parse import urlsplit
    if not isinstance(raw, dict) or not isinstance(raw.get("competitors"), list):
        frappe.throw("报价必须包含 competitors 数组")
    if raw.get("syncing"):
        return None
    offers = []
    for q in raw["competitors"]:
        if not isinstance(q, dict) or str(q.get("itemId")) != str(product_id):
            frappe.throw("报价的商品内部 ID 不匹配")
        price = q.get("price") or {}
        if not isinstance(price, dict) or not re.fullmatch(r"[0-9]+", str(price.get("units", ""))) or not re.fullmatch(r"[0-9]+", str(price.get("nanos") if price.get("nanos") is not None else 0)):
            frappe.throw("报价金额必须使用整数 units 和 nanos")
        try:
            units, nanos = int(price.get("units")), int(price.get("nanos") or 0)
        except (ValueError, TypeError):
            frappe.throw("报价金额格式不正确")
        if units < 0 or not 0 <= nanos < 1_000_000_000:
            frappe.throw("报价金额格式不正确")
        currency = str(price.get("currencyCode") or "")
        if not re.fullmatch(r"[A-Z]{3}", currency):
            frappe.throw("报价币种格式不正确")
        url = str(q.get("url") or "")
        match = re.search(r"\]\((https?://[^)]+)\)$", url)
        if match:
            url = match.group(1)
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not (parsed.hostname == "ozon.ru" or (parsed.hostname or "").endswith(".ozon.ru")):
            frappe.throw("报价链接必须是 Ozon HTTPS 地址")
        reasons = q.get("rejectionReason") or []
        if not isinstance(reasons, list):
            frappe.throw("报价拒绝原因必须是数组")
        offers.append({"price": units + nanos / 1e9, "currency": currency, "url": url,
                       "sku": str(q.get("sku") or ""), "rejectionReason": [str(x) for x in reasons], "downloadedAt": q.get("downloadedAt")})
    return offers


def _insert_quote(canvas, store, product_id, record, import_key, batch_id, source, item_code=None):
    if frappe.db.exists(HISTORY, {"import_key": import_key}):
        return
    version = frappe.db.sql(
        "SELECT COALESCE(MAX(version_number),0) FROM \x60tabOzon Competitor Price History\x60 WHERE canvas=%s AND store=%s AND product_id=%s",
        (canvas, store, product_id))[0][0] + 1
    doc = frappe.new_doc(HISTORY)
    doc.update({"canvas": canvas, "store": store, "product_id": product_id, "version_number": version,
                "company_id": record.get("companyId"), "batch_id": batch_id, "source": source, "import_key": import_key,
                "collected_at_iso": record.get("collectedAt"), "collected_at": _quote_time(record.get("collectedAt")),
                "imported_at": _quote_time(record.get("importedAt")) or frappe.utils.now_datetime(),
                "item_code": item_code if item_code and frappe.db.exists("Item", item_code) else None,
                "sku": ",".join(dict.fromkeys(q.get("sku", "") for q in record["offers"] if q.get("sku")))[:140],
                "offer_count": len(record["offers"]),
                "offers_json": json.dumps(record["offers"], ensure_ascii=False),
                "raw_json": json.dumps(record.get("raw") or {}, ensure_ascii=False)})
    doc.flags.canvas_quote_import = True
    # Only reachable after explicit canvas write / history read permission checks.
    doc.insert(ignore_permissions=True)


def _migrate_canvas_quotes(canvas_name, canvas):
    import hashlib
    for node in canvas.get("nodes", []):
        data = node.get("competitors") or {}
        if data.get("fromHistoryStore") or data.get("external"):
            node["competitors"] = {"external": True}
            continue
        for product in data.get("products") or []:
            snapshots = list(product.get("history") or []) + [{k: v for k, v in product.items() if k != "history"}]
            for snapshot in snapshots:
                store, product_id = str(product.get("store") or ""), str(product.get("itemId") or "")
                if not store or not product_id.isdigit():
                    frappe.throw("旧报价缺少店铺或商品内部 ID，未移除旧数据")
                if not isinstance(snapshot.get("offers"), list):
                    frappe.throw("旧报价格式不正确，未移除旧数据")
                digest = hashlib.sha256(json.dumps([canvas_name, store, product_id, snapshot], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                _insert_quote(canvas_name, store, product_id, snapshot, digest, "legacy-" + digest[:24], "旧画布历史迁移", node["item"].get("item_code"))
        if data.get("products"):
            node["competitors"] = {"external": True}


def _import_quote_batch(canvas_name, canvas, packet):
    import hashlib
    packet = _json_value(packet)
    if not isinstance(packet, dict) or len(json.dumps(packet, ensure_ascii=False).encode()) > 20_000_000:
        frappe.throw("单次采集结果不能超过20MB")
    batch_id = str(packet.get("batch_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9-]{1,100}", batch_id):
        frappe.throw("缺少采集批次编号")
    payload = _json_value(packet.get("payload"))
    if not isinstance(payload, dict):
        frappe.throw("请粘贴采集 JSON")
    if payload.get("format") == "ozfc-competitors-v1":
        results = payload.get("results")
    else:
        competitors = payload.get("competitors") or []
        results = [{"ok": True, "data": payload, "itemId": competitors[0].get("itemId") if competitors else None,
                    "collectedAt": payload.get("collectedAt")}]
    if not isinstance(results, list) or len(results) > 10000:
        frappe.throw("采集批次格式不正确")
    targets = {}
    for node in canvas["nodes"]:
        for product in (node.get("meta") or {}).get("prices") or []:
            store, product_id = str(product.get("store") or ""), str(product.get("product_id") or "")
            if store and product_id.isdigit():
                targets.setdefault((store, product_id), []).append(node)
    pending, failed, unmatched = {}, 0, 0
    for entry in results:
        if not isinstance(entry, dict):
            frappe.throw("采集条目格式不正确")
        if not entry.get("ok"):
            failed += 1
            continue
        if not isinstance(entry.get("data"), dict):
            frappe.throw("采集条目缺少有效的报价数据")
        if entry["data"].get("syncing"):
            failed += 1
            continue
        product_id = str(entry.get("itemId") or "")
        store = str(entry.get("store") or "")
        matches = [key for key in targets if key[1] == product_id and (not store or key[0] == store)]
        if len(matches) != 1:
            unmatched += 1
            continue
        offers = _quote_offers(entry.get("data"), product_id)
        if offers is None:
            failed += 1
            continue
        pending[matches[0]] = {"offers": offers, "raw": entry["data"], "collectedAt": entry.get("collectedAt") or payload.get("collectedAt"),
                               "companyId": entry.get("companyId")}
    if not pending:
        frappe.throw("没有成功且匹配当前画布的报价；未写入任何新版本")
    matched_nodes = set()
    for (store, product_id), record in pending.items():
        key = hashlib.sha256((canvas_name + "|" + batch_id + "|" + store + "|" + product_id).encode()).hexdigest()
        nodes = targets[(store, product_id)]
        _insert_quote(canvas_name, store, product_id, record, key, batch_id, "Ozon 浏览器 F12 采集", nodes[0]["item"].get("item_code"))
        for node in nodes:
            node["competitors"] = {"external": True}
            node["rivalsHidden"] = False
            matched_nodes.add(node["id"])
    return {"matched": len(matched_nodes), "products": len(pending), "failed": failed, "unmatched": unmatched, "batch_id": batch_id}


def _quote_metadata(doc):
    return {"record_name": doc["name"], "version": doc["version_number"], "collectedAt": doc.get("collected_at_iso"),
            "importedAt": str(doc.get("imported_at") or ""), "itemId": doc["product_id"], "store": doc["store"], "loaded": False}


@frappe.whitelist()
def competitor_record(name):
    doc = frappe.get_doc(HISTORY, name)
    doc.check_permission("read")
    _history_access(doc.canvas)
    result = _quote_metadata(doc.as_dict())
    result.update({"offers": _json_value(doc.offers_json) or [], "loaded": True})
    return result


@frappe.whitelist()
def competitor_versions(canvas, store, product_id, before_version, start=0):
    _history_access(canvas)
    filters = {"canvas": canvas, "store": store, "product_id": str(product_id), "version_number": ["<", int(before_version)]}
    rows = frappe.get_list(HISTORY, filters=filters, fields=["name", "version_number", "collected_at_iso", "imported_at", "product_id", "store"],
                           order_by="version_number desc", start=max(0, int(start)), limit_page_length=51)
    return {"versions": [_quote_metadata(r) for r in rows[:50]], "more": len(rows) > 50}


@frappe.whitelist()
def competitor_catalog(canvas):
    _history_access(canvas)
    rows = frappe.db.sql("""
        SELECT h.name,h.store,h.product_id,h.version_number,h.company_id,h.collected_at_iso,h.imported_at,h.offers_json
        FROM \x60tabOzon Competitor Price History\x60 h
        JOIN (SELECT store,product_id,MAX(version_number) v FROM \x60tabOzon Competitor Price History\x60
              WHERE canvas=%s GROUP BY store,product_id) latest
        ON h.store=latest.store AND h.product_id=latest.product_id AND h.version_number=latest.v
        WHERE h.canvas=%s
    """, (canvas, canvas), as_dict=True)
    products = []
    for row in rows:
        product = _quote_metadata(row)
        history = competitor_versions(canvas, row.store, row.product_id, row.version_number)
        product.update({"key": row.store + "|" + row.product_id, "companyId": row.company_id,
                        "offers": _json_value(row.offers_json) or [], "loaded": True,
                        "history": history["versions"], "historyMore": history["more"]})
        products.append(product)
    return {"products": products}


PACKAGE_FIELDS = {
    "length": "custom_带包装长度mm", "width": "custom_带包装宽度mm",
    "height": "custom_带包装高度mm", "weight": "custom_带包装重量g",
}


def default_config():
    # Snapshot of the supplied tariff sheets, not a live spreadsheet dependency.
    groups = [
        ("Extra Small", 3.37, 0.001, .5, 1, 1500, 60, 90, 0, 0),
        ("Budget", 25.83, .5, 30, 1, 1500, 60, 150, 0, 0),
        ("Small", 17.97, .001, 2, 1500, 7000, 60, 150, 0, 0),
        ("Big", 40.44, 2, 30, 1500, 7000, 150, 310, 12000, 31),
        ("Premium Small", 24.71, .001, 5, 7000, 250000, 150, 250, 0, 0),
        ("Premium Big", 69.64, 5, 30, 7000, 250000, 150, 310, 12000, 80),
    ]
    routes = []
    for provider in ("CEL", "兴远", "RETS"):
        for group, fixed, lo, hi, vlo, vhi, side, total, divisor, billmax in groups:
            for speed in ("Express", "Standard", "Economy"):
                if provider == "兴远" and speed == "Express":
                    continue
                if provider == "RETS" and ((group in ("Budget", "Big", "Premium Big") and speed == "Express") or (group == "Big" and speed == "Economy") or (group == "Premium Big" and speed == "Standard")):
                    continue
                rates = [50.5, 39.3, 28.1] if group in ("Extra Small", "Small", "Premium Small") else [37.1, 28.1, 19.1]
                if group == "Premium Big":
                    rates = [37.1, 31.4, 25.8]
                low, high = lo, hi
                if provider == "CEL":
                    low = {"Budget": .501, "Big": 2.001, "Premium Big": 5.001}.get(group, lo)
                if provider == "兴远":
                    low = {"Budget": .55, "Big": 2.2, "Premium Big": 5.5}.get(group, lo)
                    high = {"Extra Small": .55, "Small": 2.2, "Premium Small": 5.5}.get(group, hi)
                    billmax = 0
                elif provider == "RETS":
                    divisor = billmax = 0  # RETS supplied calculator uses actual weight.
                    if group == "Big": total = 250
                routes.append(dict(
                    id=f"{provider}-{group}-{speed}", provider=provider, name=f"{group} · {speed}",
                    enabled=True, destination="俄罗斯", mode="rFBS" if provider == "兴远" else "FBP" if provider == "CEL" else "RETS",
                    fixed=fixed, rate=rates[("Express", "Standard", "Economy").index(speed)],
                    min_weight=low, max_weight=high, min_exclusive=provider != "CEL" and group in ("Budget", "Big", "Premium Big"),
                    min_value=vlo + 1 if vlo in (1500, 7000) else vlo, max_value=vhi, value_exclusive=False,
                    max_side=side, max_sum=total, divisor=divisor, max_billable=billmax,
                    sorted_sides=[150, 80, 80] if provider == "CEL" and group == "Premium Big" else [],
                    step=0, surcharge=0, battery=provider == "兴远" or (provider == "RETS" and speed != "Express"), liquid=provider == "兴远",
                    days=({"Express":"4–8天", "Standard":"8–12天", "Economy":"11–15天"} if provider == "CEL" else {"Express":"4–9天", "Standard":"7–19天" if provider == "兴远" else "10–15天", "Economy":("12–24天" if group in ("Small", "Premium Small") else "17–29天") if provider == "兴远" else "15–20天"})[speed],
                    source="CEL产品资费表 V7.24 / OZON-FBP" if provider == "CEL" else "20260903XY兴远 / OZON-RFBS运费" if provider == "兴远" else "RETS 2026.07.24 / 俄罗斯主表",
                    note="历史运价快照，使用前请确认最新报价。未明确的带电、液体条件默认不放行；请向承运商核实后编辑。"))
    # July sheet duplicates the twelve September XY lanes: keep the newer
    # bounds/transit times, while recording both sources rather than quoting twice.
    for route in routes:
        if route["provider"] == "兴远":
            route["source"] += " / 兴远rFBS全渠道计算器临沂 · 兴远渠道计算（2026.07.17）"
            route["note"] += " 7月表同渠道单价一致，部分货值上限和时效不同，保留9月版参数。"
    for suffix, name, destination, fixed, rate, days in (
        ("ePacket", "ePacket Economy Track", "俄罗斯", 12, 30, "25–30天"),
        ("CIS", "China Post ePacket Economy Track CIS", "哈萨克斯坦", 1.6, 33, "15–20天"),
        ("Belarus", "China Post ePacket Economy Belarus", "白俄罗斯", 13, 30, "20–30天"),
    ):
        routes.append(dict(
            id=f"兴远-Post-{suffix}", provider="兴远", name=name,
            enabled=True, destination=destination, mode="rFBS",
            fixed=fixed, rate=rate, min_weight=.001, max_weight=5,
            min_exclusive=False, min_value=0, max_value=1000,
            value_currency="CNY", value_exclusive=False, max_side=60,
            max_sum=90, divisor=0, max_billable=0, sorted_sides=[],
            step=0, surcharge=0, battery=False, liquid=False, days=days,
            source="兴远rFBS全渠道计算器临沂 / 兴远渠道计算 · 2026.07.17",
            note="中国邮政渠道；按实重计费，货值上限1000人民币。原表H15:H17、I15:I17、J15:J17为合并限制。自送，不适用免费顺丰揽收；带电、液体未确认。请核对目的国和实时运价。",
        ))
    # Independent rFBS sheet: same mainland rates/bounds, different transit
    # times and no Express service for Big / Premium Big. Keep FBP untouched.
    for original in list(routes):
        if original["provider"] != "CEL":
            continue
        if original["id"] in ("CEL-Big-Express", "CEL-Premium Big-Express"):
            continue
        route = dict(original)
        speed = original["name"].split(" · ")[-1]
        route.update(id="CEL-RFBS-" + original["id"][4:], mode="RFBS", speed=speed,
                     days={"Express": "5–10天", "Standard": "10–15天", "Economy": "15–25天"}[speed],
                     source="CEL产品资费表 V7.24 / OZON-rFBS",
                     note="原表支持到取货点或到门。轻小件/低客单价件免费销毁、不改派、不退回；其余大陆渠道支持改派，退回收取正向运价1.5倍。带电、液体未明确，默认不放行。")
        routes.append(route)
    routes.append(dict(
        id="CEL-RFBS-HK-Express", provider="CEL", name="香港空运 · Express",
        enabled=True, destination="俄罗斯", mode="RFBS", speed="Express",
        fixed=19, rate=96, min_weight=.001, max_weight=25,
        min_value=1, max_value=500000, max_side=150, max_sum=310,
        divisor=6000, volumetric_min_sum=60, step=.1, max_billable=0,
        surcharge=0, sorted_sides=[], battery=False, liquid=False,
        days="7–12天", source="CEL产品资费表 V7.24 / OZON-rFBS / 香港空运",
        note="三边和超过60cm才计体积重，实重与体积重取大；百克向上进位。免费销毁、支持改派，退运收取正向运价1.5倍。带电、液体需另行确认。"))
    from .guoo_tariffs import routes as guoo_routes
    routes.extend(guoo_routes())
    from .xy_tariffs import routes as xy_routes
    routes.extend(xy_routes())
    return {"version": 2, "xy_post_snapshot": 2, "guoo_snapshot": 1, "cel_rfbs_snapshot": 1, "xy_extra_snapshot": 1, "routes": routes}


def _object(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        frappe.throw("数据必须是 JSON 对象")
    if len(json.dumps(value, ensure_ascii=False).encode()) > 2_000_000:
        frappe.throw("单份画布或配置不能超过 2MB")
    return value


def validate_config(config):
    config = _object(config)
    for key in ("acquisition_pct", "withdrawal_pct", "returns_rub", "lastmile_rub", "advertising_cny"):
        if key in config:
            value = config[key]
            if key == "advertising_cny" and value == "":
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (key.endswith("_pct") and value >= 100) or (key == "lastmile_rub" and value > 500):
                frappe.throw(f"费用参数无效：{key}")
    for key in ("margin_enabled", "acquisition_enabled", "withdrawal_enabled", "returns_enabled", "lastmile_enabled", "advertising_enabled"):
        if key in config and not isinstance(config[key], bool):
            frappe.throw(f"费用开关 {key} 必须为布尔值")
    if "margin_pct" in config:
        margin = config["margin_pct"]
        if isinstance(margin, bool) or not isinstance(margin, (int, float)) or not math.isfinite(margin) or not 0 <= margin < 100:
            frappe.throw("目标利润率必须为0至100%之间的数字（不含100%）")
    if "cny_per_rub" in config:
        rate = config["cny_per_rub"]
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate < 0:
            frappe.throw("汇率必须是非负数字")
    routes = config.get("routes")
    if not isinstance(routes, list) or len(routes) > 500:
        frappe.throw("运价配置需要 routes 数组，最多500条")
    ids = set()
    numeric = ("fixed", "rate", "min_weight", "max_weight", "min_value", "max_value", "max_side", "max_sum", "divisor", "volumetric_min_sum", "max_billable", "step", "surcharge")
    for r in routes:
        if not isinstance(r, dict) or not r.get("id") or r["id"] in ids:
            frappe.throw("渠道 ID 不能为空或重复")
        ids.add(r["id"])
        for key in ("fixed", "rate", "min_weight", "max_weight", "min_value", "max_value"):
            if key not in r:
                frappe.throw(f"渠道缺少必填参数 {key}")
        if not r.get("provider") or not r.get("name"):
            frappe.throw("承运商和渠道名称不能为空")
        if r.get("value_currency", "RUB") not in ("RUB", "CNY"):
            frappe.throw("渠道货值币种仅支持RUB或CNY")
        for key in numeric:
            v = r.get(key, 0)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                frappe.throw(f"{r['id']} 的 {key} 必须是非负数字")
        if r.get("max_weight", 0) < r.get("min_weight", 0) or r.get("max_value", 0) < r.get("min_value", 0):
            frappe.throw("重量或货值上下限顺序错误")
        for key in ("enabled", "battery", "liquid", "min_exclusive", "value_exclusive", "no_value_limit"):
            if key in r and not isinstance(r[key], bool): frappe.throw(f"{key} 必须为 true 或 false")
        sides = r.get("sorted_sides", [])
        if not isinstance(sides, list) or len(sides) not in (0, 3) or any(not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in sides):
            frappe.throw("sorted_sides 必须为空或三个正数")
        minimum = r.get("min_sorted_sides", [])
        if not isinstance(minimum, list) or len(minimum) not in (0, 3) or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in minimum):
            frappe.throw("min_sorted_sides 必须为空或三个非负数字")
    return config


@frappe.whitelist()
def bootstrap():
    frappe.has_permission(DOCTYPE, "read", throw=True)
    return dict(can_write=frappe.has_permission(DOCTYPE, "write"), can_create=frappe.has_permission(DOCTYPE, "create"),
                canvases=frappe.get_list(DOCTYPE, fields=["name", "canvas_title", "modified"], order_by="modified desc", limit_page_length=200), defaults=default_config(), exchange=exchange_info())


@frappe.whitelist()
def exchange_info():
    frappe.has_permission(DOCTYPE, "read", throw=True)
    try:
        from erpnext.setup.utils import get_exchange_rate
        rate = flt(get_exchange_rate("RUB", "CNY", nowdate()))
    except Exception:
        rate = 0
    return {"cny_per_rub": rate if rate > 0 else 0, "date": nowdate(), "source": "ERPNext 汇率（参考换算）" if rate > 0 else "未取得汇率，请手动填写"}


def _valuation(item, visited=None):
    """Weighted valuation of readable positive stock, separated by company."""
    result = []
    visited = set(visited or ())
    if item.name in visited or len(visited) >= 10:
        return []
    visited.add(item.name)
    if frappe.has_permission("Bin", "read") and frappe.has_permission("Warehouse", "read"):
        bins = frappe.get_list("Bin", filters={"item_code": item.name, "actual_qty": [">", 0]}, fields=["warehouse", "actual_qty", "stock_value"], limit_page_length=1000)
        warehouses = frappe.get_list("Warehouse", filters={"name": ["in", [b.warehouse for b in bins]]}, fields=["name", "company"], limit_page_length=1000) if bins else []
        companies = {w.name: w.company for w in warehouses}
        grouped = {}
        for b in bins:
            company = companies.get(b.warehouse)
            if not company: continue
            totals = grouped.setdefault(company, [0, 0])
            totals[0] += flt(b.actual_qty)
            totals[1] += flt(b.stock_value)
        for company, (qty, value) in grouped.items():
            if qty > 0 and value > 0:
                result.append({"company": company, "amount": value / qty, "currency": frappe.db.get_value("Company", company, "default_currency"), "source": "当前正库存加权估值成本"})
    if not result and frappe.has_permission("Product Bundle", "read"):
        bundles = frappe.get_list("Product Bundle", filters={"new_item_code": item.name, "disabled": 0}, fields=["name"], limit_page_length=2)
        if len(bundles) == 1:
            bundle = frappe.get_doc("Product Bundle", bundles[0].name)
            bundle.check_permission("read")
            parts = []
            for row in bundle.items:
                if not frappe.has_permission("Item", "read"):
                    break
                component = frappe.get_doc("Item", row.item_code)
                component.check_permission("read")
                costs = [c for c in _valuation(component, visited) if c.get("currency") == "CNY"]
                qty = flt(row.qty)
                uom = row.get("uom") or component.stock_uom
                factor = 1 if uom == component.stock_uom else next((flt(u.conversion_factor) for u in component.get("uoms", []) if u.uom == uom), 0)
                if len(costs) != 1 or qty <= 0 or factor <= 0:
                    break
                parts.append({"item_code": component.name, "item_name": component.item_name, "qty": qty * factor, "unit_cost": costs[0]["amount"], "amount": costs[0]["amount"] * qty * factor, "source": costs[0]["source"], "company": costs[0].get("company")})
            if bundle.items and len(parts) == len(bundle.items) and len({p["company"] for p in parts if p["company"]}) <= 1:
                result.append({"amount": sum(p["amount"] for p in parts), "currency": "CNY", "source": "套件组成物料成本合计（各组件优先库存加权，其次采购参考）", "components": parts})
            return result
    if not result and frappe.has_permission("Item Price", "read"):
        prices = frappe.get_list("Item Price", filters={"item_code": item.name, "buying": 1, "currency": "CNY", "price_list_rate": [">", 0]},
                                fields=["price_list_rate", "price_list", "uom", "valid_from", "valid_upto", "supplier"], order_by="valid_from desc, modified desc", limit_page_length=500)
        today = nowdate()
        applicable = [p for p in prices if not p.supplier and p.uom == item.stock_uom and (not p.valid_from or str(p.valid_from) <= today) and (not p.valid_upto or str(p.valid_upto) >= today)]
        latest = {}
        for price in applicable:
            latest.setdefault(price.price_list, price)
        for price in latest.values():
            result.append({"amount": flt(price.price_list_rate), "currency": "CNY", "source": f"物料价格采购参考 · {price.price_list}（非库存估值）"})
    if not result and flt(item.last_purchase_rate) > 0:
        result.append({"amount": flt(item.last_purchase_rate), "currency": "", "source": "最近采购价参考（不是当前库存成本；币种请核对）"})
    return result


def _seller_read(row, path, payload):
    # Only these price/product read endpoints are permitted; never mutate Ozon prices.
    if path not in ("/v3/product/info/list", "/v5/product/info/prices", "/v3/product/list", "/v2/warehouse/list", "/v2/delivery-method/list", "/v2/product/info/stocks-by-warehouse/fbs"):
        raise ValueError("Unsupported read endpoint")
    response = requests.post("https://api-seller.ozon.ru" + path, headers={
        "Client-Id": str(row.get("ozon_id") or "").strip(),
        "Api-Key": str(row.get("ozon_秘钥") or "").strip(),
    }, json=payload, timeout=(5, 15))
    if response.status_code != 200:
        raise ValueError(f"Ozon 只读查询失败：HTTP {response.status_code}")
    return response.json()


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (ValueError, TypeError):
        return None


def _warehouse_channels(row, store):
    warehouses = _seller_read(row, "/v2/warehouse/list", {}).get("warehouses", [])
    by_id = {str(w["warehouse_id"]): w for w in warehouses}
    output, cursor, seen = [], "", set()
    for _ in range(50):
        # v2 paginates by cursor, not v1's offset; join actual warehouse_id.
        page = _seller_read(row, "/v2/delivery-method/list", {"limit": 100, "cursor": cursor})
        for method in page.get("delivery_methods", []):
            w = by_id.get(str(method.get("warehouse_id")))
            if not w or method.get("status") != "ACTIVE":
                continue
            drop = method.get("tpl_dropoff_point") or {}
            output.append(dict(store=store, warehouse_id=str(w["warehouse_id"]), warehouse_name=w.get("name") or "",
                               method_id=str(method["id"]), method_name=method.get("name") or "",
                               mode="RFBS" if w.get("is_rfbs") else "FBS",
                               dropoff_name=drop.get("name") or "", dropoff_code=drop.get("code") or ""))
        if not page.get("has_next"):
            return output
        cursor = page.get("cursor")
        if not cursor or cursor in seen:
            raise ValueError("仓库配送方式分页没有推进")
        seen.add(cursor)
    raise ValueError("仓库配送方式超过分页上限，未完整读取")


def _warehouse_stocks(row, store, skus):
    output, cursor, seen = [], "", set()
    requested = set(skus)
    for _ in range(100):
        page = _seller_read(row, "/v2/product/info/stocks-by-warehouse/fbs",
                            {"sku": [int(s) for s in skus], "limit": 1000, "cursor": cursor})
        for p in page.get("products", []):
            if str(p.get("sku")) not in requested or not p.get("warehouse_id"):
                continue
            free = _number(p.get("free_stock"))
            present, reserved = _number(p.get("present")), _number(p.get("reserved"))
            if free is None and present is not None and reserved is not None:
                free = max(0, present - reserved)
            output.append(dict(store=store, sku=str(p["sku"]), warehouse_id=str(p["warehouse_id"]), free_stock=free))
        if not page.get("has_next"):
            return output
        cursor = page.get("cursor")
        if not cursor or cursor in seen:
            raise ValueError("分仓库存分页没有推进")
        seen.add(cursor)
    raise ValueError("分仓库存超过分页上限，未完整读取")


@frappe.whitelist()
def warehouse_stocks(products, force=0):
    parent = _ozon_access()
    if isinstance(products, str):
        products = json.loads(products)
    if not isinstance(products, list) or len(products) > 1500:
        frappe.throw("分仓库存查询最多1500组商品")
    configs = {}
    for row in parent.get("table_wckx") or []:
        if row.get("店铺选项") and row.get("ozon_id") and row.get("ozon_秘钥"):
            configs.setdefault(str(row.get("店铺选项")), []).append(row)
    grouped = {}
    for p in products:
        if not isinstance(p, dict) or not isinstance(p.get("skus"), list) or len(p["skus"]) > 20:
            frappe.throw("分仓库存商品格式不正确")
        store = str(p.get("store") or "")
        if len(configs.get(store, [])) != 1:
            frappe.throw("分仓库存查询的店铺配置不存在或不唯一")
        for sku in p["skus"]:
            if not re.fullmatch(r"[1-9][0-9]{0,18}", str(sku)):
                frappe.throw("Ozon SKU ID格式不正确")
            grouped.setdefault(store, set()).add(str(sku))
    output, errors = [], []
    for store, values in grouped.items():
        row = configs[store][0]
        values = sorted(values)
        for start in range(0, len(values), 100):
            skus = values[start:start + 100]
            digest = hashlib.sha256(json.dumps([row.get("ozon_id"), row.get("ozon_秘钥"), skus]).encode()).hexdigest()
            key = "ozfc:warehouse-stock:" + digest
            cached = None if int(force) else frappe.cache.get_value(key)
            try:
                records = cached if cached is not None else _warehouse_stocks(row, store, skus)
                if cached is None:
                    frappe.cache.set_value(key, records, expires_in_sec=300)
                output.extend({**record, "store": store} for record in records)
            except (ValueError, requests.RequestException):
                errors.append(store + "：分仓库存查询未完成，未将未知库存当作0")
    return {"stocks": output, "errors": errors, "checked_at": str(frappe.utils.now_datetime())[:19]}


@frappe.whitelist()
def warehouse_channels(force=0):
    parent = _ozon_access()
    rows = [r for r in parent.get("table_wckx") or [] if r.get("ozon_id") and r.get("ozon_秘钥")]
    names = [str(r.get("店铺选项") or "") for r in rows]
    if any(not s for s in names) or len(set(names)) != len(names):
        frappe.throw("Ozon 店铺配置必须唯一且非空")
    output, errors = [], []
    for row, store in zip(rows, names):
        credential = hashlib.sha256(str(row.get("ozon_秘钥")).encode()).hexdigest()[:16]
        key = "ozfc:warehouse:" + str(row.get("ozon_id")) + ":" + credential
        cached = None if int(force) else frappe.cache.get_value(key)
        try:
            channels = cached if cached is not None else _warehouse_channels(row, store)
            if cached is None:
                frappe.cache.set_value(key, channels, expires_in_sec=300)
            output.extend({**channel, "store": store} for channel in channels)
        except (ValueError, requests.RequestException):
            errors.append(store + "：仓库配送方式查询未完成，请稍后重试")
    return {"channels": output, "errors": errors, "stores": names, "checked_at": str(frappe.utils.now_datetime())[:19]}


def _ozon_access():
    frappe.has_permission(DOCTYPE, "write", throw=True)
    if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
        frappe.throw("仅系统管理员可以管理 Ozon 后台登录凭证和查询店铺商品")
    parent = frappe.get_single("Fengjing - Product Corresponding Platform - Configuration")
    parent.check_permission("read")
    return parent


@frappe.whitelist()
def competitor_collection_manifest(products):
    """Resolve account IDs only for the current canvas's explicit product list."""
    parent = _ozon_access()
    products = json.loads(products) if isinstance(products, str) else products
    if not isinstance(products, list) or len(products) > 1500:
        frappe.throw("当前画布商品列表格式不正确，最多1500个绑定商品")
    configs = {}
    for row in parent.get("table_wckx") or []:
        if row.get("ozon_id") and row.get("ozon_秘钥"):
            configs.setdefault(str(row.get("店铺选项") or ""), []).append(row)
    output, seen = [], set()
    for product in products:
        if not isinstance(product, dict):
            frappe.throw("当前画布商品编号格式不正确")
        store, item_id = str(product.get("store") or ""), str(product.get("itemId") or "")
        if not store or not item_id.isdigit() or int(item_id) <= 0:
            frappe.throw("卡片缺少店铺或有效商品内部 ID，请先刷新物料标识")
        rows = configs.get(store, [])
        if len(rows) != 1:
            frappe.throw("卡片对应 Ozon 店铺配置不存在或重复")
        company = str(rows[0].get("ozon_id") or "")
        if not company.isdigit():
            frappe.throw("Ozon 店铺 Client ID 格式不正确")
        if (store, item_id) in seen:
            continue
        seen.add((store, item_id))
        output.append({"store": store, "companyId": company, "itemId": item_id})
    return {"products": output}


@frappe.whitelist()
def list_ozon_canvas_products(store="", last_id=""):
    parent = _ozon_access()
    configs = [r for r in parent.get("table_wckx") or [] if r.get("ozon_id") and r.get("ozon_秘钥")]
    stores = [str(r.get("店铺选项") or "") for r in configs]
    if not store:
        if not stores or any(not s for s in stores) or len(set(stores)) != len(stores):
            frappe.throw("请先配置唯一且非空的 Ozon 店铺")
        return {"stores": stores}
    rows = [r for r in configs if str(r.get("店铺选项")) == store]
    if len(rows) != 1:
        frappe.throw("Ozon 店铺配置不存在或重复")
    row = rows[0]
    response = _seller_read(row, "/v3/product/list", {"filter": {"visibility": "ALL"}, "last_id": str(last_id), "limit": 100})
    result = response.get("result") or response
    raw = result.get("items") or []
    ids = [int(r["product_id"]) for r in raw if r.get("product_id")]
    info = _seller_read(row, "/v3/product/info/list", {"product_id": ids}) if ids else {}
    products = info.get("items") or (info.get("result") or {}).get("items") or []
    price_data = _seller_read(row, "/v5/product/info/prices", {"filter": {"product_id": [str(v) for v in ids], "visibility": "ALL"}, "limit": 100, "cursor": ""}) if ids else {}
    price_map = {str(v.get("product_id")): v for v in price_data.get("items") or (price_data.get("result") or {}).get("items") or []}
    fx = exchange_info()["cny_per_rub"]
    mapping = "Fengjing - Product Corresponding Platform - Main Table"
    bindings = frappe.get_list(mapping, filters={"店铺": store, "启用": 1}, fields=["平台sku", "物料id"], limit_page_length=10000)
    output = []
    for product in products:
        sku_ids = list(dict.fromkeys(str(v) for v in [product.get("sku")] + [s.get("sku") for s in product.get("sources") or [] if isinstance(s, dict)] if v))
        offer = str(product.get("offer_id") or "")
        matches = set(b.get("物料id") for b in bindings if str(b.get("平台sku")) in sku_ids + [offer] and b.get("物料id"))
        item = None
        costs = []
        if len(matches) == 1:
            doc = frappe.get_doc("Item", next(iter(matches)))
            if frappe.has_permission("Item", "read", doc=doc):
                item = {"item_code": doc.name, "item_name": doc.item_name, "image": doc.image, **{f: doc.get(f) for f in PACKAGE_FIELDS.values()}}
                costs = _valuation(doc)
        primary = product.get("primary_image") or product.get("images") or []
        pricing = price_map.get(str(product.get("id") or product.get("product_id")), {})
        price = pricing.get("price") or {}
        amount = _number(price.get("price"))
        currency = str(price.get("currency_code") or product.get("currency_code") or "").upper()
        raw_commissions = pricing.get("commissions") or {}
        commissions = [{"schema": schema, "percent": _number(raw_commissions[key]), "source": "Ozon prices.commissions." + key}
                       for key, schema in (("sales_percent_rfbs", "RFBS"), ("sales_percent_fbs", "FBS"), ("sales_percent_fbo", "FBO"))
                       if isinstance(raw_commissions, dict) and _number(raw_commissions.get(key)) is not None and _number(raw_commissions[key]) < 100]
        output.append({"store": store, "product_id": str(product.get("id") or product.get("product_id") or ""), "ozon_sku_ids": sku_ids,
                       "amount": amount, "currency": currency, "cny": amount if currency == "CNY" else amount * fx if currency == "RUB" and amount is not None and fx else None,
                       "commissions": commissions, "checked_at": str(frappe.utils.now_datetime())[:19], "source": "Ozon 后台 price.price（非买家优惠成交价）",
                       "offer_id": offer, "name": product.get("name"), "image": primary[0] if isinstance(primary, list) and primary else primary,
                       "item": item, "costs": costs, "binding_ambiguous": len(matches) > 1})
    return {"products": output, "last_id": str(result.get("last_id") or ""), "more": len(raw) == 100}




def _product_price(row, identifier):
    query = {"sku": [int(identifier)]} if identifier.isdigit() else {"offer_id": [identifier]}
    info = _seller_read(row, "/v3/product/info/list", query)
    products = info.get("items") or info.get("result", {}).get("items") or []
    if not products and identifier.isdigit():
        info = _seller_read(row, "/v3/product/info/list", {"offer_id": [identifier]})
        products = info.get("items") or info.get("result", {}).get("items") or []
    if len(products) != 1:
        raise ValueError("未找到唯一的 Ozon 商品，检查平台SKU绑定")
    product = products[0]
    product_id = product.get("id") or product.get("product_id")
    if not product_id:
        raise ValueError("商品接口没有返回商品ID")
    payload = _seller_read(row, "/v5/product/info/prices", {
        "filter": {"product_id": [str(product_id)], "visibility": "ALL"}, "limit": 100, "cursor": ""
    })
    prices = payload.get("items") or payload.get("result", {}).get("items") or []
    matching = [p for p in prices if str(p.get("product_id")) == str(product_id)]
    if len(matching) != 1:
        raise ValueError("价格接口未返回唯一匹配商品")
    price = matching[0].get("price") or {}
    currency = str(price.get("currency_code") or product.get("currency_code") or "").upper()
    amount = _number(price.get("price"))
    if amount is None or not currency:
        raise ValueError("后台定价或币种缺失，不能自动使用")
    cny = amount if currency == "CNY" else None
    if currency == "RUB":
        rate = exchange_info()["cny_per_rub"]
        cny = amount * rate if rate else None
    elif currency != "CNY":
        try:
            from erpnext.setup.utils import get_exchange_rate
            rate = _number(get_exchange_rate(currency, "CNY", nowdate()))
            cny = amount * rate if rate else None
        except Exception:
            pass
    primary = product.get("primary_image") or product.get("images") or []
    commissions = []
    raw_commissions = matching[0].get("commissions") or {}
    if isinstance(raw_commissions, dict):
        for key, schema in (("sales_percent_fbo", "FBO"), ("sales_percent_fbs", "FBS"), ("sales_percent_rfbs", "RFBS")):
            percent = _number(raw_commissions.get(key))
            if percent is not None and percent < 100:
                commissions.append({"schema": schema, "percent": percent, "source": "Ozon prices.commissions." + key})
    for entry in product.get("commissions") or []:
        if not isinstance(entry, dict): continue
        schema = str(entry.get("sale_schema") or "").upper()
        percent = _number(entry.get("percent"))
        if schema and percent is not None and percent < 100 and not any(c["schema"] == schema for c in commissions):
            commissions.append({"schema": schema, "percent": percent, "source": "Ozon product.commissions.percent"})
    # SKU is the marketplace SKU, not Seller product_id or the binding identifier.
    sku_ids = list(dict.fromkeys(str(v) for v in
        [product.get("sku")] + [source.get("sku") for source in product.get("sources") or [] if isinstance(source, dict)]
        if v and str(v).isdigit() and int(v) > 0))
    return {"amount": amount, "currency": currency, "cny": cny, "commissions": commissions,
            "ozon_sku_ids": sku_ids,
            "product_id": str(product_id), "offer_id": product.get("offer_id"),
            "image": primary[0] if isinstance(primary, list) and primary else primary,
            "checked_at": str(frappe.utils.now_datetime()), "source": "Ozon 后台 price.price（非买家优惠成交价）"}


@frappe.whitelist()
def item_details(item_code, force=0):
    frappe.has_permission(DOCTYPE, "read", throw=True)
    item = frappe.get_doc("Item", item_code)
    item.check_permission("read")
    result = {"item_code": item_code, "costs": _valuation(item), "prices": [], "errors": []}
    mapping_type = "Fengjing - Product Corresponding Platform - Main Table"
    if not frappe.has_permission(mapping_type, "read"):
        result["errors"].append("没有平台物料绑定表读取权限")
        return result
    bindings = frappe.get_list(mapping_type, filters={"物料id": item_code, "启用": 1}, fields=["店铺", "平台sku"], limit_page_length=100)
    parent = frappe.get_single("Fengjing - Product Corresponding Platform - Configuration")
    if not frappe.has_permission(parent.doctype, "read", doc=parent):
        result["errors"].append("没有 API 配置读取权限")
        return result
    configs = {}
    for row in parent.get("table_wckx") or []:
        if row.get("店铺选项"):
            configs.setdefault(str(row.get("店铺选项")), []).append(row)
    seen = set()
    ozon_bindings = [b for b in bindings if str(b.get("店铺")) in configs]
    if len(ozon_bindings) > 5:
        result["errors"].append("该物料绑定超过5个Ozon商品，请缩小绑定范围后查询")
        return result
    for b in ozon_bindings:
        store, sku = str(b.get("店铺") or ""), str(b.get("平台sku") or "").strip()
        if not sku or (store, sku) in seen: continue
        seen.add((store, sku))
        if len(configs[store]) != 1:
            result["errors"].append(f"{store} 的 API 行不唯一，未自动选用")
            continue
        row = configs[store][0]
        if not row.get("ozon_id") or not row.get("ozon_秘钥"):
            result["errors"].append(f"{store} 缺少 Seller API 凭据")
            continue
        key = "ozfc:price:" + str(row.get("ozon_id")) + ":" + sku
        cached = None if int(force) else frappe.cache.get_value(key)
        if cached and ("commissions" not in cached or "ozon_sku_ids" not in cached): cached = None
        try:
            price = cached or _product_price(row, sku)
            if not cached: frappe.cache.set_value(key, price, expires_in_sec=300)
            result["prices"].append({**price, "store": store, "sku": sku})
        except (ValueError, requests.RequestException) as exc:
            result["errors"].append(f"{store} / {sku}：{str(exc)[:180]}")
    if not result["prices"] and not result["errors"]:
        result["errors"].append("没有找到与 Ozon API 店铺匹配的已启用物料绑定")
    return result


@frappe.whitelist()
def search_items(query="", start=0):
    frappe.has_permission("Item", "read", throw=True)
    meta = frappe.get_meta("Item")
    fields = ["name", "item_code", "item_name", "image"] + [f for f in PACKAGE_FIELDS.values() if meta.has_field(f)]
    return frappe.get_list("Item", fields=fields, filters={"disabled": 0},
                           or_filters={"item_code": ["like", f"%{query[:100]}%"], "item_name": ["like", f"%{query[:100]}%"]},
                           start=max(0, int(start)), page_length=30, order_by="modified desc")


@frappe.whitelist()
def load_canvas(name):
    doc = frappe.get_doc(DOCTYPE, name)
    doc.check_permission("read")
    return dict(name=doc.name, title=doc.canvas_title, canvas=doc.canvas_json, config=doc.freight_config_json, modified=str(doc.modified))


def validate_packing(node):
    plan = node.get("packing")
    if plan is not None:
        if not isinstance(plan, dict) or plan.get("mode") not in ("none", "equal", "custom"):
            frappe.throw("分包方式不正确")
        if "perPack" in plan:
            value = plan["perPack"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 1 <= value <= 10000 or int(value) != value:
                frappe.throw("每包件数必须为1至10000的整数")
        rows = plan.get("rows", [])
        if not isinstance(rows, list) or len(rows) > 100:
            frappe.throw("最多100个包裹")
        for row in rows:
            if not isinstance(row, dict):
                frappe.throw("包裹数据无效")
            quantity = row.get("quantity")
            if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(quantity) or not 1 <= quantity <= 10000 or int(quantity) != quantity:
                frappe.throw("包裹件数必须为有效整数")
            for key in ("length", "width", "height", "weight"):
                value = row.get(key)
                if value in (None, ""):
                    continue
                try:
                    number = float(value)
                except (ValueError, TypeError):
                    frappe.throw("包裹尺寸或重量无效")
                if isinstance(value, bool) or not math.isfinite(number) or number < 0:
                    frappe.throw("包裹尺寸或重量无效")
    quotes = node.get("parcelQuotes", [])
    if not isinstance(quotes, list) or len(quotes) > 100:
        frappe.throw("包裹物流设置无效")
    for position in [node.get("packPos")] + quotes:
        if position is None:
            continue
        if not isinstance(position, dict):
            frappe.throw("包裹物流设置无效")
        if "x" in position or "y" in position:
            if any(isinstance(position.get(k), bool) or not isinstance(position.get(k), (int, float)) or not math.isfinite(position[k]) or abs(position[k]) > 1000000 for k in ("x", "y")):
                frappe.throw("包裹卡片坐标无效")
        if "routeId" in position and not isinstance(position["routeId"], str):
            frappe.throw("包裹物流选择无效")
        filters = position.get("filters", {})
        if not isinstance(filters, dict) or set(filters) - {"destination", "mode", "provider", "speeds"}:
            frappe.throw("物流筛选设置无效")
        if any(not isinstance(filters.get(k, ""), str) or len(filters.get(k, "")) > 200 for k in ("destination", "mode", "provider")):
            frappe.throw("物流筛选设置无效")
        speeds = filters.get("speeds", [])
        if not isinstance(speeds, list) or len(speeds) > 3 or any(v not in ("Express", "Standard", "Economy") for v in speeds):
            frappe.throw("物流速度筛选无效")


@frappe.whitelist(methods=["POST"])
def save_canvas(title, canvas, config, name=None, modified=None, quote_batch=None):
    canvas, config = _object(canvas), validate_config(config)
    if not isinstance(canvas.get("nodes"), list) or len(canvas["nodes"]) > 300:
        frappe.throw("画布最多300张物料卡片")
    view = canvas.get("view", {})
    if not isinstance(view, dict) or any(not isinstance(view.get(k), (int, float)) or not math.isfinite(view[k]) for k in ("x", "y", "z")) or not .05 <= view["z"] <= 5:
        frappe.throw("画布视角数据不正确")
    ids = set()
    for node in canvas["nodes"]:
        if not isinstance(node, dict) or not isinstance(node.get("item"), dict) or not node["item"].get("item_code"):
            frappe.throw("物料卡片数据不完整")
        validate_packing(node)
        identifier = node.get("id", "")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,100}", identifier) or identifier in ids:
            frappe.throw("物料卡片 ID 不正确或重复")
        ids.add(identifier)
        if "manual" in node and not isinstance(node["manual"], bool):
            frappe.throw("空白物料标记必须为布尔值")
        if node.get("manual") and node.get("manualSale", "") != "":
            value = node["manualSale"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                frappe.throw("空白物料售价必须为非负数字")
        if "saleOverride" in node:
            value = node["saleOverride"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                frappe.throw("模拟售价必须为非负数字")
        fees = node.get("fees", {})
        allowed_fees = {"margin_pct", "acquisition_pct", "withdrawal_pct", "returns_rub", "lastmile_rub", "advertising_cny",
                        "margin_enabled", "acquisition_enabled", "withdrawal_enabled", "returns_enabled", "lastmile_enabled", "advertising_enabled"}
        if not isinstance(fees, dict) or set(fees) - allowed_fees:
            frappe.throw("物料费用设置不正确")
        for key, value in fees.items():
            if key.endswith("_enabled"):
                if not isinstance(value, bool):
                    frappe.throw("费用开关必须为布尔值")
                continue
            if value == "":
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (key.endswith("_pct") and value >= 100) or (key == "lastmile_rub" and value > 500):
                frappe.throw("物料费用参数无效")
        quantity = node["item"].get("quantity", 1)
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(quantity) or quantity < 1 or quantity > 10000 or int(quantity) != quantity:
            frappe.throw("数量必须是1至10000之间的整数")
        positions = [node] + [node[k] for k in ("quote", "cost", "sale", "rivals") if k in node]
        if "costEdited" in node and not isinstance(node["costEdited"], bool):
            frappe.throw("模拟成本标记必须为布尔值")
        if node.get("costEdited"):
            try:
                cost_value = float(node["item"]["value"])
            except (TypeError, ValueError, KeyError):
                frappe.throw("模拟成本必须为数字")
            if not math.isfinite(cost_value) or cost_value < 0:
                frappe.throw("模拟成本必须为非负数字")
        if "commissionOverride" in node:
            value = node["commissionOverride"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value < 100:
                frappe.throw("手动佣金必须在0至100%之间")
        for pos in positions:
            if not isinstance(pos, dict) or any(not isinstance(pos.get(k), (int, float)) or not math.isfinite(pos[k]) or abs(pos[k]) > 1_000_000 for k in ("x", "y")):
                frappe.throw("卡片位置不正确")
    title = str(title or "未命名画布").strip()[:140]
    if name:
        # Serialize concurrent saves before checking the revision.
        frappe.db.sql("SELECT name FROM `tabOzon Freight Canvas` WHERE name=%s FOR UPDATE", (name,))
        doc = frappe.get_doc(DOCTYPE, name)
        doc.check_permission("write")
        if str(doc.modified) != str(modified):
            frappe.throw("画布已被其他窗口修改，请导出本地画布后重新打开，避免覆盖")
    else:
        doc = frappe.new_doc(DOCTYPE)
        doc.check_permission("create")
    doc.canvas_title = title
    doc.canvas_json = canvas
    doc.freight_config_json = config
    doc.save()
    has_legacy = any((node.get("competitors") or {}).get("products") and not (node.get("competitors") or {}).get("fromHistoryStore") for node in canvas["nodes"])
    summary = None
    if has_legacy or quote_batch:
        _history_access(doc.name, write=True)
        _migrate_canvas_quotes(doc.name, canvas)
        if quote_batch:
            summary = _import_quote_batch(doc.name, canvas, quote_batch)
        doc.canvas_json = canvas
        doc.save()
    return dict(name=doc.name, modified=str(doc.modified), quote_summary=summary)
