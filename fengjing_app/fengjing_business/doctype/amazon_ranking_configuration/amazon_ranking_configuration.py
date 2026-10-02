"""Amazon 排名配置、商品发现与排名抓取调度。"""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, cint, get_datetime, now_datetime
from redis.exceptions import LockError

from fengjing_app.fengjing_business.doctype.amazon_ranking_product.amazon_ranking_product import (
	get_due_products,
	mark_missing_automatic_products,
	set_fetch_result,
	upsert_discovered_product,
)
from fengjing_app.fengjing_business.doctype.amazon_store_configuration.amazon_store_configuration import (
	amazon_api_request,
	ensure_database_connection,
	get_store,
)


class AmazonRankingConfiguration(Document):
	def validate(self):
		self.interval_minutes = max(cint(self.interval_minutes), 1)
		self.retention_days = cint(self.retention_days) or 1825
		self.max_retries = max(cint(self.max_retries), 0)
		self.task_timeout_minutes = max(cint(self.task_timeout_minutes), 5)
		self.discovery_interval_hours = max(cint(self.discovery_interval_hours), 1)
		if self.amazon_store:
			store = get_store(self.amazon_store, require_enabled=False)
			duplicate = frappe.db.get_value(
				self.doctype,
				{"amazon_store": store.name, "name": ["!=", self.name or ""]},
				"name",
			)
			if duplicate:
				frappe.throw(_("This Amazon store already has a ranking configuration: {0}").format(duplicate))


def _response_payload(response, action):
	if response is None:
		raise RuntimeError(f"Amazon {action} returned no response")
	if response.status_code != 200:
		try:
			body = json.dumps(response.json(), ensure_ascii=False)
		except Exception:
			body = response.text
		raise RuntimeError(f"Amazon {action} failed (HTTP {response.status_code}): {body[:1500]}")
	return response.json() or {}


def _runtime_lock(name):
	return frappe.cache().lock(
		frappe.cache().make_key(f"fengjing:amazon-ranking:{name}"),
		timeout=4 * 60 * 60,
		blocking_timeout=0,
	)


def _set_runtime(config_name, **values):
	frappe.db.set_value(
		"Amazon Ranking Configuration",
		config_name,
		values,
		update_modified=False,
	)


def discover_store_products(config, store):
	if not cint(config.auto_discover_store_products):
		return {"skipped": True, "count": 0}
	if not store.seller_id:
		raise ValueError(f"Amazon store {store.store_name or store.name} has no Seller ID")
	discovered_at = now_datetime()
	discovered_names = set()
	page_token = None
	seen_tokens = set()
	pages = 0
	while True:
		params = {
			"marketplaceIds": store.marketplace_id,
			"pageSize": 20,
			"includedData": "summaries,issues,offers,fulfillmentAvailability,procurement",
		}
		if page_token:
			params["pageToken"] = page_token
		response = amazon_api_request(
			store,
			"listings",
			"GET",
			f"/listings/2021-08-01/items/{store.seller_id}",
			params=params,
			timeout=90,
			max_retries=config.max_retries,
		)
		payload = _response_payload(response, "listing discovery")
		data = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
		for item in data.get("items") or []:
			name = upsert_discovered_product(config, store, item, discovered_at)
			if name:
				discovered_names.add(name)
		pages += 1
		pagination = data.get("pagination") or {}
		page_token = pagination.get("nextToken") or pagination.get("next_token")
		if not page_token:
			break
		if page_token in seen_tokens:
			raise RuntimeError("Listings Items API returned a repeated pagination token")
		seen_tokens.add(page_token)
	if cint(config.remove_inactive_products):
		mark_missing_automatic_products(config.name, discovered_names)
	next_at = add_to_date(discovered_at, hours=config.discovery_interval_hours)
	result = f"发现 {len(discovered_names)} 个自有 ASIN，共 {pages} 页"
	_set_runtime(
		config.name,
		last_discovery_at=discovered_at,
		next_discovery_at=next_at,
		last_discovery_result=result,
		last_discovery_error="",
	)
	return {"count": len(discovered_names), "pages": pages, "result": result}


def fetch_rank_snapshots(config, store, include_all=False):
	from fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log import save_rank_snapshot

	product_names = get_due_products(config, include_all=include_all)
	success = 0
	failed = []
	for product_name in product_names:
		target = frappe.get_doc("Amazon Ranking Product", product_name)
		try:
			included = "productTypes,salesRanks,summaries"
			if cint(config.capture_catalog_images):
				included += ",images"
			response = amazon_api_request(
				store,
				"catalog",
				"GET",
				f"/catalog/2022-04-01/items/{target.asin}",
				params={"marketplaceIds": store.marketplace_id, "includedData": included},
				timeout=90,
				max_retries=config.max_retries,
			)
			payload = _response_payload(response, f"catalog fetch {target.asin}")
			save_rank_snapshot(target, config, store, payload)
			set_fetch_result(target.name, True, config.interval_minutes)
			success += 1
		except Exception as exc:
			set_fetch_result(target.name, False, config.interval_minutes, exc)
			failed.append({"asin": target.asin, "error": str(exc)[:500]})
			frappe.logger("amazon_rank", allow_site=True).warning(
				"Amazon ranking fetch failed: %s\n%s", target.asin, frappe.get_traceback()
			)
		frappe.db.commit()
	finished_at = now_datetime()
	result = f"成功 {success}，失败 {len(failed)}，总计 {len(product_names)}"
	_set_runtime(
		config.name,
		last_fetch_at=finished_at,
		next_fetch_at=add_to_date(finished_at, minutes=config.interval_minutes),
		last_fetch_result=result,
	)
	return {"success": success, "failed": failed, "total": len(product_names), "result": result}


