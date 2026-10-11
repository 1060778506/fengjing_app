# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""Amazon广告结构、历史日报和日常回看同步。"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, getdate, now_datetime, today

from fengjing_app.fengjing_business.doctype.amazon_store_configuration.amazon_store_configuration import (
	ensure_database_connection,
	get_store,
)

from .amazon_advertising_api import (
	AmazonAdvertisingAPIError,
	fetch_v3_report,
	iter_unified_entities,
	list_profiles,
)
from .amazon_advertising_storage import (
	enrich_structure_context,
	save_report_rows,
	save_structure_entities,
)


SITE_DOCTYPE = "Amazon Advertising Site Configuration"
MASTER_DOCTYPE = "Amazon Advertising Master Configuration"
BUSY_STATUSES = {"排队中", "运行中"}


def _definition(key, ad_product, report_type_id, group_by, columns, retention_days, flag):
	return {
		"key": key,
		"ad_product": ad_product,
		"report_type_id": report_type_id,
		"group_by": group_by,
		"columns": columns,
		"retention_days": retention_days,
		"flag": flag,
	}


COMMON_METRICS = ["impressions", "clicks", "cost", "purchases7d", "unitsSoldClicks7d", "sales7d"]
REPORT_DEFINITIONS = [
	_definition(
		"campaign", "SPONSORED_PRODUCTS", "spCampaigns", ["campaign"],
		["date", "campaignId", "campaignName", "campaignStatus", "campaignBudgetAmount", "campaignBudgetType", "campaignBudgetCurrencyCode", *COMMON_METRICS],
		95, "sync_campaign_daily",
	),
	_definition(
		"product", "SPONSORED_PRODUCTS", "spAdvertisedProduct", ["advertiser"],
		["date", "campaignId", "campaignName", "campaignStatus", "adGroupId", "adGroupName", "adId", "advertisedSku", "advertisedAsin", *COMMON_METRICS],
		95, "sync_product_daily",
	),
	_definition(
		"targeting", "SPONSORED_PRODUCTS", "spTargeting", ["targeting"],
		["date", "campaignId", "campaignName", "adGroupId", "adGroupName", "targetingId", "keywordId", "keyword", "keywordType", "matchType", "targeting", *COMMON_METRICS],
		95, "sync_targeting_daily",
	),
	_definition(
		"search_term", "SPONSORED_PRODUCTS", "spSearchTerm", ["searchTerm"],
		["date", "campaignId", "campaignName", "adGroupId", "adGroupName", "keywordId", "keyword", "searchTerm", "matchType", *COMMON_METRICS],
		65, "sync_targeting_daily",
	),
	_definition(
		"placement", "SPONSORED_PRODUCTS", "spCampaigns", ["campaignPlacement"],
		["date", "campaignId", "campaignName", "campaignStatus", "placementClassification", "campaignBudgetAmount", "campaignBudgetType", *COMMON_METRICS],
		95, "sync_placement_daily",
	),
	_definition(
		"purchased_product", "SPONSORED_PRODUCTS", "spPurchasedProduct", ["asin"],
		["date", "campaignId", "campaignName", "adGroupId", "adGroupName", "advertisedSku", "advertisedAsin", "purchasedAsin", "purchases7d", "unitsSoldClicks7d", "sales7d"],
		95, "sync_purchased_product_daily",
	),
	_definition(
		"campaign", "SPONSORED_BRANDS", "sbCampaigns", ["campaign"],
		["date", "campaignId", "campaignName", "campaignStatus", "campaignBudget", "campaignBudgetType", "campaignBudgetCurrencyCode", *COMMON_METRICS],
		60, "sync_campaign_daily",
	),
	_definition(
		"targeting", "SPONSORED_BRANDS", "sbTargeting", ["targeting"],
		["date", "campaignId", "campaignName", "adGroupId", "adGroupName", "targetingId", "keywordId", "keyword", "matchType", "targeting", *COMMON_METRICS],
		60, "sync_targeting_daily",
	),
	_definition(
		"search_term", "SPONSORED_BRANDS", "sbSearchTerm", ["searchTerm"],
		["date", "campaignId", "campaignName", "adGroupId", "adGroupName", "keywordId", "keyword", "searchTerm", "matchType", *COMMON_METRICS],
		60, "sync_targeting_daily",
	),
	_definition(
		"placement", "SPONSORED_BRANDS", "sbCampaigns", ["campaignPlacement"],
		["date", "campaignId", "campaignName", "campaignStatus", "placementClassification", *COMMON_METRICS],
		60, "sync_placement_daily",
	),
	_definition(
		"purchased_product", "SPONSORED_BRANDS", "sbPurchasedProduct", ["purchasedAsin"],
		["date", "campaignBudgetCurrencyCode", "campaignName", "adGroupName", "attributionType", "purchasedAsin", "productName", "productCategory", "sales14d", "orders14d", "unitsSold14d", "newToBrandSales14d", "newToBrandPurchases14d", "newToBrandUnitsSold14d"],
		731, "sync_purchased_product_daily",
	),
	_definition(
		"campaign", "SPONSORED_DISPLAY", "sdCampaigns", ["campaign"],
		["date", "campaignId", "campaignName", "campaignStatus", "campaignBudget", "campaignBudgetType", "campaignBudgetCurrencyCode", *COMMON_METRICS],
		65, "sync_campaign_daily",
	),
	_definition(
		"product", "SPONSORED_DISPLAY", "sdAdvertisedProduct", ["advertisedProduct"],
		["date", "campaignId", "campaignName", "adGroupId", "adGroupName", "adId", "advertisedSku", "advertisedAsin", *COMMON_METRICS],
		65, "sync_product_daily",
	),
	_definition(
		"targeting", "SPONSORED_DISPLAY", "sdTargeting", ["targeting"],
		["date", "campaignId", "campaignName", "adGroupId", "adGroupName", "targetingId", "targeting", *COMMON_METRICS],
		65, "sync_targeting_daily",
	),
]


