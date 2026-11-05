# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""FBA 库存分类账汇总与明细报告同步。"""

import calendar
import csv
import hashlib
import io
import json
import re
from datetime import date, datetime, time, timedelta, timezone

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, getdate, get_datetime, now_datetime

from fengjing_app.fengjing_business.doctype.amazon_store_configuration.amazon_store_configuration import (
	AmazonAPIError,
	create_amazon_report,
	download_amazon_report,
	ensure_database_connection,
	get_region,
	get_store,
	wait_for_amazon_report,
)


DOCTYPE = "Amazon FBA Inventory Ledger Configuration"
MASTER_DOCTYPE = "Amazon FBA Inventory Ledger Master Configuration"
SUMMARY_DOCTYPE = "Amazon FBA Inventory Ledger Summary"
DETAIL_DOCTYPE = "Amazon FBA Inventory Ledger Detail"
TASK_TIMEOUT = 6 * 60 * 60
QUOTA_RETRY_MINUTES = 35
SUMMARY_REPORT_TYPE = "GET_LEDGER_SUMMARY_VIEW_DATA"
DETAIL_REPORT_TYPE = "GET_LEDGER_DETAIL_VIEW_DATA"
RECHECK_DAYS = (7, 14, 30, 90, 180)
MARKETPLACE_COUNTRY_CODES = {
	"ATVPDKIKX0DER": "US",
	"A2EUQ1WTGCTBG2": "CA",
	"A1AM78C64UM0Y8": "MX",
	"A2Q3Y263D00KWC": "BR",
	"A2ZV50J4W1RKNI": "CL",
	"A28R8C7NBKEWEA": "IE",
	"A1RKKUPIHCS9HS": "ES",
	"A1F83G8C2ARO7P": "GB",
	"A13V1IB3VIYZZH": "FR",
	"AMEN7PMS3EDWL": "BE",
	"A1805IZSGTT6HS": "NL",
	"A1PA6795UKMFR9": "DE",
	"APJ6JRA9NG5V4": "IT",
	"A2NODRKZP88ZB9": "SE",
	"AE08WJ6YKNBMC": "ZA",
	"A1C3SOZRARQ6R3": "PL",
	"ARBP9OOSHTCHU": "EG",
	"A33AVAJ2PDY3EV": "TR",
	"A17E79C6D8DWNP": "SA",
	"A2VIGQ35RCS4UG": "AE",
	"A21TJRUUN4KGV": "IN",
	"A19VAU5U5O7RUS": "SG",
	"A39IBJ37TRP1C6": "AU",
	"A1VC38T7YXB528": "JP",
}
COUNTRY_CODES = {
	"US": "US", "USA": "US", "UNITEDSTATES": "US", "UNITEDSTATESOFAMERICA": "US",
	"CA": "CA", "CAN": "CA", "CANADA": "CA",
	"MX": "MX", "MEX": "MX", "MEXICO": "MX",
	"BR": "BR", "BRA": "BR", "BRAZIL": "BR", "BRASIL": "BR",
	"CL": "CL", "CHL": "CL", "CHILE": "CL",
	"GB": "GB", "UK": "GB", "GBR": "GB", "UNITEDKINGDOM": "GB", "GREATBRITAIN": "GB",
	"IE": "IE", "IRL": "IE", "IRELAND": "IE",
	"DE": "DE", "DEU": "DE", "GERMANY": "DE", "DEUTSCHLAND": "DE",
	"FR": "FR", "FRA": "FR", "FRANCE": "FR",
	"IT": "IT", "ITA": "IT", "ITALY": "IT", "ITALIA": "IT",
	"ES": "ES", "ESP": "ES", "SPAIN": "ES", "ESPANA": "ES",
	"NL": "NL", "NLD": "NL", "NETHERLANDS": "NL", "HOLLAND": "NL",
	"BE": "BE", "BEL": "BE", "BELGIUM": "BE",
	"SE": "SE", "SWE": "SE", "SWEDEN": "SE",
	"PL": "PL", "POL": "PL", "POLAND": "PL",
	"TR": "TR", "TUR": "TR", "TURKEY": "TR", "TURKIYE": "TR",
	"SA": "SA", "SAU": "SA", "SAUDIARABIA": "SA",
	"AE": "AE", "ARE": "AE", "UNITEDARABEMIRATES": "AE", "UAE": "AE",
	"EG": "EG", "EGY": "EG", "EGYPT": "EG",
	"ZA": "ZA", "ZAF": "ZA", "SOUTHAFRICA": "ZA",
	"IN": "IN", "IND": "IN", "INDIA": "IN",
	"JP": "JP", "JPN": "JP", "JAPAN": "JP",
	"AU": "AU", "AUS": "AU", "AUSTRALIA": "AU",
	"SG": "SG", "SGP": "SG", "SINGAPORE": "SG",
}


