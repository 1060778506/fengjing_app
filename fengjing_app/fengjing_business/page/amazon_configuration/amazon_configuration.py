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
	"fba_inventory": {
		"title": "FBA 库存配置",
		"description": "按当前店铺和国家站点管理 FBA 实时库存快照。",
		"doctype": "Amazon FBA Inventory Configuration",
		"primary_field": "amazon_store",
		"enabled_field": "enabled",
		"status_field": "current_status",
		"last_field": "last_sync_at",
		"next_field": "next_sync_at",
		"error_field": "last_error",
		"progress_field": None,
		"actions": [
			{"key": "latest", "label": "立即同步库存", "style": "primary"},
		],
	},
	"fba_ledger": {
		"title": "FBA 库存分类账配置",
		"description": "统一管理同一卖家的库存分类账任务，并分别查看各国家站点的保存结果。",
		"doctype": "Amazon FBA Inventory Ledger Configuration",
		"primary_field": "amazon_store",
		"enabled_field": "enabled",
		"status_field": "current_status",
		"last_field": "last_sync_at",
		"next_field": "next_sync_at",
		"error_field": "last_error",
		"progress_field": None,
		"actions": [],
		"group_actions": [
			{"key": "group_recheck_7", "label": "立即核对最近7天", "style": "primary", "confirm": True},
			{"key": "group_history", "label": "同步历史范围", "style": "soft", "confirm": True},
			{"key": "group_recheck_14", "label": "核对14天", "style": "ghost", "confirm": True},
			{"key": "group_recheck_30", "label": "核对30天", "style": "ghost", "confirm": True},
			{"key": "group_recheck_90", "label": "核对90天", "style": "ghost", "confirm": True},
			{"key": "group_recheck_180", "label": "核对180天", "style": "ghost", "confirm": True},
		],
	},
	"fba_inbound": {
		"title": "FBA 入库货件配置",
		"description": "统一读取同一卖家的入库计划、货件、商品明细与数量变化。",
		"doctype": "Amazon FBA Inbound Shipment Country Configuration",
		"primary_field": "amazon_store",
		"enabled_field": "enabled",
		"status_field": "current_status",
		"last_field": "last_success_at",
		"next_field": "incremental_next_at",
		"error_field": "last_error",
		"progress_field": None,
		"actions": [],
		"group_actions": [
			{"key": "group_latest", "label": "立即同步", "style": "primary"},
			{"key": "group_history", "label": "同步历史范围", "style": "soft", "confirm": True},
			{"key": "group_recheck_7", "label": "核对7天", "style": "ghost", "confirm": True},
			{"key": "group_recheck_14", "label": "核对14天", "style": "ghost", "confirm": True},
			{"key": "group_recheck_30", "label": "核对30天", "style": "ghost", "confirm": True},
			{"key": "group_recheck_90", "label": "核对90天", "style": "ghost", "confirm": True},
			{"key": "group_recheck_180", "label": "核对180天", "style": "ghost", "confirm": True},
		],
	},
	"awd_inventory": {
		"title": "AWD 库存配置",
		"description": "按当前店铺和国家站点管理 AWD 实时库存及权限状态。",
		"doctype": "Amazon AWD Inventory Configuration",
		"primary_field": "amazon_store",
		"enabled_field": "enabled",
		"status_field": "current_status",
		"last_field": "last_sync_at",
		"next_field": "next_sync_at",
		"error_field": "last_error",
		"progress_field": None,
		"actions": [
			{"key": "latest", "label": "立即同步库存", "style": "primary"},
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
	("fba_inventory", "latest"): "fengjing_app.fengjing_business.doctype.amazon_fba_inventory_configuration.amazon_fba_inventory_configuration.start_inventory_sync",
	("fba_ledger", "history"): "fengjing_app.fengjing_business.doctype.amazon_fba_inventory_ledger_configuration.amazon_fba_inventory_ledger_configuration.start_ledger_sync",
	("awd_inventory", "latest"): "fengjing_app.fengjing_business.doctype.amazon_awd_inventory_configuration.amazon_awd_inventory_configuration.start_inventory_sync",
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


def _ledger_group_payload(documents, section, master_name=None):
	"""按总配置生成公共操作区；不同 API 区域允许归属同一个卖家总配置。"""
	if not documents:
		return {
			"master_name": master_name,
			"status": "Not Started",
			"countries": [],
			"shared": {},
			"mixed_fields": [],
			"actions": section.get("group_actions") or [],
			"actions_ready": False,
			"action_notice": "当前总配置没有国家站点。",
		}

	store_names = [row["values"].get("amazon_store") for row in documents]
	store_names = [name for name in store_names if name]
	stores = {
		row.name: row
		for row in frappe.get_all(
			"Amazon Store Configuration",
			filters={"name": ["in", store_names]},
			fields=["name", "store_name", "country", "marketplace_id", "seller_id", "api_region", "cost_center"],
			limit_page_length=0,
		)
	}

	def aggregate(doctype):
		if not store_names:
			return {}
		table_name = {
			"Amazon FBA Inventory Ledger Summary": "tabAmazon FBA Inventory Ledger Summary",
			"Amazon FBA Inventory Ledger Detail": "tabAmazon FBA Inventory Ledger Detail",
		}[doctype]
		date_field = {
			"Amazon FBA Inventory Ledger Summary": "period_date",
			"Amazon FBA Inventory Ledger Detail": "event_at",
		}[doctype]
		return {
			row.amazon_store: row
			for row in frappe.db.sql(
				f"""SELECT amazon_store, COUNT(name) AS record_count,
					MAX(`{date_field}`) AS latest_date
				FROM `{table_name}`
				WHERE amazon_store IN %(store_names)s
				GROUP BY amazon_store""",  # nosec: names come from fixed mappings above
				{"store_names": tuple(store_names)},
				as_dict=True,
			)
		}

	shared_fields = (
		"history_start_date",
		"history_end_date",
		"history_segment_days",
		"sync_interval_hours",
		"routine_lookback_days",
		"summary_time_aggregation",
		"summary_location_aggregation",
		"detail_event_type",
	)
	master = None
	if master_name:
		master = frappe.db.get_value(
			"Amazon FBA Inventory Ledger Master Configuration",
			master_name,
			["name", "configuration_name", "enabled", "summary_enabled", "detail_enabled", *shared_fields],
			as_dict=True,
		)
	shared = {fieldname: _serialise_value(master.get(fieldname)) if master else None for fieldname in shared_fields}
	summary_stats = aggregate("Amazon FBA Inventory Ledger Summary")
	detail_stats = aggregate("Amazon FBA Inventory Ledger Detail")
	country_order = {"United States": 10, "Canada": 20, "Mexico": 30, "Brazil": 40}
	countries = []
	for document in documents:
		values = document["values"]
		store_name = values.get("amazon_store")
		store = stores.get(store_name)
		summary = summary_stats.get(store_name)
		detail = detail_stats.get(store_name)
		error = values.get("last_error") or values.get("summary_last_error") or values.get("detail_last_error") or ""
		countries.append({
			"configuration_name": document["name"],
			"master_configuration": values.get("master_configuration"),
			"amazon_store": store_name,
			"store_name": store.store_name if store else store_name,
			"country": store.country if store else "",
			"marketplace_id": store.marketplace_id if store else values.get("marketplace_id"),
			"seller_id": store.seller_id if store else "",
			"api_region": store.api_region if store else values.get("api_region"),
			"cost_center": store.cost_center if store else values.get("cost_center"),
			"enabled": cint(values.get("enabled")),
			"status": values.get("current_status") or "Not Started",
			"history_completed": cint(values.get("history_completed")),
			"summary_status": values.get("summary_status") or "Not Started",
			"detail_status": values.get("detail_status") or "Not Started",
			"summary_latest_date": _serialise_value(summary.latest_date) if summary else None,
			"detail_latest_date": _serialise_value(detail.latest_date) if detail else None,
			"summary_records": cint(summary.record_count) if summary else 0,
			"detail_records": cint(detail.record_count) if detail else 0,
			"last_success_at": _serialise_value(values.get("last_success_at")),
			"next_sync_at": _serialise_value(values.get("next_sync_at")),
			"last_result": values.get("last_sync_result") or "",
			"error": error,
		})

	countries.sort(key=lambda row: (country_order.get(row["country"], 99), row["country"] or "", row["store_name"] or ""))
	enabled = [row for row in countries if row["enabled"]]
	statuses = {str(row["status"] or "").lower() for row in enabled}
	if any(row["error"] for row in enabled) or "failed" in statuses:
		overall_status = "Failed"
	elif "running" in statuses:
		overall_status = "Running"
	elif "waiting" in statuses:
		overall_status = "Waiting"
	elif enabled and all(status == "completed" for status in statuses):
		overall_status = "Completed"
	else:
		overall_status = "Not Started"

	waiting_rows = [row for row in enabled if str(row["status"]).lower() == "waiting" and row["next_sync_at"]]
	next_retry_at = min((row["next_sync_at"] for row in waiting_rows), default=None)
	quota_message = next((
		row["last_result"] for row in waiting_rows
		if "频率" in str(row["last_result"]) or "quota" in str(row["last_result"]).lower()
	), "")
	latest_summary_dates = [row["summary_latest_date"] for row in enabled if row["summary_latest_date"]]
	latest_detail_dates = [row["detail_latest_date"] for row in enabled if row["detail_latest_date"]]
	sellers = {str(row["seller_id"] or "").strip().upper() for row in enabled}
	regions = {str(row["api_region"] or "").strip() for row in enabled}
	marketplaces = [str(row["marketplace_id"] or "").strip().upper() for row in enabled]
	readiness_issues = []
	if not enabled:
		readiness_issues.append("没有已启用的国家配置")
	if not master:
		readiness_issues.append("国家配置尚未引用有效的总配置")
	elif not cint(master.enabled):
		readiness_issues.append("总配置未启用")
	if "" in sellers or len(sellers) != 1:
		readiness_issues.append("卖家编号不一致")
	if "" in regions:
		readiness_issues.append("存在无法识别的API区域")
	if "" in marketplaces or len(set(marketplaces)) != len(marketplaces):
		readiness_issues.append("Marketplace ID为空或重复")
	if master and cint(master.summary_enabled) and shared.get("summary_location_aggregation") != "COUNTRY":
		readiness_issues.append("汇总位置粒度不是国家")
	actions_ready = not readiness_issues
	return {
		"master_name": master.name if master else master_name,
		"master_label": master.configuration_name if master else (master_name or "未关联总配置"),
		"status": overall_status,
		"enabled_countries": len(enabled),
		"api_regions": sorted(region for region in regions if region),
		"region_count": len([region for region in regions if region]),
		"countries": countries,
		"shared": shared,
		"mixed_fields": [],
		"actions": section.get("group_actions") or [],
		"actions_ready": actions_ready,
		"action_notice": (
			f"每个API区域只提交一组 Amazon 报告，{len(enabled)}个站点的结果仍分别保存。"
			if actions_ready else "暂时不能运行：" + "、".join(readiness_issues) + "。"
		),
		"readiness_issues": readiness_issues,
		"next_retry_at": next_retry_at,
		"quota_message": quota_message,
		"summary_latest_date": min(latest_summary_dates) if latest_summary_dates else None,
		"detail_latest_date": min(latest_detail_dates) if latest_detail_dates else None,
		"summary_records": sum(row["summary_records"] for row in enabled),
		"detail_records": sum(row["detail_records"] for row in enabled),
	}


def _aggregate_by_configuration(doctype, configuration_names, date_field):
	if not configuration_names:
		return {}
	table_name = {
		"Amazon FBA Inbound Shipment": "tabAmazon FBA Inbound Shipment",
		"Amazon FBA Inbound Shipment Item": "tabAmazon FBA Inbound Shipment Item",
		"Amazon FBA Inbound Shipment History": "tabAmazon FBA Inbound Shipment History",
	}[doctype]
	return {
		row.country_configuration: row
		for row in frappe.db.sql(
			f"""SELECT country_configuration, COUNT(name) AS record_count,
				MAX(`{date_field}`) AS latest_date
			FROM `{table_name}`
			WHERE country_configuration IN %(configuration_names)s
			GROUP BY country_configuration""",  # nosec: table and date field come from fixed mappings
			{"configuration_names": tuple(configuration_names)},
			as_dict=True,
		)
	}


def _inbound_group_payload(documents, section, master_name=None):
	if not documents:
		return {
			"master_name": master_name,
			"status": "Not Started",
			"countries": [],
			"shared": {},
			"actions": section.get("group_actions") or [],
			"actions_ready": False,
			"action_notice": "请先建立入库货件总配置，再建立并关联国家配置。",
		}

	store_names = [row["values"].get("amazon_store") for row in documents]
	store_names = [name for name in store_names if name]
	stores = {
		row.name: row
		for row in frappe.get_all(
			"Amazon Store Configuration",
			filters={"name": ["in", store_names]},
			fields=["name", "store_name", "country", "marketplace_id", "seller_id", "api_region", "cost_center"],
			limit_page_length=0,
		)
	}
	shared_fields = (
		"api_region", "history_start_datetime", "history_end_datetime", "history_segment_days",
		"record_retention_days", "sync_interval_minutes", "incremental_lookback_hours",
		"enable_recheck_7", "recheck_7_interval_days", "enable_recheck_14", "recheck_14_interval_days",
		"enable_recheck_30", "recheck_30_interval_days", "enable_recheck_90", "recheck_90_interval_days",
		"enable_recheck_180", "recheck_180_interval_days",
	)
	master = frappe.db.get_value(
		"Amazon FBA Inbound Shipment Master Configuration",
		master_name,
		["name", "configuration_name", "enabled", *shared_fields],
		as_dict=True,
	) if master_name else None
	shared = {fieldname: _serialise_value(master.get(fieldname)) if master else None for fieldname in shared_fields}

	configuration_names = [row["name"] for row in documents]
	shipment_stats = _aggregate_by_configuration(
		"Amazon FBA Inbound Shipment", configuration_names, "last_fetched_at"
	)
	item_stats = _aggregate_by_configuration(
		"Amazon FBA Inbound Shipment Item", configuration_names, "last_fetched_at"
	)
	history_stats = _aggregate_by_configuration(
		"Amazon FBA Inbound Shipment History", configuration_names, "observed_at"
	)
	plan_stats = frappe.db.sql(
		"""SELECT COUNT(name) AS record_count, MAX(last_fetched_at) AS latest_date
		FROM `tabAmazon FBA Inbound Plan` WHERE master_configuration = %s""",
		master_name,
		as_dict=True,
	)[0] if master_name else frappe._dict(record_count=0, latest_date=None)

	country_order = {"United States": 10, "Canada": 20, "Mexico": 30, "Brazil": 40}
	countries = []
	for document in documents:
		values = document["values"]
		store_name = values.get("amazon_store")
		store = stores.get(store_name)
		shipment = shipment_stats.get(document["name"])
		item = item_stats.get(document["name"])
		history = history_stats.get(document["name"])
		countries.append({
			"configuration_name": document["name"],
			"master_configuration": values.get("master_configuration"),
			"amazon_store": store_name,
			"store_name": store.store_name if store else store_name,
			"country": (store.country if store else None) or values.get("country"),
			"marketplace_id": (store.marketplace_id if store else None) or values.get("marketplace_id"),
			"seller_id": (store.seller_id if store else None) or values.get("seller_id"),
			"api_region": (store.api_region if store else None) or values.get("api_region"),
			"cost_center": (store.cost_center if store else None) or values.get("cost_center"),
			"enabled": cint(values.get("enabled")),
			"status": values.get("current_status") or "Not Started",
			"execution_type": values.get("current_execution_type") or "",
			"history_completed": cint(values.get("history_completed")),
			"history_status": values.get("history_status") or "Not Started",
			"shipment_records": cint(shipment.record_count) if shipment else 0,
			"shipment_latest_at": _serialise_value(shipment.latest_date) if shipment else None,
			"item_records": cint(item.record_count) if item else 0,
			"item_latest_at": _serialise_value(item.latest_date) if item else None,
			"history_records": cint(history.record_count) if history else 0,
			"history_latest_at": _serialise_value(history.latest_date) if history else None,
			"last_success_at": _serialise_value(values.get("last_success_at")),
			"next_sync_at": _serialise_value(values.get("incremental_next_at")),
			"error": values.get("last_error") or values.get("history_last_error") or "",
		})
	countries.sort(key=lambda row: (country_order.get(row["country"], 99), row["country"] or "", row["store_name"] or ""))

	enabled = [row for row in countries if row["enabled"]]
	statuses = {str(row["status"] or "").lower() for row in enabled}
	if any(row["error"] for row in enabled) or "failed" in statuses:
		overall_status = "Failed"
	elif "running" in statuses:
		overall_status = "Running"
	elif "waiting" in statuses:
		overall_status = "Waiting"
	elif enabled and all(status == "completed" for status in statuses):
		overall_status = "Completed"
	else:
		overall_status = "Not Started"

	sellers = {str(row["seller_id"] or "").strip().upper() for row in enabled}
	regions = {str(row["api_region"] or "").strip() for row in enabled}
	marketplaces = [str(row["marketplace_id"] or "").strip().upper() for row in enabled]
	readiness_issues = []
	if not enabled:
		readiness_issues.append("没有已启用的国家配置")
	if not master:
		readiness_issues.append("国家配置尚未引用有效的总配置")
	elif not cint(master.enabled):
		readiness_issues.append("总配置未启用")
	if "" in sellers or len(sellers) != 1:
		readiness_issues.append("卖家编号不一致")
	if "" in regions or len(regions) != 1 or (master and master.api_region not in regions):
		readiness_issues.append("API区域与总配置不一致")
	if "" in marketplaces or len(set(marketplaces)) != len(marketplaces):
		readiness_issues.append("Marketplace ID为空或重复")
	actions_ready = not readiness_issues
	next_times = [row["next_sync_at"] for row in enabled if row["next_sync_at"]]
	return {
		"master_name": master.name if master else master_name,
		"master_label": master.configuration_name if master else (master_name or "未关联总配置"),
		"status": overall_status,
		"enabled_countries": len(enabled),
		"api_regions": sorted(region for region in regions if region),
		"region_count": len([region for region in regions if region]),
		"countries": countries,
		"shared": shared,
		"actions": section.get("group_actions") or [],
		"actions_ready": actions_ready,
		"action_notice": (
			f"由总配置统一读取{len(enabled)}个国家站点，货件结果按国家分别保存。"
			if actions_ready else "暂时不能运行：" + "、".join(readiness_issues) + "。"
		),
		"readiness_issues": readiness_issues,
		"next_sync_at": min(next_times) if next_times else None,
		"plan_records": cint(plan_stats.record_count),
		"plan_latest_at": _serialise_value(plan_stats.latest_date),
		"shipment_records": sum(row["shipment_records"] for row in enabled),
		"item_records": sum(row["item_records"] for row in enabled),
		"history_records": sum(row["history_records"] for row in enabled),
	}


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
	if section_key == "ranking" and documents:
		frappe.has_permission("Amazon Ranking Product", "read", throw=True)
		products = frappe.get_list(
			"Amazon Ranking Product",
			filters={"ranking_configuration": ["in", [row["name"] for row in documents]]},
			fields=[
				"name", "ranking_configuration", "asin", "sku", "product_title",
				"amazon_image_url", "listing_status", "corresponding_item", "enabled",
				"is_competitor", "source", "deleted_from_store", "last_discovered_at",
				"last_fetch_status", "last_fetch_at", "next_fetch_at", "last_error",
			],
			order_by="enabled desc, deleted_from_store asc, product_title asc, asin asc",
			limit_page_length=0,
		)
		by_configuration = {}
		for product in products:
			row = {key: _serialise_value(value) for key, value in product.items()}
			by_configuration.setdefault(product.ranking_configuration, []).append(row)
		for document in documents:
			document["products"] = by_configuration.get(document["name"], [])
	payload = {
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
	if section_key == "fba_ledger":
		by_master = {}
		for document in documents:
			master_name = str(document["values"].get("master_configuration") or "").strip() or None
			by_master.setdefault(master_name, []).append(document)
		payload["groups"] = [
			_ledger_group_payload(rows, section, master_name)
			for master_name, rows in sorted(by_master.items(), key=lambda item: str(item[0] or ""))
		]
		payload["group"] = payload["groups"][0] if payload["groups"] else _ledger_group_payload([], section)
	if section_key == "fba_inbound":
		by_master = {}
		for document in documents:
			master_name = str(document["values"].get("master_configuration") or "").strip() or None
			by_master.setdefault(master_name, []).append(document)
		payload["groups"] = [
			_inbound_group_payload(rows, section, master_name)
			for master_name, rows in sorted(by_master.items(), key=lambda item: str(item[0] or ""))
		]
		payload["group"] = payload["groups"][0] if payload["groups"] else _inbound_group_payload([], section)
	return payload


@frappe.whitelist()
def get_dashboard():
	frappe.only_for("System Manager")
	return {
		"platform": "amazon",
		"title": "亚马逊配置中心",
		"subtitle": "集中管理店铺、排名、订单、财务与库存自动化",
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
	if section_key in {"orders", "finances", "fba_ledger"} and str(action_key).startswith("recheck_"):
		days = cint(str(action_key).split("_")[-1])
		if days not in {7, 14, 30, 90, 180}:
			frappe.throw(_("不支持的核对周期。"))
		base = {
			"orders": "amazon_order_configuration",
			"finances": "amazon_financial_configuration",
			"fba_ledger": "amazon_fba_inventory_ledger_configuration",
		}[section_key]
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


@frappe.whitelist(methods=["POST"])
def run_group_action(section_key, action_key, master_name):
	frappe.only_for("System Manager")
	section_key = str(section_key or "")
	if section_key not in {"fba_ledger", "fba_inbound"}:
		frappe.throw(_("该配置不支持公共任务。"))
	action_key = str(action_key or "").strip()
	allowed = {item["key"] for item in SECTIONS[section_key].get("group_actions", [])}
	if action_key not in allowed:
		frappe.throw(_("不支持的公共配置操作。"))
	if not master_name:
		frappe.throw(_("当前国家配置尚未关联总配置。"))
	if section_key == "fba_inbound":
		method = frappe.get_attr(
			"fengjing_app.fengjing_business.doctype.amazon_fba_inbound_shipment_country_configuration."
			"amazon_fba_inbound_sync.start_group_sync"
		)
		if action_key == "group_latest":
			return method(master_name=master_name, mode="incremental")
		if action_key == "group_history":
			return method(master_name=master_name, mode="history")
		days = cint(action_key.rsplit("_", 1)[-1])
		return method(master_name=master_name, mode="recheck", days=days)
	method = frappe.get_attr(
		"fengjing_app.fengjing_business.doctype.amazon_fba_inventory_ledger_configuration."
		"amazon_fba_inventory_ledger_configuration.start_group_ledger_sync"
	)
	if action_key == "group_history":
		return method(master_name=master_name, action="history")
	days = cint(action_key.rsplit("_", 1)[-1])
	return method(master_name=master_name, action="recheck", days=days)
