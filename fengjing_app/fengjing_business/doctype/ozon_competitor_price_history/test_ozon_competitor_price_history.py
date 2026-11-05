"""Explicit rollback-only integration checks; no retained business records."""
import copy
import json
import frappe
from fengjing_app.fengjing_business.page.ozon_freight_calcula import ozon_freight_calcula as api


def run_checks():
    store = frappe.get_all("Cost Center", filters={"is_group": 0}, pluck="name", limit=1)[0]
    state = {"version": 2, "nodes": [{"id": "history-test", "x": 0, "y": 0,
             "item": {"item_code": "SIM-history-test", "item_name": "测试"},
             "meta": {"prices": [{"store": store, "product_id": "6013264220"}]}}],
             "view": {"x": 0, "y": 0, "z": 1}}
    raw = {"syncing": False, "competitors": [{"itemId": "6013264220", "sku": "5514900265",
           "url": "https://ozon.ru/product/5552034272/", "price": {"currencyCode": "RUB", "units": "436", "nanos": 750000000}, "rejectionReason": []}]}
    def packet(batch, data):
        return {"batch_id": batch, "payload": {"format": "ozfc-competitors-v1", "results": [
            {"ok": True, "store": store, "itemId": "6013264220", "collectedAt": "2026-09-17T02:00:00Z", "data": data}]}}
    frappe.db.savepoint("quote_history_test")
    try:
        result = api.save_canvas("报价历史测试-回滚", copy.deepcopy(state), api.default_config(), quote_batch=packet("test-batch-1", raw))
        name = result["name"]
        first = api.competitor_catalog(name)["products"][0]
        assert first["version"] == 1 and first["offers"][0]["price"] == 436.75
        assert json.loads(frappe.db.get_value(api.HISTORY, first["record_name"], "raw_json")) == raw
        second_raw = copy.deepcopy(raw)
        second_raw["competitors"][0]["price"]["units"] = "485"
        result = api.save_canvas("报价历史测试-回滚", copy.deepcopy(state), api.default_config(), name, result["modified"], packet("test-batch-2", second_raw))
        latest = api.competitor_catalog(name)["products"][0]
        assert latest["version"] == 2 and len(latest["history"]) == 1
        assert api.competitor_record(first["record_name"])["offers"][0]["price"] == 436.75
        result = api.save_canvas("报价历史测试-回滚", copy.deepcopy(state), api.default_config(), name, result["modified"], packet("test-batch-2", second_raw))
        assert frappe.db.count(api.HISTORY, {"canvas": name}) == 2
        result = api.save_canvas("报价历史测试-回滚", copy.deepcopy(state), api.default_config(), name, result["modified"], packet("test-batch-3", second_raw))
        assert frappe.db.count(api.HISTORY, {"canvas": name}) == 3
        doc = frappe.get_doc(api.HISTORY, first["record_name"])
        try:
            doc.save(ignore_permissions=True)
        except frappe.ValidationError:
            pass
        else:
            raise AssertionError("History must be immutable")
        legacy = copy.deepcopy(state)
        snapshot = {"store": store, "itemId": "6013264220", "offers": api._quote_offers(raw, "6013264220"), "raw": raw}
        current = dict(snapshot, version=2, history=[dict(snapshot, version=1)])
        legacy["nodes"][0]["competitors"] = {"products": [current]}
        migrated = api.save_canvas("旧报价迁移测试-回滚", legacy, api.default_config())
        assert frappe.db.count(api.HISTORY, {"canvas": migrated["name"]}) == 2
        loaded = api.load_canvas(migrated["name"])["canvas"]
        loaded = json.loads(loaded) if isinstance(loaded, str) else loaded
        assert loaded["nodes"][0]["competitors"] == {"external": True}
        api.save_canvas("旧报价迁移测试-回滚", loaded, api.default_config(), migrated["name"], migrated["modified"])
        assert frappe.db.count(api.HISTORY, {"canvas": migrated["name"]}) == 2
        return {"ok": True, "checks": "append, old JSON retained, batch retry deduplication, immutable history, legacy migration; all test records rolled back"}
    finally:
        frappe.db.rollback(save_point="quote_history_test")