class AmazonFBAInventoryLedgerConfiguration(Document):
	def validate(self):
		store = get_store(self.amazon_store, require_enabled=False) if self.amazon_store else None
		if store:
			self.cost_center = store.cost_center
			self.marketplace_id = store.marketplace_id
			self.api_region = get_region(store)
			if cint(self.enabled) and not cint(store.enabled):
				frappe.throw(_("启用国家配置前，请先启用关联的 Amazon 店铺。"))
		if cint(self.enabled) and not self.master_configuration:
			frappe.throw(_("启用国家配置前，请先选择库存分类账总配置。"))
		if self.master_configuration:
			master = frappe.get_doc(MASTER_DOCTYPE, self.master_configuration)
			if cint(self.enabled) and not cint(master.enabled):
				frappe.throw(_("当前库存分类账总配置尚未启用。"))
			if cint(self.enabled) and store:
				other_stores = frappe.get_all(
					DOCTYPE,
					filters={
						"master_configuration": self.master_configuration,
						"enabled": 1,
						"name": ["!=", self.name or ""],
					},
					pluck="amazon_store",
				)
				seller_ids = {
					str(get_store(name, require_enabled=False).seller_id or "").strip().upper()
					for name in other_stores if name
				}
				seller_ids.add(str(store.seller_id or "").strip().upper())
				if "" in seller_ids or len(seller_ids) != 1:
					frappe.throw(_("同一个库存分类账总配置只能关联同一个 Amazon 卖家的店铺。"))
		if not self.is_new() and self.has_value_changed("master_configuration"):
			self.history_completed = 0
			self.history_checkpoint = None
			self.summary_checkpoint = None
			self.detail_checkpoint = None
			self.summary_status = "Not Started"
			self.detail_status = "Not Started"


def _configuration(name):
	ensure_database_connection()
	return frappe.get_doc(DOCTYPE, name)


def _update_configuration(name, **values):
	ensure_database_connection()
	valid = set(frappe.get_meta(DOCTYPE).get_valid_columns())
	values = {key: value for key, value in values.items() if key in valid}
	if values:
		frappe.db.set_value(DOCTYPE, name, values, update_modified=False)
		frappe.db.commit()


def _master_configuration(name):
	ensure_database_connection()
	return frappe.get_doc(MASTER_DOCTYPE, name)


def _group_lock(master_name):
	key = hashlib.sha256(str(master_name).encode("utf-8")).hexdigest()[:24]
	return frappe.cache().lock(
		frappe.cache().make_key(f"fengjing:amazon-fba-ledger-group:{key}"),
		timeout=TASK_TIMEOUT,
		blocking_timeout=0,
	)


def _enabled_group(master_name, configuration_names=None):
	master = _master_configuration(master_name)
	if not cint(master.enabled):
		frappe.throw(_("库存分类账总配置尚未启用。"))
	filters = {"enabled": 1, "master_configuration": master.name}
	if configuration_names:
		filters["name"] = ["in", list(configuration_names)]
	names = frappe.get_all(DOCTYPE, filters=filters, pluck="name", order_by="name asc")
	configurations = [_configuration(name) for name in names]
	if not configurations:
		frappe.throw(_("当前总配置没有已启用的 FBA 库存分类账国家配置。"))
	stores = [get_store(config.amazon_store) for config in configurations]
	regions = _validate_group(master, configurations, stores)
	return master, configurations, stores, regions


def _validate_group(master, configurations, stores):
	if len(configurations) != len(stores):
		frappe.throw(_("FBA 库存分类账配置与店铺数量不一致。"))
	sellers = {str(store.seller_id or "").strip().upper() for store in stores}
	if "" in sellers or len(sellers) != 1:
		frappe.throw(_("同一个总配置要求所有店铺使用同一个 Amazon 卖家编号。"))
	marketplaces = [str(store.marketplace_id or "").strip().upper() for store in stores]
	if len(set(marketplaces)) != len(marketplaces):
		frappe.throw(_("同一个总配置中存在重复的 Amazon Marketplace ID。"))
	if cint(master.summary_enabled) and str(master.summary_location_aggregation or "").upper() != "COUNTRY":
		frappe.throw(_("多站点合并抓取要求汇总位置粒度使用“国家”。"))
	regions = {}
	for config, store in zip(configurations, stores):
		regions.setdefault(get_region(store), []).append((config, store))
	return regions


def _enqueue_group(master_name, reset=False, recheck_days=0, routine=False):
	recheck_days = cint(recheck_days)
	identity = hashlib.sha256(str(master_name).encode("utf-8")).hexdigest()[:16]
	job_suffix = f"-recheck-{recheck_days}" if recheck_days else ("-routine" if routine else "-history")
	frappe.enqueue(
		execute_group_ledger_sync,
		queue="long",
		timeout=TASK_TIMEOUT,
		enqueue_after_commit=True,
		job_id=f"amazon-fba-ledger-group-{identity}{job_suffix}",
		deduplicate=True,
		master_name=master_name,
		reset=reset,
		recheck_days=recheck_days,
		routine=routine,
	)


