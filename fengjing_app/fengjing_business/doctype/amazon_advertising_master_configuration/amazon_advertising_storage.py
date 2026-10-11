# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""把Amazon广告结构与报告行规范化并幂等保存到ERPNext。"""

from __future__ import annotations

import hashlib
import json
from datetime import date

import frappe
from frappe.utils import flt, getdate, now_datetime


STRUCTURE_DOCTYPE = "Amazon Advertising Structure"
REPORT_DOCTYPES = {
	"campaign": "Amazon Advertising Campaign Daily",
	"product": "Amazon Advertising Product Daily",
	"targeting": "Amazon Advertising Targeting Daily",
	"search_term": "Amazon Advertising Targeting Daily",
	"placement": "Amazon Advertising Placement Daily",
	"purchased_product": "Amazon Advertising Purchased Product Daily",
}
_ITEM_CACHE = {}


def pick(row, *keys, default=None):
	for key in keys:
		if key in row and row.get(key) not in (None, ""):
			return row.get(key)
	return default


def number(row, *keys):
	return flt(pick(row, *keys, default=0))


def text(value):
	if value is None:
		return ""
	if isinstance(value, (dict, list)):
		return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
	return str(value).strip()


def row_json(row):
	return json.dumps(row, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def digest(*values):
	return hashlib.sha256("|".join(text(value) for value in values).encode("utf-8")).hexdigest()


def raw_hash(row):
	return hashlib.sha256(row_json(row).encode("utf-8")).hexdigest()


def common_values(site, sync_batch_id, sync_type, row):
	return {
		"master_configuration": site.master_configuration,
		"site_configuration": site.name,
		"amazon_store": site.amazon_store,
		"company": site.company,
		"cost_center": site.cost_center,
		"country": site.country,
		"marketplace_id": site.marketplace_id,
		"profile_id": site.profile_id,
		"sync_batch_id": sync_batch_id,
		"sync_type": sync_type,
		"fetched_at": now_datetime(),
		"source_updated_at": _datetime_value(pick(row, "lastUpdatedDateTime", "lastUpdatedAt", "updatedAt")),
		"raw_json_hash": raw_hash(row),
		"raw_json": row_json(row),
	}


def save_structure_entities(site, rows, sync_batch_id, *, ad_product=None, sync_type="结构同步"):
	stats = {"created": 0, "updated": 0, "unchanged": 0}
	for row in rows:
		entities = _structure_entities(row, ad_product)
		for values in entities:
			values = enrich_structure_context(site, values, sync_batch_id, sync_type)
			# 同一广告实体可能在不同站点出现，站点必须参与幂等键。
			values["unique_key"] = digest(site.name, values["unique_key"])
			result = _upsert(STRUCTURE_DOCTYPE, values["unique_key"], values)
			stats[result] += 1
	return stats


def save_report_rows(site, report_key, rows, sync_batch_id, sync_type, *, ad_product):
	doctype = REPORT_DOCTYPES[report_key]
	stats = {"created": 0, "updated": 0, "unchanged": 0}
	for row in rows:
		values = _report_values(site, report_key, row, sync_batch_id, sync_type, ad_product)
		result = _upsert(doctype, values["unique_key"], values)
		stats[result] += 1
	# 报告本身也能补全旧活动、广告组、广告、关键词和定向结构。
	structure_stats = save_structure_entities(
		site,
		rows,
		sync_batch_id,
		ad_product=ad_product,
		sync_type=sync_type,
	)
	stats["structure_created"] = structure_stats["created"]
	stats["structure_updated"] = structure_stats["updated"]
	return stats


def _upsert(doctype, unique_key, values):
	name = frappe.db.get_value(doctype, {"unique_key": unique_key}, "name")
	if name:
		current_hash = frappe.db.get_value(doctype, name, "raw_json_hash")
		if current_hash and current_hash == values.get("raw_json_hash"):
			frappe.db.set_value(
				doctype,
				name,
				{"fetched_at": values.get("fetched_at"), "sync_batch_id": values.get("sync_batch_id"), "sync_type": values.get("sync_type")},
				update_modified=False,
			)
			return "unchanged"
		doc = frappe.get_doc(doctype, name)
		doc.update(values)
		doc.save(ignore_permissions=True)
		return "updated"
	doc = frappe.get_doc({"doctype": doctype, **values})
	doc.insert(ignore_permissions=True)
	return "created"


def _report_values(site, report_key, row, sync_batch_id, sync_type, ad_product):
	common = common_values(site, sync_batch_id, sync_type, row)
	report_date = _date_value(pick(row, "date", "startDate", "reportDate"))
	campaign_id = text(pick(row, "campaignId", "campaign.id"))
	campaign_name = text(pick(row, "campaignName", "campaign.name"))
	currency = text(pick(row, "campaignBudgetCurrencyCode", "currencyCode", "budgetCurrency.value", default=site.currency_code))
	base = {
		**common,
		"report_date": report_date,
		"ad_product": ad_product,
		"currency_code": currency or site.currency_code,
		"campaign_id": campaign_id,
		"campaign_name": campaign_name,
		"campaign_status": text(pick(row, "campaignStatus", "campaign.state")),
	}
	metric = _metrics(row)
	if report_key == "campaign":
		identity = (site.name, report_key, report_date, ad_product, campaign_id)
		return {
			**base,
			"unique_key": digest(*identity),
			"campaign_type": text(pick(row, "campaignType", "campaign.type")),
			"targeting_type": text(pick(row, "targetingType")),
			"budget_type": text(pick(row, "campaignBudgetType", "budgetType")),
			"daily_budget": number(row, "campaignBudgetAmount", "dailyBudget"),
			"start_date": _date_value(pick(row, "campaignStartDate", "startDate")),
			"end_date": _date_value(pick(row, "campaignEndDate", "endDate")),
			**metric,
			"purchases_1d": number(row, "purchases1d"),
			"purchases_7d": number(row, "purchases7d"),
			"purchases_14d": number(row, "purchases14d"),
			"purchases_30d": number(row, "purchases30d"),
			"new_to_brand_purchases": number(row, "newToBrandPurchases14d", "newToBrandPurchases"),
			"new_to_brand_sales": number(row, "newToBrandSales14d", "newToBrandSales"),
			"new_to_brand_units": number(row, "newToBrandUnitsSold14d", "newToBrandUnitsSold"),
		}
	if report_key == "product":
		sku = text(pick(row, "advertisedSku", "sku"))
		asin = text(pick(row, "advertisedAsin", "asin"))
		item = _resolve_item(site, sku, asin)
		identity = (site.name, report_key, report_date, ad_product, campaign_id, pick(row, "adGroupId"), pick(row, "adId"), sku, asin)
		return {
			**base,
			"unique_key": digest(*identity),
			"ad_group_id": text(pick(row, "adGroupId")),
			"ad_group_name": text(pick(row, "adGroupName")),
			"ad_id": text(pick(row, "adId")),
			"seller_sku": sku,
			"asin": asin,
			"item_code": item.get("item_code"),
			"item_name": item.get("item_name"),
			"item_image": item.get("image"),
			**metric,
			"same_sku_purchases": number(row, "purchasesSameSku7d", "purchasesSameSku14d"),
			"same_sku_units": number(row, "unitsSoldSameSku7d", "unitsSoldSameSku14d"),
			"same_sku_sales": number(row, "salesSameSku7d", "salesSameSku14d"),
			"new_to_brand_purchases": number(row, "newToBrandPurchases14d", "newToBrandPurchases"),
			"new_to_brand_sales": number(row, "newToBrandSales14d", "newToBrandSales"),
		}
	if report_key in {"targeting", "search_term"}:
		record_type = "搜索词" if report_key == "search_term" else ("关键词" if pick(row, "keywordId", "keyword") else "商品定向")
		sku = text(pick(row, "advertisedSku"))
		asin = text(pick(row, "advertisedAsin"))
		item = _resolve_item(site, sku, asin)
		identity_value = pick(row, "searchTerm", "keywordId", "targetingId", "keyword", "targeting", "targetingExpression")
		identity = (site.name, report_key, report_date, ad_product, campaign_id, pick(row, "adGroupId"), identity_value, sku, asin)
		return {
			**base,
			"unique_key": digest(*identity),
			"record_type": record_type,
			"ad_group_id": text(pick(row, "adGroupId")),
			"ad_group_name": text(pick(row, "adGroupName")),
			"keyword_id": text(pick(row, "keywordId")),
			"keyword_text": text(pick(row, "keyword", "keywordText")),
			"search_term": text(pick(row, "searchTerm")),
			"target_id": text(pick(row, "targetingId", "targetId")),
			"targeting_expression": text(pick(row, "targeting", "targetingExpression")),
			"match_type": text(pick(row, "matchType", "keywordType")),
			"bid": number(row, "keywordBid", "targetingBid", "bid"),
			"advertised_sku": sku,
			"advertised_asin": asin,
			"item_code": item.get("item_code"),
			**metric,
		}
	if report_key == "placement":
		placement = text(pick(row, "placementClassification", "placement", "placementName"))
		identity = (site.name, report_key, report_date, ad_product, campaign_id, placement)
		return {
			**base,
			"unique_key": digest(*identity),
			"placement_classification": placement,
			"placement_name": text(pick(row, "placementName", "placement")),
			"bid_adjustment": number(row, "bidAdjustment", "percentage"),
			"campaign_budget_amount": number(row, "campaignBudgetAmount"),
			"campaign_budget_type": text(pick(row, "campaignBudgetType")),
			**metric,
		}
	if report_key == "purchased_product":
		advertised_sku = text(pick(row, "advertisedSku"))
		advertised_asin = text(pick(row, "advertisedAsin"))
		purchased_sku = text(pick(row, "purchasedSku"))
		purchased_asin = text(pick(row, "purchasedAsin"))
		advertised_item = _resolve_item(site, advertised_sku, advertised_asin)
		purchased_item = _resolve_item(site, purchased_sku, purchased_asin)
		identity = (
			site.name,
			report_key,
			report_date,
			ad_product,
			campaign_id,
			campaign_name,
			pick(row, "adGroupId"),
			pick(row, "adGroupName"),
			advertised_asin,
			purchased_asin,
		)
		return {
			**base,
			"unique_key": digest(*identity),
			"ad_group_id": text(pick(row, "adGroupId")),
			"ad_group_name": text(pick(row, "adGroupName")),
			"ad_id": text(pick(row, "adId")),
			"advertised_sku": advertised_sku,
			"advertised_asin": advertised_asin,
			"advertised_item": advertised_item.get("item_code"),
			"advertised_item_name": advertised_item.get("item_name"),
			"advertised_item_image": advertised_item.get("image"),
			"purchased_sku": purchased_sku,
			"purchased_asin": purchased_asin,
			"purchased_product_name": text(pick(row, "productName")),
			"purchased_product_category": text(pick(row, "productCategory")),
			"attribution_type": text(pick(row, "attributionType")),
			"purchased_item": purchased_item.get("item_code"),
			"purchased_item_name": purchased_item.get("item_name"),
			"purchased_item_image": purchased_item.get("image"),
			"purchases": number(row, "purchases7d", "purchases14d", "orders14d", "purchases"),
			"units_sold": number(row, "unitsSoldClicks7d", "unitsSoldClicks14d", "unitsSold14d", "unitsSold"),
			"sales": number(row, "sales7d", "sales14d", "sales"),
			"new_to_brand_purchases": number(row, "newToBrandPurchases14d", "newToBrandPurchases"),
			"new_to_brand_units": number(row, "newToBrandUnitsSold14d", "newToBrandUnitsSold"),
			"new_to_brand_sales": number(row, "newToBrandSales14d", "newToBrandSales"),
		}
	raise ValueError(f"未知广告报告类型：{report_key}")


def _metrics(row):
	impressions = number(row, "impressions", "metric.impressions")
	clicks = number(row, "clicks", "metric.clicks")
	cost = number(row, "cost", "spend", "metric.totalCost", "metric.supplyCost")
	purchases = number(row, "purchases7d", "purchases14d", "purchases", "metric.purchases")
	units = number(row, "unitsSoldClicks7d", "unitsSoldClicks14d", "unitsSold", "metric.unitsSold")
	sales = number(row, "sales7d", "sales14d", "sales", "metric.sales")
	return {
		"impressions": impressions,
		"clicks": clicks,
		"cost": cost,
		"purchases": purchases,
		"units_sold": units,
		"sales": sales,
		"click_through_rate": number(row, "clickThroughRate", "ctr", "metric.ctr") or (clicks / impressions * 100 if impressions else 0),
		"cost_per_click": number(row, "costPerClick", "cpc") or (cost / clicks if clicks else 0),
		"conversion_rate": number(row, "conversionRate", "purchaseRate", "metric.purchaseRate") or (purchases / clicks * 100 if clicks else 0),
		"acos": number(row, "acosClicks7d", "acosClicks14d", "acos") or (cost / sales * 100 if sales else 0),
		"roas": number(row, "roasClicks7d", "roasClicks14d", "roas", "metric.roas") or (sales / cost if cost else 0),
	}


def _structure_entities(row, ad_product):
	entities = []
	common = {
		"ad_product": ad_product,
		"currency_code": text(pick(row, "campaignBudgetCurrencyCode", "currencyCode")),
		"portfolio_id": text(pick(row, "portfolioId")),
		"campaign_id": text(pick(row, "campaignId")),
		"campaign_name": text(pick(row, "campaignName")),
		"ad_group_id": text(pick(row, "adGroupId")),
		"ad_group_name": text(pick(row, "adGroupName")),
		"ad_id": text(pick(row, "adId")),
		"seller_sku": text(pick(row, "advertisedSku", "sku")),
		"asin": text(pick(row, "advertisedAsin", "asin")),
		"state": text(pick(row, "state", "campaignStatus", "adGroupStatus", "adStatus", "keywordStatus", "targetingStatus")),
		"serving_status": text(pick(row, "servingStatus")),
		"budget_type": text(pick(row, "campaignBudgetType", "budgetType")),
		"budget_amount": number(row, "campaignBudgetAmount", "budget"),
		"default_bid": number(row, "adGroupDefaultBid", "defaultBid"),
		"bid": number(row, "keywordBid", "targetingBid", "bid"),
		"start_date": _date_value(pick(row, "campaignStartDate", "startDate")),
		"end_date": _date_value(pick(row, "campaignEndDate", "endDate")),
	}
	definitions = []
	if common["campaign_id"]:
		definitions.append(("广告活动", common["campaign_id"], common["campaign_name"]))
	if common["ad_group_id"]:
		definitions.append(("广告组", common["ad_group_id"], common["ad_group_name"]))
	if common["ad_id"]:
		definitions.append(("广告", common["ad_id"], common["seller_sku"] or common["asin"]))
	keyword_id = text(pick(row, "keywordId"))
	keyword_text = text(pick(row, "keyword", "keywordText"))
	if keyword_id or keyword_text:
		definitions.append(("关键词", keyword_id or digest(common["ad_group_id"], keyword_text), keyword_text))
	target_id = text(pick(row, "targetingId", "targetId"))
	target_expression = text(pick(row, "targeting", "targetingExpression", "targetDetails"))
	if target_id or target_expression:
		definitions.append(("商品定向", target_id or digest(common["ad_group_id"], target_expression), target_expression))
	for entity_type, entity_id, entity_name in definitions:
		values = {
			"unique_key": digest("structure", ad_product, entity_type, entity_id),
			"entity_type": entity_type,
			"entity_id": entity_id,
			"entity_name": entity_name,
			**common,
			"keyword_id": keyword_id if entity_type == "关键词" else None,
			"keyword_text": keyword_text if entity_type == "关键词" else None,
			"match_type": text(pick(row, "matchType", "keywordType")),
			"target_id": target_id if entity_type == "商品定向" else None,
			"targeting_expression": target_expression if entity_type == "商品定向" else None,
			"raw_json_hash": raw_hash(row),
			"raw_json": row_json(row),
			"fetched_at": now_datetime(),
		}
		entities.append(values)
	return entities


def enrich_structure_context(site, values, sync_batch_id, sync_type="日常同步"):
	values.update({
		"master_configuration": site.master_configuration,
		"site_configuration": site.name,
		"amazon_store": site.amazon_store,
		"company": site.company,
		"cost_center": site.cost_center,
		"country": site.country,
		"marketplace_id": site.marketplace_id,
		"profile_id": site.profile_id,
		"sync_batch_id": sync_batch_id,
		"sync_type": sync_type,
	})
	item = _resolve_item(site, values.get("seller_sku"), values.get("asin"))
	values["item_code"] = item.get("item_code")
	values["item_name"] = item.get("item_name")
	return values


def _resolve_item(site, sku, asin):
	key = (site.cost_center, text(sku), text(asin))
	if key in _ITEM_CACHE:
		return _ITEM_CACHE[key]
	filters_base = {"启用": 1}
	if site.cost_center:
		filters_base["店铺"] = site.cost_center
	row = None
	for fieldname, value in (("平台sku", text(sku)), ("平台asin", text(asin))):
		if not value:
			continue
		row = frappe.db.get_value(
			"Fengjing - Product Corresponding Platform - Main Table",
			{**filters_base, fieldname: value},
			["物料id", "物料名称"],
			as_dict=True,
		)
		if row:
			break
	result = {"item_code": row.get("物料id") if row else None, "item_name": row.get("物料名称") if row else None, "image": None}
	if result["item_code"]:
		item = frappe.db.get_value("Item", result["item_code"], ["item_name", "image"], as_dict=True)
		if item:
			result["item_name"] = result["item_name"] or item.item_name
			result["image"] = item.image
	_ITEM_CACHE[key] = result
	return result


def _date_value(value):
	if not value:
		return None
	if isinstance(value, date):
		return value
	try:
		return getdate(str(value)[:10])
	except Exception:
		return None


def _datetime_value(value):
	if not value:
		return None
	try:
		return str(value).replace("T", " ").replace("Z", "")[:19]
	except Exception:
		return None
