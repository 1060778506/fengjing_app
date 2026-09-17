"""Explicit diagnostic; creates test records only inside a rolled-back transaction."""
import frappe
from unittest.mock import patch
from .ozon_freight_calcula import bootstrap, default_config, load_canvas, save_canvas, validate_config
from .ozon_freight_calcula import _product_price
from .ozon_freight_calcula import _valuation, validate_packing


def run_warehouse_checks():
    from . import ozon_freight_calcula as mod
    pages = [
        {"warehouses": [{"warehouse_id": 1, "name": "test", "is_rfbs": True}]},
        {"delivery_methods": [{"id": 10, "warehouse_id": 1, "status": "ACTIVE", "name": "CEL Standard Small", "tpl_dropoff_point": {"name": "CEL test", "address": "private"}}, {"id": 11, "warehouse_id": 99, "status": "ACTIVE"}, {"id": 12, "warehouse_id": 1, "status": "INACTIVE"}], "has_next": True, "cursor": "next"},
        {"delivery_methods": [], "has_next": False},
    ]
    with patch.object(mod, "_seller_read", side_effect=pages) as read:
        result = mod._warehouse_channels({}, "shop")
        assert len(result) == 1 and result[0]["dropoff_name"] == "CEL test"
        assert "address" not in str(result) and result[0]["mode"] == "RFBS"
        assert read.call_args_list[-1].args[2]["cursor"] == "next"
    with patch.object(mod, "_seller_read", side_effect=[pages[0], {"has_next": True, "cursor": "same"}, {"has_next": True, "cursor": "same"}]):
        try:
            mod._warehouse_channels({}, "shop")
        except ValueError:
            pass
        else:
            raise AssertionError("Must stop a repeated cursor")
    with patch.object(mod, "_seller_read", side_effect=[{"products": [{"sku": 100, "warehouse_id": 1, "free_stock": 0}, {"sku": 100, "warehouse_id": 2, "free_stock": 100}, {"sku": 101, "warehouse_id": 3, "present": 3, "reserved": 1}, {"sku": 100, "warehouse_id": 4}], "has_next": True, "cursor": "next"}, {"products": [], "has_next": False}]) as read:
        stocks = mod._warehouse_stocks({}, "shop", ["100", "101"])
        assert [s["free_stock"] for s in stocks] == [0, 100, 2, None]
        assert read.call_args_list[-1].args[2]["cursor"] == "next"
    return {"ok": True, "checks": "v2 cursor pagination, active warehouse join, per-SKU stocks preserve zero/positive/unknown; mocked read-only"}


def run_cost_fallback_checks():
    item = frappe._dict(name="test-item", stock_uom="Nos", last_purchase_rate=0)
    prices = [
        frappe._dict(price_list_rate=8, price_list="Buying", uom="Nos", valid_from=None, valid_upto=None, supplier=None),
        frappe._dict(price_list_rate=9, price_list="Buying", uom="Nos", valid_from=None, valid_upto=None, supplier=None),
        frappe._dict(price_list_rate=200, price_list="Other", uom="Box", valid_from=None, valid_upto=None, supplier=None),
        frappe._dict(price_list_rate=300, price_list="Future", uom="Nos", valid_from="2099-01-01", valid_upto=None, supplier=None),
    ]
    with patch.object(frappe, "has_permission", side_effect=lambda dt, *a, **kw: dt == "Item Price"), patch.object(frappe, "get_list", return_value=prices):
        result = _valuation(item)
    assert len(result) == 1 and result[0]["amount"] == 8 and result[0]["currency"] == "CNY"
    def records(dt, **kwargs):
        if dt == "Product Bundle":
            return []
        if dt == "Bin":
            return [frappe._dict(warehouse="warehouse", actual_qty=2, stock_value=0)]
        if dt == "Warehouse":
            return [frappe._dict(name="warehouse", company="company")]
        return prices
    with patch.object(frappe, "has_permission", return_value=True), patch.object(frappe, "get_list", side_effect=records):
        zero_stock = _valuation(item)
    assert zero_stock[0]["amount"] == 8
    def stock_records(dt, **kwargs):
        if dt == "Bin":
            return [frappe._dict(warehouse="warehouse", actual_qty=2, stock_value=20)]
        if dt == "Warehouse":
            return [frappe._dict(name="warehouse", company="company")]
        raise AssertionError("Do not read buying prices when stock valuation is available")
    with patch.object(frappe, "has_permission", return_value=True), patch.object(frappe, "get_list", side_effect=stock_records), patch.object(frappe.db, "get_value", return_value="CNY"):
        stock = _valuation(item)
    assert stock[0]["amount"] == 10
    return {"ok": True, "checks": "buying price fallback; latest per list, matching UOM and validity; mocked read-only"}


