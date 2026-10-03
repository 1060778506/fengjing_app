from __future__ import annotations

from datetime import date, datetime

import frappe
from frappe import _
from frappe.utils import cint


SUPPORTED_FIELD_TYPES = {
	"Autocomplete", "Check", "Currency", "Data", "Date", "Datetime",
	"Float", "Int", "Link", "Long Text", "Password", "Percent", "Select",
	"Small Text", "Text",
}
LAYOUT_FIELD_TYPES = {"Section Break", "Column Break"}
SENSITIVE_FIELDS = {"seller_api_key", "performance_api_key"}


SECTIONS = {
	"stores": {
		"title": "店铺配置", "description": "统一管理 Ozon 店铺身份、Seller API 与 Performance API 凭证。",
		"doctype": "Ozon Store Configuration", "primary_field": "store_name", "enabled_field": "enabled",
		"status_field": "seller_api_status", "last_field": "last_tested_at", "next_field": None,
		"error_field": "last_error", "rename_by_primary": True,
		"actions": [{"key": "test", "label": "测试 API", "style": "primary"}],
	},
	"ranking": {
		"title": "排名配置", "description": "管理商品排名、关键词明细、历史回看和自动抓取。",
		"doctype": "Ozon Ranking Configuration", "primary_field": "ozon_store", "enabled_field": "enabled",
		"status_field": "current_task_status", "last_field": "last_sync_at", "next_field": "next_sync_at",
		"error_field": "last_error", "progress_field": "history_progress",
		"actions": [
			{"key": "latest", "label": "立即抓取", "style": "primary"},
			{"key": "history", "label": "历史同步", "style": "soft", "confirm": True},
		],
	},
	"prices": {
		"title": "价格配置", "description": "管理商品价格记录范围、频率与价格组成。",
		"doctype": "Ozon Price Configuration", "primary_field": "ozon_store", "enabled_field": "enabled",
		"status_field": "current_task_status", "last_field": "last_recorded_at", "next_field": "next_record_at",
		"error_field": "last_error", "progress_field": None,
		"actions": [{"key": "latest", "label": "立即抓取", "style": "primary"}],
	},
	"orders": {
		"title": "订单配置", "description": "管理 FBS、跨境与 FBO 订单增量、历史同步及防漏核对。",
		"doctype": "Ozon Order Configuration", "primary_field": "ozon_store", "enabled_field": "enabled",
		"status_field": "current_task_status", "last_field": "incremental_last_at", "next_field": "incremental_next_at",
		"error_field": "last_error", "progress_field": "history_progress",
		"actions": [
			{"key": "latest", "label": "立即同步", "style": "primary"},
			{"key": "history", "label": "历史同步", "style": "soft", "confirm": True},
			{"key": "recheck_7", "label": "核对7天", "style": "ghost", "confirm": True},
			{"key": "recheck_14", "label": "核对14天", "style": "ghost", "confirm": True},
			{"key": "recheck_30", "label": "核对30天", "style": "ghost", "confirm": True},
			{"key": "recheck_90", "label": "核对90天", "style": "ghost", "confirm": True},
			{"key": "recheck_180", "label": "核对180天", "style": "ghost", "confirm": True},
		],
	},
	"finances": {
		"title": "财务配置", "description": "管理财务流水、历史回看、增量同步与多周期核对。",
		"doctype": "Ozon Financial Configuration", "primary_field": "ozon_store", "enabled_field": "enabled",
		"status_field": "current_task_status", "last_field": "incremental_last_at", "next_field": "incremental_next_at",
		"error_field": "last_error", "progress_field": "history_progress",
		"actions": [
			{"key": "latest", "label": "立即同步", "style": "primary"},
			{"key": "history", "label": "历史同步", "style": "soft", "confirm": True},
			{"key": "recheck_7", "label": "核对7天", "style": "ghost", "confirm": True},
			{"key": "recheck_14", "label": "核对14天", "style": "ghost", "confirm": True},
			{"key": "recheck_30", "label": "核对30天", "style": "ghost", "confirm": True},
			{"key": "recheck_90", "label": "核对90天", "style": "ghost", "confirm": True},
			{"key": "recheck_180", "label": "核对180天", "style": "ghost", "confirm": True},
		],
	},
	"settlements": {
		"title": "结算报告配置", "description": "独立管理半月结算报告的自动回看与历史同步。",
		"doctype": "Ozon Settlement Statement Configuration", "primary_field": "ozon_store", "enabled_field": "enabled",
		"status_field": "current_task_status", "last_field": "last_sync_at", "next_field": "next_sync_at",
		"error_field": "last_error", "progress_field": "history_progress",
		"actions": [
			{"key": "latest", "label": "立即同步", "style": "primary"},
			{"key": "history", "label": "历史同步", "style": "soft", "confirm": True},
		],
	},
}


