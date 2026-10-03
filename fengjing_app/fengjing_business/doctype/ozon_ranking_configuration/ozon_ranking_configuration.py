# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""Ozon ranking synchronization driven by the independent configuration."""

import hashlib
import json
import re
from datetime import timedelta

import frappe
from frappe.model.document import Document
from frappe.utils import cint, get_datetime, getdate, now_datetime, today

from fengjing_app.fengjing_business.doctype.ozon_store_configuration.ozon_store_configuration import (
	ensure_database_connection,
	get_store,
	ozon_seller_request,
	response_error_summary,
)


DOCTYPE = "Ozon Ranking Configuration"
STORAGE_DOCTYPE = "Ozon ranking storage"
MAPPING_DOCTYPE = "Fengjing - Product Corresponding Platform - Main Table"
TASK_TIMEOUT = 6 * 60 * 60
BATCH_SIZE = 1000


class OzonRankingConfiguration(Document):
	def validate(self):
		if self.ozon_store:
			get_store(self.ozon_store, require_enabled=False)
		if cint(self.enabled) and not (cint(self.fetch_product_summary) or cint(self.fetch_keyword_details)):
			frappe.throw("Please enable product ranking summary or search keyword details")
		if self.product_scope == "Specified Ozon SKUs" and not _parse_skus(self.specified_ozon_skus):
			frappe.throw("Please enter at least one Ozon SKU for the selected product scope")
		self.max_keywords_per_product = min(max(cint(self.max_keywords_per_product), 1), 15)
		self.retention_days = max(cint(self.retention_days), 1)
		self.sync_interval_hours = max(cint(self.sync_interval_hours), 1)
		self.data_delay_days = max(cint(self.data_delay_days), 0)
		self.lookback_days = max(cint(self.lookback_days), 1)
		if self.history_start_date and self.history_end_date:
			if getdate(self.history_start_date) > getdate(self.history_end_date):
				frappe.throw("History start date cannot be later than history end date")


def _get_configuration(name):
	ensure_database_connection()
	return frappe.get_doc(DOCTYPE, name)


def _update_configuration(name, **values):
	ensure_database_connection()
	valid = set(frappe.get_meta(DOCTYPE).get_valid_columns())
	values = {key: value for key, value in values.items() if key in valid}
	if values:
		frappe.db.set_value(DOCTYPE, name, values, update_modified=False)
		frappe.db.commit()


def _request_json(store, path, body, api_name):
	response = ozon_seller_request(store, "POST", path, json_data=body, timeout=90)
	ensure_database_connection()
	if response is None or response.status_code != 200:
		status = response.status_code if response is not None else "no response"
		raise RuntimeError(f"Ozon {api_name} failed (HTTP {status}): {response_error_summary(response, 1800)}")
	try:
		payload = response.json()
	except ValueError as exc:
		raise RuntimeError(f"Ozon {api_name} returned invalid JSON") from exc
	if not isinstance(payload, dict):
		raise RuntimeError(f"Ozon {api_name} returned an unexpected structure")
	return payload


def _chunks(values, size=BATCH_SIZE):
	for start in range(0, len(values), size):
		yield values[start : start + size]


def _parse_skus(value):
	return {
		part.strip()
		for part in re.split(r"[\s,，;；]+", str(value or ""))
		if part.strip()
	}


def _stock_skus(item):
	return [
		str(stock.get("sku") or "").strip()
		for stock in (item.get("stocks") or [])
		if str(stock.get("sku") or "").strip() not in {"", "0"}
	]


def _read_store_products(store):
	products = {}
	cursor = ""
	seen_cursors = set()
	while True:
		body = {"filter": {"visibility": "ALL"}, "limit": BATCH_SIZE}
		if cursor:
			body["cursor"] = cursor
		payload = _request_json(store, "/v4/product/info/stocks", body, "product stock API")
		items = payload.get("items") or []
		for item in items:
			for sku in _stock_skus(item):
				products.setdefault(
					sku,
					{
						"sku": sku,
						"offer_id": str(item.get("offer_id") or "").strip(),
						"product_id": str(item.get("product_id") or "").strip(),
					},
				)
		next_cursor = str(payload.get("cursor") or "").strip()
		if len(items) < BATCH_SIZE or not next_cursor:
			break
		if next_cursor == cursor or next_cursor in seen_cursors:
			raise RuntimeError("Ozon product stock pagination cursor did not advance")
		seen_cursors.add(next_cursor)
		cursor = next_cursor

	if not products:
		for row in frappe.get_all(
			"Ozon order storage",
			filters={"store": store.cost_center},
			fields=["sku", "offer_id", "product_id"],
			limit_page_length=0,
		):
			sku = str(row.get("sku") or "").strip()
			if sku:
				products.setdefault(sku, row)
	return products