def load_context(site_name, *, require_enabled=True):
	site = frappe.get_doc(SITE_DOCTYPE, site_name)
	master = frappe.get_doc(MASTER_DOCTYPE, site.master_configuration)
	if require_enabled and (not cint(site.enabled) or not cint(master.enabled)):
		raise ValueError("广告站点配置或广告总配置未启用。")
	store = get_store(site.amazon_store, require_enabled=require_enabled)
	store._advertising_master = master
	site.master_api_region = master.api_region
	return site, master, store


def test_site_access(site_name):
	site, master, store = load_context(site_name, require_enabled=False)
	profiles = list_profiles(store, master.api_region)
	matched = [row for row in profiles if str(row.get("profileId") or "") == str(site.profile_id or "")]
	if not matched:
		raise ValueError(f"当前授权返回{len(profiles)}个广告Profile，但没有找到Profile ID {site.profile_id}。")
	return matched[0]


def start_site_sync(site_name, mode="latest", days=None):
	site, master, _store = load_context(site_name)
	if site.sync_status in BUSY_STATUSES:
		return {"message": f"{site.configuration_name}的广告任务已经在{site.sync_status}。"}
	mode = str(mode or "latest")
	if mode not in {"structure", "latest", "history", "recheck"}:
		raise ValueError(f"不支持的广告同步方式：{mode}")
	job_key = hashlib.sha1(f"{site.name}:{mode}:{days or ''}".encode()).hexdigest()[:16]
	frappe.db.set_value(
		SITE_DOCTYPE,
		site.name,
		{"sync_status": "排队中", "current_job": mode, "last_error": None},
		update_modified=False,
	)
	frappe.enqueue(
		"fengjing_app.fengjing_business.doctype.amazon_advertising_master_configuration.amazon_advertising_sync.run_site_sync",
		queue="long",
		timeout=7200,
		enqueue_after_commit=True,
		job_id=f"amazon-ads-{job_key}",
		site_name=site.name,
		mode=mode,
		days=cint(days) if days else None,
	)
	return {"message": f"{site.configuration_name}广告任务已加入后台队列。"}