ACTION_METHODS = {
	("stores", "test"): "fengjing_app.fengjing_business.doctype.ozon_store_configuration.ozon_store_configuration.test_ozon_store_api",
	("ranking", "latest"): "fengjing_app.fengjing_business.doctype.ozon_ranking_configuration.ozon_ranking_configuration.start_latest_sync",
	("ranking", "history"): "fengjing_app.fengjing_business.doctype.ozon_ranking_configuration.ozon_ranking_configuration.start_history_sync",
	("prices", "latest"): "fengjing_app.fengjing_business.doctype.ozon_price_configuration.ozon_price_configuration.start_price_recording",
	("orders", "latest"): "fengjing_app.fengjing_business.doctype.ozon_order_configuration.ozon_order_configuration.start_incremental_sync",
	("orders", "history"): "fengjing_app.fengjing_business.doctype.ozon_order_configuration.ozon_order_configuration.start_history_sync",
	("finances", "latest"): "fengjing_app.fengjing_business.doctype.ozon_financial_configuration.ozon_financial_configuration.start_incremental_sync",
	("finances", "history"): "fengjing_app.fengjing_business.doctype.ozon_financial_configuration.ozon_financial_configuration.start_history_sync",
	("settlements", "latest"): "fengjing_app.fengjing_business.doctype.ozon_settlement_statement_configuration.ozon_settlement_statement_configuration.start_latest_sync",
	("settlements", "history"): "fengjing_app.fengjing_business.doctype.ozon_settlement_statement_configuration.ozon_settlement_statement_configuration.start_history_sync",
}


def _section(section_key):
	section = SECTIONS.get(str(section_key or ""))
	if not section:
		frappe.throw(_("未知的 Ozon 配置类型。"))
	return section


def _serialise_value(value):
	return str(value) if isinstance(value, (date, datetime)) else value


def _field_schema(meta):
	fields = []
	for df in meta.fields:
		if df.hidden or df.fieldtype not in SUPPORTED_FIELD_TYPES | LAYOUT_FIELD_TYPES:
			continue
		sensitive = df.fieldtype == "Password" or df.fieldname in SENSITIVE_FIELDS
		fields.append({
			"fieldname": df.fieldname, "fieldtype": "Password" if sensitive else df.fieldtype,
			"label": _(df.label) if df.label else "", "options": df.options,
			"description": _(df.description) if df.description else "", "default": df.default,
			"reqd": cint(df.reqd), "read_only": cint(df.read_only), "set_only_once": cint(df.set_only_once),
			"depends_on": df.depends_on, "mandatory_depends_on": df.mandatory_depends_on,
			"sensitive": sensitive,
		})
	return fields


def _document_payload(doc, schema):
	values = {"name": doc.name, "modified": str(doc.modified or "")}
	password_set = {}
	for field in schema:
		fieldname = field.get("fieldname")
		if not fieldname or field["fieldtype"] in LAYOUT_FIELD_TYPES:
			continue
		if field.get("sensitive"):
			password_set[fieldname] = bool(doc.get(fieldname))
			values[fieldname] = ""
		else:
			values[fieldname] = _serialise_value(doc.get(fieldname))
	return {"name": doc.name, "values": values, "password_set": password_set}