def run_bundle_checks():
    kit = frappe._dict(name="kit", stock_uom="Nos", item_name="Kit", last_purchase_rate=0)
    leaf = frappe._dict(name="leaf", stock_uom="Nos", item_name="Leaf", last_purchase_rate=0)
    leaf.check_permission = lambda *a: None
    class Bundle:
        items = [frappe._dict(item_code="leaf", qty=3, uom="Nos")]
        def check_permission(self, *a):
            pass
    def records(dt, **kwargs):
        if dt == "Product Bundle":
            return [frappe._dict(name="bundle")] if kwargs["filters"]["new_item_code"] == "kit" else []
        if dt == "Item Price":
            return [frappe._dict(price_list_rate=4, price_list="Buying", uom="Nos", valid_from=None, valid_upto=None, supplier=None)]
        return []
    with patch.object(frappe, "has_permission", return_value=True), patch.object(frappe, "get_list", side_effect=records), patch.object(frappe, "get_doc", side_effect=lambda dt, name: Bundle() if dt == "Product Bundle" else leaf):
        result = _valuation(kit)
    assert result[0]["amount"] == 12 and result[0]["components"][0]["qty"] == 3
    assert _valuation(kit, {"kit"}) == []
    return {"ok": True, "checks": "bundle quantity-weighted cost, buying fallback, cycle guard; mocked read-only"}


def run_tariff_checks():
    config = validate_config(default_config())
    assert len(config["routes"]) == 148
    xy = [r for r in config["routes"] if r["id"].startswith("兴远-XY-")]
    assert len(xy) == 22
    assert len([r for r in xy if r["mode"] == "FBP"]) == 12
    assert len([r for r in xy if r["destination"] == "吉尔吉斯斯坦"]) == 6
    assert len([r for r in xy if r.get("no_value_limit")]) == 4
    cel = [r for r in config["routes"] if r["id"].startswith("CEL-RFBS-")]
    assert len(cel) == 17 and all(r["mode"] == "RFBS" for r in cel)
    assert len([r for r in cel if r["speed"] == "Standard"]) == 6
    hk = next(r for r in cel if r["id"] == "CEL-RFBS-HK-Express")
    assert (hk["fixed"], hk["rate"], hk["step"], hk["volumetric_min_sum"]) == (19, 96, .1, 60)
    assert len([r for r in config["routes"] if r["provider"] == "兴远"]) == 37
    post = {r["destination"]: r for r in config["routes"] if r["id"].startswith("兴远-Post-")}
    assert post["俄罗斯"]["fixed"] == 12 and post["俄罗斯"]["rate"] == 30
    assert post["哈萨克斯坦"]["fixed"] == 1.6 and post["哈萨克斯坦"]["rate"] == 33
    assert post["白俄罗斯"]["fixed"] == 13 and post["白俄罗斯"]["rate"] == 30
    assert all(r["max_value"] == 1000 and r["value_currency"] == "CNY" and r["max_weight"] == 5 for r in post.values())
    return {"ok": True, "routes": 148, "cel_rfbs_routes": 17, "xy_routes": 37, "xy_new_routes": 22, "checks": "CEL/XY snapshots and limits; no database writes"}