def _report_datetime(value, end=False):
	d = getdate(value)
	dt = datetime.combine(d, time.max if end else time.min, tzinfo=timezone.utc)
	return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _configured_range(config):
	if not config.history_start_date or not config.history_end_date:
		frappe.throw(_("请先填写历史开始日期和历史结束日期。"))
	start = getdate(config.history_start_date)
	end = getdate(config.history_end_date)
	if end < start:
		frappe.throw(_("可同步的历史结束日期不能早于开始日期。"))
	return start, end


def _effective_range(config, kind):
	"""返回 Amazon 能接受的完整日期范围，避免提交尚未结束的日、周或月。"""
	start, configured_end = _configured_range(config)
	end = min(configured_end, getdate(now_datetime()) - timedelta(days=1))
	if kind == "summary" and config.summary_time_aggregation == "WEEKLY":
		end -= timedelta(days=(end.weekday() + 1) % 7)
	elif kind == "summary" and config.summary_time_aggregation == "MONTHLY":
		last_day = calendar.monthrange(end.year, end.month)[1]
		if end.day != last_day:
			end = end.replace(day=1) - timedelta(days=1)
	if end < start:
		label = _("汇总报告") if kind == "summary" else _("明细报告")
		frappe.throw(_("{0}目前没有已经完整结束的可同步日期范围。").format(label))
	return start, end


def _lookback_range(config, kind, days):
	"""按最近完整天数生成范围；周、月汇总自动对齐完整周期。"""
	days = cint(days)
	if days < 1:
		frappe.throw(_("回看天数必须大于0。"))
	end = getdate(now_datetime()) - timedelta(days=1)
	start = end - timedelta(days=days - 1)
	if kind == "summary" and config.summary_time_aggregation == "WEEKLY":
		end -= timedelta(days=(end.weekday() + 1) % 7)
		start -= timedelta(days=start.weekday())
	elif kind == "summary" and config.summary_time_aggregation == "MONTHLY":
		last_day = calendar.monthrange(end.year, end.month)[1]
		if end.day != last_day:
			end = end.replace(day=1) - timedelta(days=1)
		start = min(start.replace(day=1), end.replace(day=1))
	if end < start:
		label = _("汇总报告") if kind == "summary" else _("明细报告")
		frappe.throw(_("{0}目前没有已经完整结束的可核对日期范围。").format(label))
	return start, end


def _recent_recheck_range(config, kind, days):
	"""按7～180天按钮生成核对范围。"""
	days = cint(days)
	if days not in RECHECK_DAYS:
		frappe.throw(_("不支持的核对周期。"))
	return _lookback_range(config, kind, days)


def _routine_recheck_range(config, kind):
	"""日常复核独立使用回看天数，不复用历史分段天数。"""
	return _lookback_range(config, kind, max(cint(config.routine_lookback_days), 1))


def _normalise_header(value):
	return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _read_report_rows(text):
	if not str(text or "").strip():
		return []
	sample = str(text)[:4096]
	delimiter = "\t" if "\t" in sample else ","
	reader = csv.DictReader(io.StringIO(str(text)), delimiter=delimiter)
	return [dict(row) for row in reader if row and any(str(value or "").strip() for value in row.values())]


def _row_value(row, *aliases):
	values = {_normalise_header(key): value for key, value in (row or {}).items()}
	for alias in aliases:
		key = _normalise_header(alias)
		if key in values and str(values[key] or "").strip() != "":
			return str(values[key]).strip()
	return ""


def _number(value):
	text = str(value or "0").strip().replace(",", "")
	try:
		return int(float(text))
	except (TypeError, ValueError):
		return 0


def _row_date(value, fallback):
	text = str(value or "").strip()
	if not text:
		return getdate(fallback)
	for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
		try:
			return datetime.strptime(text[:10], pattern).date()
		except ValueError:
			continue
	try:
		return getdate(text)
	except Exception:
		return getdate(fallback)


def _row_datetime(value, fallback):
	text = str(value or "").strip()
	if not text:
		return datetime.combine(getdate(fallback), time.min)
	try:
		return get_datetime(text)
	except Exception:
		return datetime.combine(_row_date(text, fallback), time.min)


def _item_mapping(store, sku, asin=None):
	from fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log import (
		获取平台映射物料,
	)

	item_code = 获取平台映射物料(
		store.cost_center,
		store.marketplace_id,
		asin=asin,
		sku=sku,
	)
	item_name = frappe.db.get_value("Item", item_code, "item_name") if item_code else None
	return item_code, item_name


def _upsert(doctype, key_field, key, values, raw_hash):
	name = frappe.db.get_value(doctype, {key_field: key}, "name")
	values.update({key_field: key, "raw_json_hash": raw_hash})
	if not name:
		frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)
		return "created"
	current_hash = frappe.db.get_value(doctype, name, "raw_json_hash")
	frappe.db.set_value(doctype, name, values, update_modified=False)
	return "unchanged" if current_hash == raw_hash else "updated"