def _section_payload(section_key, section):
	doctype = section["doctype"]
	frappe.has_permission(doctype, "read", throw=True)
	meta = frappe.get_meta(doctype)
	schema = _field_schema(meta)
	documents = []
	for name in frappe.get_all(doctype, pluck="name", order_by="modified desc"):
		doc = frappe.get_doc(doctype, name)
		doc.check_permission("read")
		documents.append(_document_payload(doc, schema))
	return {
		"key": section_key, "title": section["title"], "description": section["description"],
		"doctype": doctype, "primary_field": section["primary_field"],
		"enabled_field": section.get("enabled_field"), "status_field": section.get("status_field"),
		"last_field": section.get("last_field"), "next_field": section.get("next_field"),
		"error_field": section.get("error_field"), "progress_field": section.get("progress_field"),
		"actions": section.get("actions") or [], "fields": schema, "documents": documents,
		"permissions": {
			"create": frappe.has_permission(doctype, "create"),
			"write": frappe.has_permission(doctype, "write"),
			"delete": frappe.has_permission(doctype, "delete"),
		},
	}


@frappe.whitelist()
def get_dashboard():
	frappe.only_for("System Manager")
	return {
		"platform": "ozon", "title": "Ozon 配置中心",
		"subtitle": "集中管理店铺、排名、价格、订单、财务与结算报告",
		"generated_at": str(frappe.utils.now_datetime()),
		"sections": [_section_payload(key, section) for key, section in SECTIONS.items()],
	}


def _editable_fields(meta):
	return {
		df.fieldname: df for df in meta.fields
		if df.fieldname and not df.hidden and not df.read_only and df.fieldtype in SUPPORTED_FIELD_TYPES
	}


@frappe.whitelist(methods=["POST"])
def save_configuration(section_key, values, name=None):
	frappe.only_for("System Manager")
	section = _section(section_key)
	doctype = section["doctype"]
	values = frappe.parse_json(values) if isinstance(values, str) else (values or {})
	if not isinstance(values, dict):
		frappe.throw(_("配置数据格式不正确。"))
	if name:
		doc = frappe.get_doc(doctype, name)
		doc.check_permission("write")
		primary_field = section.get("primary_field")
		new_name = str(values.get(primary_field) or "").strip()
		if section.get("rename_by_primary") and new_name and new_name != doc.name:
			frappe.rename_doc(doctype, doc.name, new_name, force=False, merge=False)
			doc = frappe.get_doc(doctype, new_name)
	else:
		frappe.has_permission(doctype, "create", throw=True)
		doc = frappe.new_doc(doctype)
	for fieldname, df in _editable_fields(frappe.get_meta(doctype)).items():
		if fieldname not in values or (name and df.set_only_once):
			continue
		value = values.get(fieldname)
		if (df.fieldtype == "Password" or fieldname in SENSITIVE_FIELDS) and value in (None, ""):
			continue
		doc.set(fieldname, value)
	if doc.is_new():
		doc.insert()
	else:
		doc.save()
	return {"name": doc.name, "message": _("配置已保存。")}


@frappe.whitelist(methods=["POST"])
def delete_configuration(section_key, name):
	frappe.only_for("System Manager")
	section = _section(section_key)
	doc = frappe.get_doc(section["doctype"], name)
	doc.check_permission("delete")
	frappe.delete_doc(section["doctype"], name)
	return {"message": _("配置已删除。")}


def _action_method(section_key, action_key):
	method = ACTION_METHODS.get((section_key, action_key))
	if method:
		return method, None
	if section_key in {"orders", "finances"} and str(action_key).startswith("recheck_"):
		days = cint(str(action_key).split("_")[-1])
		if days not in {7, 14, 30, 90, 180}:
			frappe.throw(_("不支持的核对周期。"))
		base = "ozon_order_configuration" if section_key == "orders" else "ozon_financial_configuration"
		return f"fengjing_app.fengjing_business.doctype.{base}.{base}.start_recheck_sync", days
	frappe.throw(_("不支持的操作。"))


@frappe.whitelist(methods=["POST"])
def run_action(section_key, name, action_key):
	frappe.only_for("System Manager")
	section = _section(section_key)
	doc = frappe.get_doc(section["doctype"], name)
	doc.check_permission("write")
	method_name, days = _action_method(section_key, action_key)
	method = frappe.get_attr(method_name)
	return method(name=name, days=days) if days else method(name=name)