def _products_for_configuration(config, store):
	products = _read_store_products(store)
	if config.product_scope == "Specified Ozon SKUs":
		specified = _parse_skus(config.specified_ozon_skus)
		if not specified:
			raise ValueError("Specified Ozon SKU list is empty")
		for sku in specified:
			products.setdefault(sku, {"sku": sku})
		products = {sku: products[sku] for sku in specified}
	elif config.product_scope == "Only Bound Items":
		mapping_rows = frappe.get_all(
			MAPPING_DOCTYPE,
			filters={"启用": 1, "店铺": store.cost_center},
			fields=["平台sku", "平台asin"],
			limit_page_length=0,
		)
		allowed = {
			str(value).strip()
			for row in mapping_rows
			for value in (row.get("平台sku"), row.get("平台asin"))
			if value not in (None, "")
		}
		products = {sku: info for sku, info in products.items() if sku in allowed}
	if not products:
		raise ValueError("No Ozon SKUs match the configured product scope")
	return dict(sorted(products.items()))


def _storage_context(store):
	return frappe._dict({"店铺选项": store.cost_center, "ozon_id": store.ozon_id})


def _date_range(start_date, end_date):
	return (
		f"{start_date.isoformat()}T00:00:00Z",
		f"{end_date.isoformat()}T23:59:59Z",
	)


def _paged_request(store, path, base_body, result_field, api_name, page_size=1000):
	page = 0
	while True:
		payload = _request_json(
			store,
			path,
			{**base_body, "page": page, "page_size": page_size},
			api_name,
		)
		rows = payload.get(result_field) or []
		if not isinstance(rows, list):
			raise RuntimeError(f"Ozon {api_name} field {result_field} is not a list")
		yield payload, rows
		page_count = max(cint(payload.get("page_count")), 1)
		if page + 1 >= page_count or not rows:
			break
		page += 1


def _sync_period(config, store, sync_label, start_date, end_date, products):
	from fengjing_app.fengjing_business.doctype.ozon_ranking_storage.ozon_ranking_storage import (
		_保存排名记录,
	)

	date_from, date_to = _date_range(start_date, end_date)
	context = _storage_context(store)
	summary = {
		"created": 0,
		"updated": 0,
		"archived": 0,
		"product_summary_rows": 0,
		"keyword_rows": 0,
		"pages": 0,
	}
	product_summaries = {}
	for sku_batch in _chunks(list(products)):
		base_body = {
			"date_from": date_from,
			"date_to": date_to,
			"skus": sku_batch,
			"sort_by": "BY_SEARCHES",
			"sort_dir": "DESCENDING",
		}
		if cint(config.fetch_product_summary):
			for payload, rows in _paged_request(
				store,
				"/v1/analytics/product-queries",
				base_body,
				"items",
				"product ranking summary API",
			):
				analytics_period = payload.get("analytics_period") or {}
				for row in rows:
					sku = str(row.get("sku") or "").strip()
					product_info = {**products.get(sku, {}), **row}
					product_summaries[sku] = product_info
					result = _保存排名记录(
						row,
						"商品汇总",
						context,
						sync_label,
						start_date,
						end_date,
						product_info,
						analytics_period,
					)
					for key in ("created", "updated", "archived"):
						summary[key] += cint(result.get(key))
					summary["product_summary_rows"] += 1
				frappe.db.commit()
				summary["pages"] += 1

		if cint(config.fetch_keyword_details):
			detail_body = {
				**base_body,
				"limit_by_sku": min(max(cint(config.max_keywords_per_product), 1), 15),
			}
			for payload, rows in _paged_request(
				store,
				"/v1/analytics/product-queries/details",
				detail_body,
				"queries",
				"keyword ranking details API",
				page_size=100,
			):
				analytics_period = payload.get("analytics_period") or {}
				for row in rows:
					sku = str(row.get("sku") or "").strip()
					product_info = product_summaries.get(sku) or products.get(sku) or {}
					result = _保存排名记录(
						row,
						"关键词明细",
						context,
						sync_label,
						start_date,
						end_date,
						product_info,
						analytics_period,
					)
					for key in ("created", "updated", "archived"):
						summary[key] += cint(result.get(key))
					summary["keyword_rows"] += 1
				frappe.db.commit()
				summary["pages"] += 1
	return summary


def _merge_summary(target, source):
	for key, value in source.items():
		target[key] = cint(target.get(key)) + cint(value)


def _available_end_date(config):
	return getdate(today()) - timedelta(days=max(cint(config.data_delay_days), 0))