def _save_summary_rows(config, store, rows, report_id, batch_id, start, end, policy=None):
	policy = policy or config
	stats = {"created": 0, "updated": 0, "unchanged": 0, "rows": 0}
	for row in rows:
		sku = _row_value(row, "MSKU", "Seller SKU", "SKU")
		fnsku = _row_value(row, "FNSKU")
		asin = _row_value(row, "ASIN").upper()
		if not sku and not fnsku and not asin:
			continue
		period_date = _row_date(_row_value(row, "Date", "Period Date"), start)
		location = _row_value(row, "Location", "Fulfillment Center")
		disposition = _row_value(row, "Disposition")
		item_code, item_name = _item_mapping(store, sku, asin)
		raw_text = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
		identity = "|".join(
			(
				store.name,
				str(store.marketplace_id or ""),
				str(period_date),
				sku,
				fnsku,
				asin,
				disposition,
				location,
				str(policy.summary_time_aggregation or ""),
			)
		)
		transfer = _number(_row_value(row, "Warehouse Transfer In/Out", "Warehouse Transfer"))
		values = {
			"amazon_store": store.name,
			"cost_center": store.cost_center,
			"marketplace_id": store.marketplace_id,
			"country": store.country,
			"seller_sku": sku or fnsku or asin,
			"fnsku": fnsku,
			"asin": asin,
			"product_name": _row_value(row, "Title", "Product Name"),
			"corresponding_item": item_code,
			"corresponding_item_name": item_name,
			"disposition": disposition,
			"period_date": period_date,
			"report_start_date": start,
			"report_end_date": end,
			"time_aggregation": policy.summary_time_aggregation,
			"location_type": policy.summary_location_aggregation,
			"location_id": location,
			"report_id": report_id,
			"starting_warehouse_balance": _number(_row_value(row, "Starting Warehouse Balance")),
			"receipts": _number(_row_value(row, "Receipts")),
			"customer_shipments": _number(_row_value(row, "Customer Shipments")),
			"customer_returns": _number(_row_value(row, "Customer Returns")),
			"vendor_returns": _number(_row_value(row, "Vendor Returns")),
			"ending_warehouse_balance": _number(_row_value(row, "Ending Warehouse Balance")),
			"in_transit_between_warehouses": _number(_row_value(row, "In Transit Between Warehouses")),
			"warehouse_transfer_in": transfer if transfer > 0 else 0,
			"warehouse_transfer_out": abs(transfer) if transfer < 0 else 0,
			"found_quantity": _number(_row_value(row, "Found")),
			"lost_quantity": _number(_row_value(row, "Lost")),
			"damaged_quantity": _number(_row_value(row, "Damaged")),
			"disposed_quantity": _number(_row_value(row, "Disposed")),
			"other_events_quantity": _number(_row_value(row, "Other Events")),
			"unknown_events_quantity": _number(_row_value(row, "Unknown Events")),
			"sync_batch_id": batch_id,
			"fetched_at": now_datetime(),
			"raw_json": json.dumps(row, ensure_ascii=False, indent=2),
		}
		action = _upsert(
			SUMMARY_DOCTYPE,
			"summary_key",
			hashlib.sha256(identity.encode("utf-8")).hexdigest(),
			values,
			hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
		)
		stats[action] += 1
		stats["rows"] += 1
	return stats


def _save_detail_rows(config, store, rows, report_id, batch_id, start, end):
	stats = {"created": 0, "updated": 0, "unchanged": 0, "rows": 0}
	for row in rows:
		sku = _row_value(row, "MSKU", "Seller SKU", "SKU")
		fnsku = _row_value(row, "FNSKU")
		asin = _row_value(row, "ASIN").upper()
		event_type = _row_value(row, "Event Type", "Event")
		if not sku and not fnsku and not asin:
			continue
		event_at = _row_datetime(_row_value(row, "Date", "Event Date", "Event Time"), start)
		reference_id = _row_value(row, "Reference ID", "Reference")
		fulfillment_center = _row_value(row, "Fulfillment Center", "Location")
		disposition = _row_value(row, "Disposition")
		reason = _row_value(row, "Reason")
		quantity = _number(_row_value(row, "Quantity"))
		item_code, item_name = _item_mapping(store, sku, asin)
		raw_text = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
		identity = "|".join(
			(
				store.name,
				str(store.marketplace_id or ""),
				str(event_at),
				event_type,
				reference_id,
				sku,
				fnsku,
				asin,
				fulfillment_center,
				disposition,
				reason,
				str(quantity),
			)
		)
		values = {
			"amazon_store": store.name,
			"cost_center": store.cost_center,
			"marketplace_id": store.marketplace_id,
			"country": store.country,
			"seller_sku": sku or fnsku or asin,
			"fnsku": fnsku,
			"asin": asin,
			"product_name": _row_value(row, "Title", "Product Name"),
			"corresponding_item": item_code,
			"corresponding_item_name": item_name,
			"event_at": event_at,
			"event_type": event_type or "UNKNOWN",
			"reference_id": reference_id,
			"quantity": quantity,
			"fulfillment_center": fulfillment_center,
			"disposition": disposition,
			"reason": reason,
			"report_id": report_id,
			"reconciled_quantity": _number(_row_value(row, "Reconciled Quantity")),
			"unreconciled_quantity": _number(_row_value(row, "Unreconciled Quantity")),
			"sync_batch_id": batch_id,
			"fetched_at": now_datetime(),
			"raw_json": json.dumps(row, ensure_ascii=False, indent=2),
		}
		action = _upsert(
			DETAIL_DOCTYPE,
			"detail_key",
			hashlib.sha256(identity.encode("utf-8")).hexdigest(),
			values,
			hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
		)
		stats[action] += 1
		stats["rows"] += 1
	return stats