def run_site_sync(site_name, mode="latest", days=None):
	ensure_database_connection()
	site, master, store = load_context(site_name)
	batch_id = hashlib.sha256(f"{site.name}|{mode}|{now_datetime()}".encode()).hexdigest()[:24]
	started = now_datetime()
	_update_site(site.name, sync_status="运行中", current_job=mode, started_at=started, last_error=None)
	_update_master(master.name, sync_status="运行中", current_job=f"{site.name}:{mode}", started_at=started, last_error=None)
	try:
		profile = test_site_access(site.name)
		_profile_backfill(site, profile)
		result = {"batch_id": batch_id, "mode": mode, "structure": {}, "reports": [], "errors": [], "skipped": []}
		if mode in {"structure", "latest", "history"} and cint(master.sync_structure):
			result["structure"] = sync_structure(site, store, batch_id)
			result["errors"].extend(result["structure"].get("errors") or [])
			_update_site(site.name, last_structure_sync_at=now_datetime())
		if mode != "structure":
			start_date, end_date = _requested_range(master, mode, days)
			report_result = sync_reports(site, master, store, batch_id, mode, start_date, end_date)
			result["reports"] = report_result["reports"]
			result["errors"].extend(report_result["errors"])
			result["skipped"].extend(report_result["skipped"])
			_update_site(site.name, last_report_date=end_date)
		status = "部分成功" if result.get("errors") else "成功"
		summary = _summary(result)
		completed = now_datetime()
		next_sync = add_to_date(completed, hours=max(float(master.sync_interval_hours or 24), 1), as_datetime=True)
		_update_site(
			site.name,
			sync_status=status,
			current_job=None,
			completed_at=completed,
			last_success_at=completed,
			next_sync_at=next_sync,
			last_sync_result=summary,
			last_error="\n".join(result.get("errors") or [])[:2000] or None,
		)
		_update_master(
			master.name,
			sync_status=status,
			current_job=None,
			completed_at=completed,
			last_success_at=completed,
			next_sync_at=next_sync,
			last_sync_result=summary,
			last_error="\n".join(result.get("errors") or [])[:2000] or None,
		)
		frappe.db.commit()
		return result
	except Exception as exc:
		frappe.db.rollback()
		message = frappe.get_traceback(with_context=False)[-3500:]
		_update_site(site.name, sync_status="失败", current_job=None, completed_at=now_datetime(), last_error=f"{exc}\n{message}"[:4000])
		_update_master(master.name, sync_status="失败", current_job=None, completed_at=now_datetime(), last_error=str(exc)[:2000])
		frappe.db.commit()
		raise


def sync_structure(site, store, batch_id):
	total = {"created": 0, "updated": 0, "unchanged": 0, "errors": []}
	products = _enabled_ad_products(site)
	for ad_product in products:
		for resource in ("campaigns", "adGroups", "ads", "targets"):
			try:
				for page in iter_unified_entities(store, site, resource, ad_product=ad_product):
					rows = [_flatten_entity(resource, row) for row in page]
					stats = save_structure_entities(site, rows, batch_id, ad_product=ad_product)
					for key in ("created", "updated", "unchanged"):
						total[key] += stats.get(key, 0)
					frappe.db.commit()
			except Exception as exc:
				# 某个广告产品可能未开通；其他结构仍继续保存。
				total["errors"].append(f"{ad_product}/{resource}: {exc}")
	return total