def _history_plan(config):
	if not config.history_start_date or not config.history_end_date:
		raise ValueError("History start and end dates are required")
	configured_start = getdate(config.history_start_date)
	configured_end = getdate(config.history_end_date)
	if configured_start > configured_end:
		raise ValueError("History start date cannot be later than history end date")
	cursor = getdate(config.history_checkpoint) + timedelta(days=1) if config.history_checkpoint else configured_start
	available_end = min(configured_end, _available_end_date(config))
	if cursor > available_end:
		return {
			"status": "complete" if cursor > configured_end else "waiting",
			"configured_start": configured_start,
			"configured_end": configured_end,
			"available_end": available_end,
		}
	recent_boundary = getdate(today()) - timedelta(days=30)
	period_days = 7 if cursor < recent_boundary else 1
	return {
		"status": "ready",
		"start": cursor,
		"end": min(cursor + timedelta(days=period_days - 1), available_end),
		"configured_start": configured_start,
		"configured_end": configured_end,
		"available_end": available_end,
	}


def _history_progress(config, completed_date):
	start = getdate(config.history_start_date)
	end = getdate(config.history_end_date)
	total_days = max((end - start).days + 1, 1)
	completed_days = max(min((getdate(completed_date) - start).days + 1, total_days), 0)
	return round(completed_days / total_days * 100, 2)


def _cleanup_expired(config, store):
	cutoff = getdate(today()) - timedelta(days=max(cint(config.retention_days), 1))
	frappe.db.delete(STORAGE_DOCTYPE, {"store": store.cost_center, "statistics_date": ("<", cutoff)})
	frappe.db.commit()


def _task_lock(name):
	return frappe.cache().lock(
		frappe.cache().make_key(f"fengjing:ozon-ranking-config:{name}"),
		timeout=TASK_TIMEOUT,
		blocking_timeout=0,
	)


def _enqueue(name, sync_type):
	digest = hashlib.sha256(f"{name}|{sync_type}".encode("utf-8")).hexdigest()[:20]
	frappe.enqueue(
		execute_ranking_sync,
		queue="long",
		timeout=TASK_TIMEOUT,
		enqueue_after_commit=True,
		job_id=f"ozon-ranking-config-{digest}",
		deduplicate=True,
		configuration_name=name,
		sync_type=sync_type,
	)


@frappe.whitelist()
def start_history_sync(name):
	config = _get_configuration(name)
	if not cint(config.enabled):
		frappe.throw("Please enable ranking sync first")
	if not config.history_start_date or not config.history_end_date:
		frappe.throw("Please set the history start and end dates first")
	get_store(config.ozon_store)
	values = {
		"history_status": "Waiting",
		"current_task_status": "Waiting",
		"current_execution_type": "history",
		"history_last_error": "",
		"last_error": "",
	}
	if config.history_status == "Completed":
		values.update(
			{
				"history_checkpoint": None,
				"history_progress": 0,
				"history_inserted_count": 0,
				"history_updated_count": 0,
				"history_archived_count": 0,
				"history_summary": "",
			}
		)
	_update_configuration(name, **values)
	_enqueue(name, "history")
	return {"status": "queued", "message": "Ozon ranking history sync has been queued"}


@frappe.whitelist()
def start_latest_sync(name):
	config = _get_configuration(name)
	if not cint(config.enabled):
		frappe.throw("Please enable ranking sync first")
	get_store(config.ozon_store)
	_update_configuration(
		name,
		current_task_status="Waiting",
		current_execution_type="latest",
		last_error="",
	)
	_enqueue(name, "latest")
	return {"status": "queued", "message": "Ozon latest ranking sync has been queued"}


