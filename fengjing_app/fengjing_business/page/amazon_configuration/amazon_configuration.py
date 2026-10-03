from __future__ import annotations

from datetime import date, datetime

import frappe
from frappe import _
from frappe.utils import cint


SUPPORTED_FIELD_TYPES = {
	"Autocomplete",
	"Check",
	"Currency",
	"Data",
	"Date",
	"Datetime",
	"Float",
	"Int",
	"Link",
	"Long Text",
	"Password",
	"Percent",
	"Select",
	"Small Text",
	"Text",
}
LAYOUT_FIELD_TYPES = {"Section Break", "Column Break"}
SENSITIVE_FIELDS = {"client_secret", "refresh_token"}


SECTIONS = {
	"stores": {
		"title": "店铺配置",
		"description": "统一管理站点、卖家身份与 SP-API 凭证。",
		"doctype": "Amazon Store Configuration",
		"primary_field": "store_name",
		"enabled_field": "enabled",
		"status_field": "api_status",
		"last_field": "last_tested_at",
		"next_field": None,
		"error_field": "last_error",
		"rename_by_primary": True,
		"actions": [
			{"key": "test", "label": "测试 API", "style": "primary"},
		],
	},
	"ranking": {
		"title": "排名配置",
		"description": "管理商品发现、排名抓取频率和日志保留策略。",
		"doctype": "Amazon Ranking Configuration",
		"primary_field": "amazon_store",
		"enabled_field": "enabled",
		"status_field": "current_task_status",
		"last_field": "last_fetch_at",
		"next_field": "next_fetch_at",
		"error_field": "last_error",
		"progress_field": None,
		"actions": [
			{"key": "discover", "label": "发现商品", "style": "soft"},
			{"key": "latest", "label": "立即抓取", "style": "primary"},
			{"key": "full", "label": "完整同步", "style": "soft", "confirm": True},
		],
	},
	"orders": {
		"title": "订单配置",
		"description": "管理增量订单、历史同步与多周期防漏核对。",
		"doctype": "Amazon Order Configuration",
		"primary_field": "amazon_store",
		"enabled_field": "enabled",
		"status_field": "current_task_status",
		"last_field": "incremental_last_at",
		"next_field": "incremental_next_at",
		"error_field": "last_error",
		"progress_field": "history_progress",
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
		"title": "财务配置",
		"description": "管理财务增量、历史同步和分层核对任务。",
		"doctype": "Amazon Financial Configuration",
		"primary_field": "amazon_store",
		"enabled_field": "enabled",
		"status_field": "current_task_status",
		"last_field": "incremental_last_at",
		"next_field": "incremental_next_at",
		"error_field": "last_error",
		"progress_field": "history_progress",
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
}


ACTION_METHODS = {
	("stores", "test"): "fengjing_app.fengjing_business.doctype.amazon_store_configuration.amazon_store_configuration.test_amazon_store_api",
	("ranking", "discover"): "fengjing_app.fengjing_business.doctype.amazon_ranking_configuration.amazon_ranking_configuration.start_product_discovery",
	("ranking", "latest"): "fengjing_app.fengjing_business.doctype.amazon_ranking_configuration.amazon_ranking_configuration.start_ranking_fetch",
	("ranking", "full"): "fengjing_app.fengjing_business.doctype.amazon_ranking_configuration.amazon_ranking_configuration.start_full_ranking_sync",
	("orders", "latest"): "fengjing_app.fengjing_business.doctype.amazon_order_configuration.amazon_order_configuration.start_incremental_sync",
	("orders", "history"): "fengjing_app.fengjing_business.doctype.amazon_order_configuration.amazon_order_configuration.start_history_sync",
	("finances", "latest"): "fengjing_app.fengjing_business.doctype.amazon_financial_configuration.amazon_financial_configuration.start_incremental_sync",
	("finances", "history"): "fengjing_app.fengjing_business.doctype.amazon_financial_configuration.amazon_financial_configuration.start_history_sync",
}


def _section(section_key):
	section = SECTIONS.get(str(section_key or ""))
	if not section:
		frappe.throw(_("未知的亚马逊配置类型。"))
	return section


def _serialise_value(value):
	if isinstance(value, (date, datetime)):
		return str(value)
	return value


def _field_schema(meta):
	fields = []
	for df in meta.fields:
		if df.hidden or df.fieldtype not in SUPPORTED_FIELD_TYPES | LAYOUT_FIELD_TYPES:
			continue
		sensitive = df.fieldtype == "Password" or df.fieldname in SENSITIVE_FIELDS
		fields.append(
			{
				"fieldname": df.fieldname,
				"fieldtype": "Password" if sensitive else df.fieldtype,
				"label": _(df.label) if df.label else "",
				"options": df.options,
				"description": _(df.description) if df.description else "",
				"default": df.default,
				"reqd": cint(df.reqd),
				"read_only": cint(df.read_only),
				"set_only_once": cint(df.set_only_once),
				"depends_on": df.depends_on,
				"mandatory_depends_on": df.mandatory_depends_on,
				"sensitive": sensitive,
			}
		)
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
		"key": section_key,
		"title": section["title"],
		"description": section["description"],
		"doctype": doctype,
		"primary_field": section["primary_field"],
		"enabled_field": section.get("enabled_field"),
		"status_field": section.get("status_field"),
		"last_field": section.get("last_field"),
		"next_field": section.get("next_field"),
		"error_field": section.get("error_field"),
		"progress_field": section.get("progress_field"),
		"actions": section.get("actions") or [],
		"fields": schema,
		"documents": documents,
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
		"platform": "amazon",
		"title": "亚马逊配置中心",
		"subtitle": "集中管理店铺、排名、订单与财务自动化",
		"generated_at": str(frappe.utils.now_datetime()),
		"sections": [
			_section_payload(section_key, section)
			for section_key, section in SECTIONS.items()
		],
	}


def _editable_fields(meta):
	return {
		df.fieldname: df
		for df in meta.fields
		if df.fieldname
		and not df.hidden
		and not df.read_only
		and df.fieldtype in SUPPORTED_FIELD_TYPES
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

	editable = _editable_fields(frappe.get_meta(doctype))
	for fieldname, df in editable.items():
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
		base = "amazon_order_configuration" if section_key == "orders" else "amazon_financial_configuration"
		method_name = (
			"fengjing_app.fengjing_business.doctype."
			f"{base}.{base}.start_recheck_sync"
		)
		return method_name, days
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