def _country_code(value):
	normalized = re.sub(r"[^A-Z]", "", str(value or "").strip().upper())
	return COUNTRY_CODES.get(normalized)


def _store_country_code(store):
	return _country_code(store.country) or MARKETPLACE_COUNTRY_CODES.get(
		str(store.marketplace_id or "").strip().upper()
	)


def _split_group_rows(rows, stores, kind):
	"""按报告中的国家列拆分；无法识别的行不会写入错误国家。"""
	by_country = {_store_country_code(store): store for store in stores if _store_country_code(store)}
	by_store = {store.name: [] for store in stores}
	unrouted = []
	for row in rows:
		aliases = ("Country", "Country Code", "Marketplace Country")
		if kind == "summary":
			aliases += ("Location",)
		country_value = _row_value(row, *aliases)
		store = by_country.get(_country_code(country_value))
		if not store and len(stores) == 1:
			store = stores[0]
		if store:
			by_store[store.name].append(row)
		else:
			unrouted.append(row)
	return by_store, unrouted


def _empty_stats():
	return {"created": 0, "updated": 0, "unchanged": 0, "rows": 0}


def _request_group_report(master, targets, kind, start, end, batch_id):
	configurations = [config for config, _store in targets]
	stores = [store for _config, store in targets]
	primary_store = stores[0]
	if kind == "summary":
		report_type = SUMMARY_REPORT_TYPE
		options = {
			"aggregatedByTimePeriod": master.summary_time_aggregation,
			"aggregateByLocation": master.summary_location_aggregation,
		}
	else:
		report_type = DETAIL_REPORT_TYPE
		options = {}
		if str(master.detail_event_type or "").strip():
			options["eventType"] = str(master.detail_event_type).strip()
	report_id = create_amazon_report(
		primary_store,
		report_type,
		marketplace_ids=[store.marketplace_id for store in stores],
		data_start_time=_report_datetime(start),
		data_end_time=_report_datetime(end, end=True),
		report_options=options,
	)
	for config in configurations:
		_update_configuration(
			config.name,
			**{
				f"{kind}_last_report_id": report_id,
				f"{kind}_status": "Running",
				f"{kind}_last_error": "",
			},
		)
	metadata = wait_for_amazon_report(primary_store, report_id, timeout_seconds=3600, poll_seconds=15)
	if str(metadata.get("processingStatus") or "").upper() == "CANCELLED":
		return report_id, {config.name: {**_empty_stats(), "no_data": True} for config in configurations}
	text, _document = download_amazon_report(primary_store, metadata.get("reportDocumentId"))
	ensure_database_connection()
	rows = _read_report_rows(text)
	rows_by_store, unrouted = _split_group_rows(rows, stores, kind)
	results = {}
	for config, store in targets:
		store_rows = rows_by_store.get(store.name, [])
		if kind == "summary":
			results[config.name] = _save_summary_rows(
				config, store, store_rows, report_id, batch_id, start, end, policy=master
			)
		else:
			results[config.name] = _save_detail_rows(
				config, store, store_rows, report_id, batch_id, start, end
			)
	if unrouted:
		results["unrouted_rows"] = len(unrouted)
		frappe.logger("amazon_fba_inventory_ledger", allow_site=True).warning(
			"Grouped FBA ledger report left rows unrouted: report_id=%s kind=%s rows=%s",
			report_id,
			kind,
			len(unrouted),
		)
	frappe.db.commit()
	return report_id, results