def sync_reports(site, master, store, batch_id, mode, start_date, end_date):
	result = {"reports": [], "errors": [], "skipped": []}
	segment_days = min(max(cint(master.history_segment_days), 1), 31)
	sync_type = {"history": "历史同步", "recheck": "近期核对", "latest": "日常同步"}.get(mode, "手动同步")
	products = set(_enabled_ad_products(site))
	for definition in REPORT_DEFINITIONS:
		if definition["ad_product"] not in products or not cint(master.get(definition["flag"])):
			continue
		available_start = max(start_date, getdate(today()) - timedelta(days=definition["retention_days"] - 1))
		if end_date < available_start:
			result["skipped"].append(
				f"{definition['ad_product']}/{definition['key']}：请求范围早于API可用的{definition['retention_days']}天。"
			)
			continue
		for segment_start, segment_end in _segments(available_start, end_date, segment_days):
			try:
				report_id, rows = fetch_v3_report(store, site, definition, segment_start, segment_end)
				_update_site(site.name, last_report_request_id=report_id)
				stats = save_report_rows(
					site,
					definition["key"],
					rows,
					batch_id,
					sync_type,
					ad_product=definition["ad_product"],
				)
				result["reports"].append({
					"report": f"{definition['ad_product']}/{definition['key']}",
					"start": str(segment_start),
					"end": str(segment_end),
					"rows": len(rows),
					**stats,
				})
				frappe.db.commit()
			except AmazonAdvertisingAPIError as exc:
				result["errors"].append(
					f"{definition['ad_product']}/{definition['key']} {segment_start}至{segment_end}：{exc}"
				)
				frappe.db.rollback()
				break
			except Exception as exc:
				result["errors"].append(
					f"{definition['ad_product']}/{definition['key']} {segment_start}至{segment_end}：{exc}"
				)
				frappe.db.rollback()
				break
	return result


def discover_profiles(master_name, *, create_site_configurations=True):
	master = frappe.get_doc(MASTER_DOCTYPE, master_name)
	store = get_store(master.credential_store)
	store._advertising_master = master
	profiles = list_profiles(store, master.api_region)
	created = []
	updated = []
	for profile in profiles:
		account = profile.get("accountInfo") or {}
		marketplace_id = str(account.get("marketplaceStringId") or profile.get("marketplaceId") or "").strip()
		amazon_store = frappe.db.get_value("Amazon Store Configuration", {"marketplace_id": marketplace_id}, "name")
		if not amazon_store:
			continue
		store_doc = frappe.get_doc("Amazon Store Configuration", amazon_store)
		profile_id = str(profile.get("profileId") or "").strip()
		name = frappe.db.get_value(SITE_DOCTYPE, {"profile_id": profile_id}, "name")
		values = {
			"master_configuration": master.name,
			"amazon_store": amazon_store,
			"enabled": 1,
			"company": store_doc.company,
			"cost_center": store_doc.cost_center,
			"profile_id": profile_id,
			"advertiser_id": str(account.get("id") or ""),
			"seller_id": store_doc.seller_id,
			"account_name": account.get("name") or store_doc.store_name,
			"marketplace_id": marketplace_id,
			"country": store_doc.country,
			"currency_code": profile.get("currencyCode"),
			"timezone": profile.get("timezone") or "Asia/Shanghai",
		}
		if name:
			frappe.db.set_value(SITE_DOCTYPE, name, values)
			updated.append(name)
		elif create_site_configurations:
			configuration_name = f"{store_doc.store_name}-广告配置"
			doc = frappe.get_doc({"doctype": SITE_DOCTYPE, "configuration_name": configuration_name, **values})
			doc.insert(ignore_permissions=True)
			created.append(doc.name)
	frappe.db.commit()
	return {"profiles": profiles, "created": created, "updated": updated}


def run_scheduled_sync():
	now = now_datetime()
	queued = []
	for row in frappe.get_all(
		SITE_DOCTYPE,
		filters={"enabled": 1},
		fields=["name", "sync_status", "next_sync_at"],
		limit_page_length=0,
	):
		if row.sync_status in BUSY_STATUSES:
			continue
		if row.next_sync_at and row.next_sync_at > now:
			continue
		try:
			start_site_sync(row.name, "latest")
			queued.append(row.name)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Amazon广告调度失败：{row.name}")
	return queued


def _enabled_ad_products(site):
	products = []
	if cint(site.sponsored_products_enabled):
		products.append("SPONSORED_PRODUCTS")
	if cint(site.sponsored_brands_enabled):
		products.append("SPONSORED_BRANDS")
	if cint(site.sponsored_display_enabled):
		products.append("SPONSORED_DISPLAY")
	return products


def _requested_range(master, mode, days):
	end = getdate(today())
	if mode == "history":
		start = getdate(master.history_start_date)
		end = min(getdate(master.history_end_date), end) if master.history_end_date else end
	elif mode == "recheck":
		days = max(cint(days), 1)
		start = end - timedelta(days=days - 1)
	else:
		lookback = max(cint(master.routine_lookback_days), 1)
		start = end - timedelta(days=lookback - 1)
	return start, end