def execute_ranking_task(configuration_name, execution_type="full", include_all=False):
	"""后台任务入口；配置级锁保证发现和排名不会并发冲突。"""
	ensure_database_connection()
	try:
		with _runtime_lock(configuration_name):
			config = frappe.get_doc("Amazon Ranking Configuration", configuration_name)
			if not cint(config.enabled):
				_set_runtime(config.name, current_task_status="Paused", last_run_result="配置未启用")
				return
			store = get_store(config.amazon_store)
			started_at = now_datetime()
			_set_runtime(
				config.name,
				current_task_status="Running",
				current_execution_type=execution_type,
				current_task_started_at=started_at,
				current_task_completed_at=None,
				last_error="",
			)
			discovery = None
			discovery_error = None
			if execution_type in {"discovery", "full"}:
				try:
					discovery = discover_store_products(config, store)
				except Exception as exc:
					discovery_error = str(exc)
					_set_runtime(config.name, last_discovery_error=discovery_error[:2000])
					frappe.logger("amazon_rank", allow_site=True).warning(
						"Amazon product discovery failed: %s\n%s", config.name, frappe.get_traceback()
					)
			ranking = None
			if execution_type in {"ranking", "full"}:
				ranking = fetch_rank_snapshots(config, store, include_all=include_all)
			messages = []
			if discovery:
				messages.append(discovery.get("result", ""))
			if discovery_error:
				messages.append(f"商品发现失败：{discovery_error}")
			if ranking:
				messages.append(ranking.get("result", ""))
			failed = bool(discovery_error or (ranking and ranking.get("failed")))
			ranking_error = ""
			if ranking and ranking.get("failed"):
				ranking_error = json.dumps(ranking["failed"][:10], ensure_ascii=False)
			_set_runtime(
				config.name,
				current_task_status="Failed" if failed else "Success",
				current_task_completed_at=now_datetime(),
				last_run_result="；".join(filter(None, messages)),
				last_error=(discovery_error or ranking_error)[:2000],
			)
			frappe.db.commit()
	except LockError:
		# 同一配置已在运行，不再启动重复任务。
		return
	except Exception as exc:
		_set_runtime(
			configuration_name,
			current_task_status="Failed",
			current_task_completed_at=now_datetime(),
			last_error=str(exc)[:2000],
			last_run_result="任务异常终止",
		)
		frappe.db.commit()
		raise


def _enqueue(configuration_name, execution_type, include_all=False):
	config = frappe.get_doc("Amazon Ranking Configuration", configuration_name)
	timeout = max(cint(config.task_timeout_minutes), 5) * 60
	_set_runtime(
		config.name,
		current_task_status="Waiting",
		current_execution_type=execution_type,
		current_task_started_at=now_datetime(),
		last_error="",
	)
	frappe.enqueue(
		"fengjing_app.fengjing_business.doctype.amazon_ranking_configuration.amazon_ranking_configuration.execute_ranking_task",
		queue="long",
		timeout=timeout,
		enqueue_after_commit=True,
		job_id=f"amazon-ranking-{execution_type}-{config.name}",
		deduplicate=True,
		configuration_name=config.name,
		execution_type=execution_type,
		include_all=include_all,
	)
	return {"status": "queued", "configuration": config.name, "execution_type": execution_type}


@frappe.whitelist()
def start_product_discovery(name):
	frappe.get_doc("Amazon Ranking Configuration", name).check_permission("write")
	return _enqueue(name, "discovery")


@frappe.whitelist()
def start_ranking_fetch(name):
	frappe.get_doc("Amazon Ranking Configuration", name).check_permission("write")
	return _enqueue(name, "ranking", include_all=True)


@frappe.whitelist()
def start_full_ranking_sync(name):
	frappe.get_doc("Amazon Ranking Configuration", name).check_permission("write")
	return _enqueue(name, "full", include_all=True)


def run_scheduled_ranking_sync():
	ensure_database_connection()
	now_value = now_datetime()
	for row in frappe.get_all(
		"Amazon Ranking Configuration",
		filters={"enabled": 1},
		fields=[
			"name", "auto_discover_store_products", "next_discovery_at", "next_fetch_at",
			"current_task_status", "current_task_started_at", "task_timeout_minutes",
		],
		limit_page_length=0,
	):
		if row.current_task_status in {"Waiting", "Running"}:
			started = get_datetime(row.current_task_started_at) if row.current_task_started_at else None
			if not started or started > add_to_date(now_value, minutes=-max(cint(row.task_timeout_minutes), 5)):
				continue
			_set_runtime(row.name, current_task_status="Failed", last_error="上次任务超时，已自动解锁")
		discovery_due = cint(row.auto_discover_store_products) and (
			not row.next_discovery_at or get_datetime(row.next_discovery_at) <= now_value
		)
		ranking_due = not row.next_fetch_at or get_datetime(row.next_fetch_at) <= now_value
		if discovery_due:
			_enqueue(row.name, "full", include_all=ranking_due)
		elif ranking_due:
			_enqueue(row.name, "ranking")