def _process_group_kind(master, targets, kind, start, end, batch_id, force_start=False):
	checkpoint_field = f"{kind}_checkpoint"
	last_sync_field = f"{kind}_last_sync_at"
	status_field = f"{kind}_status"
	error_field = f"{kind}_last_error"
	checkpoint_values = [config.get(checkpoint_field) for config, _store in targets]
	checkpoints = [getdate(value) for value in checkpoint_values if value]
	cursor = start if force_start or len(checkpoints) != len(targets) else min(checkpoints)
	cursor = max(cursor, start)
	result = {config.name: {**_empty_stats(), "reports": 0} for config, _store in targets}
	unrouted_rows = 0
	for config, _store in targets:
		_update_configuration(config.name, **{status_field: "Running", error_field: ""})
	while cursor <= end:
		if force_start:
			segment_end = end
		elif kind == "summary" and master.summary_time_aggregation == "WEEKLY":
			segment_end = min(end, cursor + timedelta(days=6))
		elif kind == "summary" and master.summary_time_aggregation == "MONTHLY":
			month_end = date(cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1])
			segment_end = min(end, month_end)
		else:
			segment_end = min(
				end,
				cursor + timedelta(days=max(cint(master.history_segment_days), 1) - 1),
			)
		_report_id, report_results = _request_group_report(
			master, targets, kind, cursor, segment_end, batch_id
		)
		unrouted_rows += cint(report_results.get("unrouted_rows"))
		for config, _store in targets:
			stats = report_results.get(config.name, _empty_stats())
			for key in ("created", "updated", "unchanged", "rows"):
				result[config.name][key] += cint(stats.get(key))
			result[config.name]["reports"] += 1
		cursor = segment_end + timedelta(days=1)
		for config, _store in targets:
			values = {
				last_sync_field: now_datetime(),
				status_field: "Completed" if cursor > end else "Running",
				error_field: "",
			}
			if not force_start:
				values[checkpoint_field] = cursor
			_update_configuration(config.name, **values)
	result["unrouted_rows"] = unrouted_rows
	return result


def _delete_expired_records(policy, store):
	days = cint(policy.snapshot_retention_days)
	if days <= 0:
		return 0
	cutoff = add_days(getdate(now_datetime()), -days)
	deleted = 0
	for doctype, date_field in ((SUMMARY_DOCTYPE, "period_date"), (DETAIL_DOCTYPE, "event_at")):
		names = frappe.get_all(
			doctype,
			filters={"amazon_store": store.name, date_field: ["<", cutoff]},
			pluck="name",
			limit_page_length=0,
		)
		if names:
			frappe.db.delete(doctype, {"name": ["in", names]})
			deleted += len(names)
	if deleted:
		frappe.db.commit()
	return deleted


@frappe.whitelist()
def start_ledger_sync(name):
	"""兼容旧入口；国家配置按钮统一转到其所属总配置。"""
	config = _configuration(name)
	config.check_permission("write")
	if not config.master_configuration:
		frappe.throw(_("请先为国家配置选择库存分类账总配置。"))
	return start_group_ledger_sync(config.master_configuration, action="history")


@frappe.whitelist()
def start_recheck_sync(name, days):
	"""兼容旧入口；核对任务统一由总配置调度。"""
	config = _configuration(name)
	config.check_permission("write")
	if not config.master_configuration:
		frappe.throw(_("请先为国家配置选择库存分类账总配置。"))
	return start_group_ledger_sync(config.master_configuration, action="recheck", days=days)


@frappe.whitelist()
def start_group_ledger_sync(master_name, action="history", days=0):
	"""按总配置启动任务；同一区域的多个站点只提交一组 Amazon 报告。"""
	master, configurations, stores, regions = _enabled_group(master_name)
	master.check_permission("write")
	for config in configurations:
		config.check_permission("write")
	action = str(action or "history").strip().lower()
	days = cint(days)
	if action not in {"history", "recheck"}:
		frappe.throw(_("不支持的公共分类账操作。"))
	if action == "recheck" and days not in RECHECK_DAYS:
		frappe.throw(_("不支持的核对周期。"))
	if cint(master.summary_enabled):
		(_recent_recheck_range(master, "summary", days) if action == "recheck" else _effective_range(master, "summary"))
	if cint(master.detail_enabled):
		(_recent_recheck_range(master, "detail", days) if action == "recheck" else _effective_range(master, "detail"))

	for config in configurations:
		values = {"current_status": "Waiting", "last_error": ""}
		if action == "history":
			values.update({"history_completed": 0, "history_checkpoint": None})
		if cint(master.summary_enabled):
			values.update({"summary_status": "Waiting", "summary_last_error": ""})
			if action == "history":
				values["summary_checkpoint"] = None
		if cint(master.detail_enabled):
			values.update({"detail_status": "Waiting", "detail_last_error": ""})
			if action == "history":
				values["detail_checkpoint"] = None
		_update_configuration(config.name, **values)
	_enqueue_group(
		master.name,
		reset=action == "history",
		recheck_days=days if action == "recheck" else 0,
	)
	if action == "recheck":
		message = _("FBA 库存分类账最近{0}天核对已进入后台队列；{1}个站点将按{2}个API区域合并请求。").format(
			days, len(stores), len(regions)
		)
	else:
		message = _("FBA 库存分类账历史同步已进入后台队列；{0}个站点将按{1}个API区域合并请求。").format(
			len(stores), len(regions)
		)
	return {
		"status": "queued",
		"master_name": master.name,
		"marketplaces": len(stores),
		"regions": len(regions),
		"message": message,
	}


