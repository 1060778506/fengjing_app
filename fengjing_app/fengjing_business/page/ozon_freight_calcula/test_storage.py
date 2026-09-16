"""Explicit diagnostic; creates test records only inside a rolled-back transaction."""
import frappe
from unittest.mock import patch
from .ozon_freight_calcula import bootstrap, default_config, load_canvas, save_canvas, validate_config
from .ozon_freight_calcula import _product_price
from .ozon_freight_calcula import _valuation


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


def run_checks():
    config = validate_config(default_config())
    assert len(config["routes"]) == 43
    assert bootstrap()["can_create"]
    canvas = {"version": 2, "displayMode": "table", "nodes": [], "active": None, "view": {"x": 60, "y": 50, "z": 1}}
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
        assert {c["schema"]:c["percent"] for c in result["commissions"]} == {"FBO":17,"FBS":18,"RFBS":12,"FBP":11}
        assert read.call_args_list[0].args[2] == {"sku": [5514899686]}
        assert read.call_args_list[1].args[2]["filter"]["product_id"] == ["6013264101"]
    prices["items"][0]["price"] = {"price": "1500", "currency_code": "RUB"}
    with patch(module + "._seller_read", side_effect=[product, prices]), patch(module + ".exchange_info", return_value={"cny_per_rub": .07952}):
        assert round(_product_price({}, "5514899686")["cny"], 2) == 119.28
    return {"ok": True, "checks": "SKU resolution, exact product match, CNY and RUB price conversion; mocked read-only API"}