def run_checks():
    config = validate_config(default_config())
    assert len(config["routes"]) == 148
    postal = [r for r in config["routes"] if r["id"].startswith("兴远-Post-")]
    assert len(postal) == 3 and all(r["value_currency"] == "CNY" and r["max_value"] == 1000 for r in postal)
    assert bootstrap()["can_create"]
    canvas = {"version": 2, "displayMode": "table", "nodes": [{"id": "blank-check", "manual": True, "manualSale": 100, "costEdited": True, "item": {"item_code": "SIM-check", "item_name": "空白物料", "value": 8}, "x": 0, "y": 0}], "active": None, "view": {"x": 60, "y": 50, "z": 1}}
    canvas["nodes"][0].update(packing={"mode": "custom", "rows": [{"quantity": 1, "length": 120, "width": 80, "height": 30, "weight": 343}]}, packPos={"x": 370, "y": 0}, parcelQuotes=[{"routeId": config["routes"][0]["id"], "x": 740, "y": 0}])
    validate_packing(canvas["nodes"][0])
    canvas["nodes"][0]["parcelQuotes"][0]["filters"] = {"destination": "俄罗斯", "mode": "RFBS", "provider": "", "speeds": ["Standard", "Economy"]}
    frappe.db.savepoint("freight_canvas_check")
    try:
        first = save_canvas("运费画布诊断-不保留", canvas, config)
        loaded = load_canvas(first["name"])
        assert loaded["title"] == "运费画布诊断-不保留"
        state = loaded["canvas"]
        if isinstance(state, str):
            import json
            state = json.loads(state)
        assert state["displayMode"] == "table"
        assert state["nodes"][0]["manualSale"] == 100
        assert state["nodes"][0]["packing"] == canvas["nodes"][0]["packing"]
        assert state["nodes"][0]["parcelQuotes"] == canvas["nodes"][0]["parcelQuotes"]
        updated = save_canvas("运费画布诊断-更新", canvas, config, first["name"], first["modified"])
        assert updated["name"] == first["name"]
        try:
            save_canvas("不应覆盖", canvas, config, first["name"], "2000-01-01 00:00:00")
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("Stale revision must be rejected")
        return {"ok": True, "routes": len(config["routes"]), "checks": "create / load / update / revision conflict; rolled back"}
    finally:
        frappe.db.rollback(save_point="freight_canvas_check")


def run_price_checks():
    module = "fengjing_app.fengjing_business.page.ozon_freight_calcula.ozon_freight_calcula"
    product = {"items": [{"id": 6013264101, "sku": 5514899686, "offer_id": "MANG-SHOVEL-23", "currency_code": "CNY"}]}
    product["items"][0]["commissions"] = [{"sale_schema": "FBP", "percent": 11}]
    prices = {"items": [{"product_id": 6013264101, "price": {"price": "78.00", "currency_code": "CNY"}, "commissions": {"sales_percent_fbo":17,"sales_percent_fbs":18,"sales_percent_rfbs":12}}]}
    with patch(module + "._seller_read", side_effect=[product, prices]) as read:
        result = _product_price({}, "5514899686")
        assert result["cny"] == 78 and result["currency"] == "CNY"
        assert result["ozon_sku_ids"] == ["5514899686"]
        assert result["offer_id"] == "MANG-SHOVEL-23" and result["product_id"] != result["ozon_sku_ids"][0]
        assert {c["schema"]:c["percent"] for c in result["commissions"]} == {"FBO":17,"FBS":18,"RFBS":12,"FBP":11}
        assert read.call_args_list[0].args[2] == {"sku": [5514899686]}
        assert read.call_args_list[1].args[2]["filter"]["product_id"] == ["6013264101"]
    prices["items"][0]["price"] = {"price": "1500", "currency_code": "RUB"}
    with patch(module + "._seller_read", side_effect=[product, prices]), patch(module + ".exchange_info", return_value={"cny_per_rub": .07952}):
        assert round(_product_price({}, "5514899686")["cny"], 2) == 119.28
    return {"ok": True, "checks": "SKU resolution, exact product match, CNY and RUB price conversion; mocked read-only API"}