def _next_recheck(now, interval_days):
	return get_datetime(now).replace(hour=2, minute=59, second=0, microsecond=0) + timedelta(
		days=max(cint(interval_days), 1)
	)


def _first_recheck(now):
	now = get_datetime(now)
	candidate = now.replace(hour=2, minute=59, second=0, microsecond=0)
	return candidate if candidate > now else candidate + timedelta(days=1)


def _is_quota_exceeded(exc):
	message = str(exc or "").lower()
	return (
		isinstance(exc, AmazonAPIError) and exc.status_code == 429
	) or "http 429" in message or "quotaexceeded" in message


def _quota_retry_at(exc):
	retry_after = max(cint(getattr(exc, "retry_after_seconds", 0)), QUOTA_RETRY_MINUTES * 60)
	return now_datetime() + timedelta(seconds=retry_after)


def execute_group_ledger_sync(master_name, reset=False, recheck_days=0, routine=False):
	master, configurations, stores, regions = _enabled_group(master_name)
	lock = _group_lock(master.name)
	if not lock.acquire(blocking=False):
		return {"status": "busy", "message": "FBA inventory ledger master task is already running"}
	started_at = now_datetime()
	active_kind = None
	active_region = None
	try:
		for config in configurations:
			_update_configuration(config.name, current_status="Running", last_sync_at=started_at, last_error="")
		if reset:
			for config in configurations:
				values = {"history_completed": 0, "history_checkpoint": None}
				if cint(master.summary_enabled):
					values["summary_checkpoint"] = None
				if cint(master.detail_enabled):
					values["detail_checkpoint"] = None
				_update_configuration(config.name, **values)
			master, configurations, stores, regions = _enabled_group(master.name)

		batch_id = f"ledger-master-{frappe.generate_hash(length=12)}"
		recheck_days = cint(recheck_days)
		routine = bool(routine)
		mode = "recheck" if recheck_days else ("routine" if routine else "history")
		group_summary = {
			"batch_id": batch_id,
			"mode": mode,
			"master_configuration": master.name,
			"configurations": [config.name for config in configurations],
			"regions": {},
		}
		if recheck_days:
			group_summary["recheck_days"] = recheck_days
		elif routine:
			group_summary["routine_lookback_days"] = max(cint(master.routine_lookback_days), 1)

		completed_ends = []
		for region_name, targets in sorted(regions.items()):
			active_region = region_name
			region_summary = {
				"marketplace_ids": [store.marketplace_id for _config, store in targets],
				"configurations": [config.name for config, _store in targets],
			}
			for kind, enabled_field in (("summary", "summary_enabled"), ("detail", "detail_enabled")):
				if not cint(master.get(enabled_field)):
					continue
				active_kind = kind
				if recheck_days:
					start, end = _recent_recheck_range(master, kind, recheck_days)
				elif routine:
					start, end = _routine_recheck_range(master, kind)
				else:
					start, end = _effective_range(master, kind)
				region_summary[f"{kind}_range"] = {"start": str(start), "end": str(end)}
				region_summary[kind] = _process_group_kind(
					master,
					targets,
					kind,
					start,
					end,
					batch_id,
					force_start=bool(recheck_days or routine),
				)
				completed_ends.append(end)
			group_summary["regions"][region_name] = region_summary

		finished_at = now_datetime()
		region_by_configuration = {
			config.name: region_name
			for region_name, targets in regions.items()
			for config, _store in targets
		}
		for config, store in zip(configurations, stores):
			region_name = region_by_configuration.get(config.name)
			region_summary = group_summary["regions"].get(region_name, {})
			country_summary = {
				"batch_id": batch_id,
				"mode": mode,
				"master_configuration": master.name,
				"api_region": region_name,
				"marketplace_id": store.marketplace_id,
			}
			if recheck_days:
				country_summary["recheck_days"] = recheck_days
			elif routine:
				country_summary["routine_lookback_days"] = max(cint(master.routine_lookback_days), 1)
			for kind in ("summary", "detail"):
				if f"{kind}_range" in region_summary:
					country_summary[f"{kind}_range"] = region_summary[f"{kind}_range"]
				kind_result = region_summary.get(kind)
				if isinstance(kind_result, dict):
					country_summary[kind] = kind_result.get(config.name, _empty_stats())
			country_summary["expired_deleted"] = _delete_expired_records(master, store)
			values = {
				"current_status": "Completed",
				"last_success_at": finished_at,
				"last_sync_result": json.dumps(country_summary, ensure_ascii=False, default=str),
				"last_error": "",
			}
			if recheck_days:
				values.update({
					f"recheck_{recheck_days}_last_at": finished_at,
					f"recheck_{recheck_days}_next_at": _next_recheck(
						finished_at, master.get(f"recheck_{recheck_days}_interval_days")
					),
				})
			elif routine:
				values["next_sync_at"] = finished_at + timedelta(
					hours=max(cint(master.sync_interval_hours), 1)
				)
			else:
				values.update({
					"history_completed": 1,
					"history_checkpoint": max(completed_ends),
					"next_sync_at": finished_at + timedelta(
						hours=max(cint(master.sync_interval_hours), 1)
					),
				})
			_update_configuration(config.name, **values)
		return {"status": "success", "summary": group_summary}
	except Exception as exc:
		message = str(exc)
		if _is_quota_exceeded(exc):
			retry_at = _quota_retry_at(exc)
			waiting_message = _(
				"Amazon FBA 报告生成频率已达到限制，总配置任务将在 {0} 自动续跑。"
			).format(retry_at)
			for config in configurations:
				values = {
					"current_status": "Waiting",
					"next_sync_at": retry_at,
					"last_error": "",
					"last_sync_result": waiting_message,
				}
				if recheck_days:
					values[f"recheck_{recheck_days}_next_at"] = retry_at
				if active_kind:
					values.update({f"{active_kind}_status": "Waiting", f"{active_kind}_last_error": ""})
				_update_configuration(config.name, **values)
			frappe.logger("amazon_fba_inventory_ledger", allow_site=True).warning(
				"FBA ledger master quota reached: master=%s region=%s retry_at=%s",
				master.name,
				active_region,
				retry_at,
			)
			return {"status": "waiting", "retry_at": retry_at, "message": waiting_message}
		for config in configurations:
			values = {
				"current_status": "Failed",
				"next_sync_at": now_datetime() + timedelta(minutes=30),
				"last_error": message[:2000],
				"last_sync_result": f"Failed: {message[:1800]}",
			}
			if active_kind:
				values.update({f"{active_kind}_status": "Failed", f"{active_kind}_last_error": message[:2000]})
			_update_configuration(config.name, **values)
		frappe.logger("amazon_fba_inventory_ledger", allow_site=True).exception(
			"FBA inventory ledger master sync failed: master=%s region=%s",
			master.name,
			active_region,
		)
		raise
	finally:
		try:
			lock.release()
		except Exception:
			pass