def execute_ranking_sync(configuration_name, sync_type):
	if sync_type not in {"history", "latest"}:
		raise ValueError(f"Unsupported Ozon ranking sync type: {sync_type}")
	lock = _task_lock(configuration_name)
	if not lock.acquire(blocking=False):
		return {"status": "busy", "message": "This Ozon ranking configuration is already running"}

	summary = {
		"created": 0,
		"updated": 0,
		"archived": 0,
		"product_summary_rows": 0,
		"keyword_rows": 0,
		"pages": 0,
		"periods": 0,
		"products": 0,
	}
	try:
		_update_configuration(
			configuration_name,
			current_task_status="Running",
			current_execution_type=sync_type,
			current_task_started_at=now_datetime(),
			last_error="",
			**({"history_status": "Running", "history_last_error": ""} if sync_type == "history" else {}),
		)
		config = _get_configuration(configuration_name)
		if not cint(config.enabled):
			raise ValueError("Ozon ranking synchronization is disabled")
		store = get_store(config.ozon_store)
		products = _products_for_configuration(config, store)
		summary["products"] = len(products)

		if sync_type == "history":
			summary["created"] = cint(config.history_inserted_count)
			summary["updated"] = cint(config.history_updated_count)
			summary["archived"] = cint(config.get("history_archived_count"))
			while True:
				config = _get_configuration(configuration_name)
				plan = _history_plan(config)
				if plan["status"] != "ready":
					completed = plan["status"] == "complete"
					text = json.dumps(summary, ensure_ascii=False)
					_cleanup_expired(config, store)
					_update_configuration(
						configuration_name,
						history_status="Completed" if completed else "Waiting",
						history_progress=100 if completed else config.history_progress,
						history_summary=text,
						current_task_status="Success" if completed else "Waiting",
						current_execution_type="",
						current_task_completed_at=now_datetime(),
						last_run_result=text,
					)
					return {"status": plan["status"], "summary": summary}
				period = _sync_period(
					config, store, "历史排名", plan["start"], plan["end"], products
				)
				_merge_summary(summary, period)
				summary["periods"] += 1
				_update_configuration(
					configuration_name,
					premium_analytics_available=1,
					history_checkpoint=plan["end"],
					history_progress=_history_progress(config, plan["end"]),
					history_inserted_count=summary["created"],
					history_updated_count=summary["updated"],
					history_archived_count=summary["archived"],
					history_summary=json.dumps(summary, ensure_ascii=False),
				)

		config = _get_configuration(configuration_name)
		end_date = _available_end_date(config)
		start_date = end_date - timedelta(days=max(cint(config.lookback_days), 1) - 1)
		cursor = start_date
		while cursor <= end_date:
			period = _sync_period(config, store, "日常同步", cursor, cursor, products)
			_merge_summary(summary, period)
			summary["periods"] += 1
			cursor += timedelta(days=1)

		finished_at = now_datetime()
		_cleanup_expired(config, store)
		_update_configuration(
			configuration_name,
			premium_analytics_available=1,
			current_task_status="Success",
			current_execution_type="",
			current_task_completed_at=finished_at,
			last_sync_at=finished_at,
			next_sync_at=finished_at + timedelta(hours=max(cint(config.sync_interval_hours), 1)),
			last_sync_result=json.dumps({"status": "Success", **summary}, ensure_ascii=False),
			last_run_result=json.dumps({"status": "Success", **summary}, ensure_ascii=False),
			last_error="",
		)
		return {"status": "success", "summary": summary}
	except Exception as exc:
		now = now_datetime()
		values = {
			"current_task_status": "Failed",
			"current_execution_type": "",
			"current_task_completed_at": now,
			"last_error": str(exc)[:2000],
			"last_run_result": f"Failed: {str(exc)[:1800]}",
		}
		if sync_type == "history":
			values.update({"history_status": "Failed", "history_last_error": str(exc)[:2000]})
		else:
			values.update(
				{
					"last_sync_at": now,
					"next_sync_at": now + timedelta(minutes=30),
					"last_sync_result": f"Failed: {str(exc)[:1800]}",
				}
			)
		_update_configuration(configuration_name, **values)
		frappe.logger("ozon_ranking_v2", allow_site=True).exception(
			"Ozon ranking sync failed: configuration=%s type=%s", configuration_name, sync_type
		)
		raise
	finally:
		try:
			lock.release()
		except Exception:
			pass


def run_scheduled_ranking_sync():
	"""Queue due jobs after this function is explicitly connected to hooks later."""
	now = now_datetime()
	for name in frappe.get_all(DOCTYPE, filters={"enabled": 1}, pluck="name"):
		try:
			config = _get_configuration(name)
			get_store(config.ozon_store)
			if config.current_task_status == "Running":
				started = get_datetime(config.current_task_started_at) if config.current_task_started_at else None
				if started and (now - started).total_seconds() > TASK_TIMEOUT:
					_update_configuration(
						name,
						current_task_status="Failed",
						current_execution_type="",
						current_task_completed_at=now,
						last_error="Previous task exceeded six hours and was released by the scheduler",
					)
					config = _get_configuration(name)
				else:
					continue
			if (
				config.history_status in {"Waiting", "Running"}
				and config.history_start_date
				and config.history_end_date
			):
				plan = _history_plan(config)
				if plan["status"] in {"ready", "complete"}:
					_enqueue(name, "history")
					continue
			if not config.next_sync_at or get_datetime(config.next_sync_at) <= now:
				_enqueue(name, "latest")
		except Exception:
			frappe.logger("ozon_ranking_v2", allow_site=True).exception(
				"Ozon ranking scheduler failed: configuration=%s", name
			)


启动Ozon历史排名同步 = start_history_sync
启动Ozon最新排名同步 = start_latest_sync
定时执行Ozon排名同步 = run_scheduled_ranking_sync