def _segments(start_date, end_date, days):
	cursor = start_date
	while cursor <= end_date:
		segment_end = min(cursor + timedelta(days=days - 1), end_date)
		yield cursor, segment_end
		cursor = segment_end + timedelta(days=1)


def _flatten_entity(resource, row):
	"""把统一结构API返回的嵌套对象转换成存储层能识别的通用键。"""
	result = dict(row)
	result["adProduct"] = row.get("adProduct")
	if resource == "campaigns":
		result.update({
			"campaignId": row.get("campaignId") or row.get("id"),
			"campaignName": row.get("name"),
			"campaignStatus": row.get("state"),
			"campaignStartDate": row.get("startDateTime") or row.get("startDate"),
			"campaignEndDate": row.get("endDateTime") or row.get("endDate"),
			"portfolioId": row.get("portfolioId"),
		})
		budgets = row.get("budgets") or []
		if budgets:
			budget = budgets[0]
			result["campaignBudgetType"] = budget.get("recurrenceTimePeriod") or budget.get("budgetType")
			result["campaignBudgetAmount"] = _nested(budget, "budgetValue", "monetaryBudgetValue", "monetaryBudget", "value")
	elif resource == "adGroups":
		result.update({
			"campaignId": row.get("campaignId"),
			"adGroupId": row.get("adGroupId") or row.get("id"),
			"adGroupName": row.get("name"),
			"adGroupStatus": row.get("state"),
			"defaultBid": _nested(row, "defaultBid", "value") or row.get("defaultBid"),
		})
	elif resource == "ads":
		product = _nested(row, "creative", "productCreative", "productCreativeSettings", "advertisedProduct") or {}
		result.update({
			"campaignId": row.get("campaignId"),
			"adGroupId": row.get("adGroupId"),
			"adId": row.get("adId") or row.get("id"),
			"adStatus": row.get("state"),
			"advertisedAsin": product.get("productId") if product.get("productIdType") == "ASIN" else None,
			"advertisedSku": product.get("productId") if product.get("productIdType") == "SKU" else None,
		})
	elif resource == "targets":
		result.update({
			"campaignId": row.get("campaignId"),
			"adGroupId": row.get("adGroupId"),
			"targetingId": row.get("targetId") or row.get("id"),
			"targetingStatus": row.get("state"),
			"targeting": json.dumps(row.get("targetDetails") or {}, ensure_ascii=False),
			"keyword": _nested(row, "targetDetails", "keywordTarget", "keyword"),
			"matchType": _nested(row, "targetDetails", "keywordTarget", "matchType"),
		})
	return result


def _nested(value, *keys):
	for key in keys:
		if not isinstance(value, dict):
			return None
		value = value.get(key)
	return value


def _profile_backfill(site, profile):
	account = profile.get("accountInfo") or {}
	values = {
		"advertiser_id": str(account.get("id") or site.advertiser_id or ""),
		"account_name": account.get("name") or site.account_name,
		"currency_code": profile.get("currencyCode") or site.currency_code,
		"timezone": profile.get("timezone") or site.timezone,
	}
	frappe.db.set_value(SITE_DOCTYPE, site.name, values, update_modified=False)
	for key, value in values.items():
		setattr(site, key, value)


def _update_site(name, **values):
	frappe.db.set_value(SITE_DOCTYPE, name, values, update_modified=False)


def _update_master(name, **values):
	frappe.db.set_value(MASTER_DOCTYPE, name, values, update_modified=False)


def _summary(result):
	rows = sum(cint(row.get("rows")) for row in result.get("reports") or [])
	created = sum(cint(row.get("created")) for row in result.get("reports") or [])
	updated = sum(cint(row.get("updated")) for row in result.get("reports") or [])
	return (
		f"批次{result.get('batch_id')}：报告{len(result.get('reports') or [])}段，"
		f"读取{rows}行，新建{created}行，更新{updated}行；"
		f"跳过{len(result.get('skipped') or [])}项，异常{len(result.get('errors') or [])}项。"
	)