def execute_ledger_sync(configuration_name, reset=False, recheck_days=0, routine=False):
	"""兼容升级前已经进入队列的单国家任务。"""
	config = _configuration(configuration_name)
	if not config.master_configuration:
		frappe.throw(_("国家配置尚未关联库存分类账总配置。"))
	return execute_group_ledger_sync(
		config.master_configuration,
		reset=reset,
		recheck_days=recheck_days,
		routine=routine,
	)


def _run_scheduled_master(master_name, now):
	master, configurations, _stores, _regions = _enabled_group(master_name)
	active = False
	for config in configurations:
		if config.current_status != "Running" or not config.last_sync_at:
			continue
		if (now - get_datetime(config.last_sync_at)).total_seconds() <= TASK_TIMEOUT:
			active = True
		else:
			_update_configuration(
				config.name,
				current_status="Failed",
				last_error="上一次总配置任务超时，已由调度器释放。",
			)
	if active:
		return

	master, configurations, _stores, _regions = _enabled_group(master_name)
	all_history_completed = all(cint(config.history_completed) for config in configurations)
	if all_history_completed:
		initialized_any = False
		for config in configurations:
			initialized = {}
			for days in RECHECK_DAYS:
				if cint(master.get(f"enable_recheck_{days}")) and not config.get(f"recheck_{days}_next_at"):
					initialized[f"recheck_{days}_next_at"] = _first_recheck(now)
			if initialized:
				initialized_any = True
				_update_configuration(config.name, **initialized)
		if initialized_any:
			return
		for days in RECHECK_DAYS:
			if not cint(master.get(f"enable_recheck_{days}")):
				continue
			due = any(
				config.get(f"recheck_{days}_next_at")
				and get_datetime(config.get(f"recheck_{days}_next_at")) <= now
				for config in configurations
			)
			if due:
				_enqueue_group(master.name, recheck_days=days)
				return

	future_times = [get_datetime(config.next_sync_at) for config in configurations if config.next_sync_at]
	if len(future_times) == len(configurations) and min(future_times) > now:
		return
	for config in configurations:
		values = {"current_status": "Waiting"}
		if cint(master.summary_enabled):
			values["summary_status"] = "Waiting"
		if cint(master.detail_enabled):
			values["detail_status"] = "Waiting"
		_update_configuration(config.name, **values)
	_enqueue_group(master.name, routine=all_history_completed)


def run_scheduled_ledger_sync():
	now = now_datetime()
	master_names = frappe.get_all(MASTER_DOCTYPE, filters={"enabled": 1}, pluck="name", order_by="name asc")
	for master_name in master_names:
		try:
			_run_scheduled_master(master_name, now)
		except Exception:
			frappe.logger("amazon_fba_inventory_ledger", allow_site=True).exception(
				"FBA inventory ledger master scheduler failed: master=%s", master_name
			)
