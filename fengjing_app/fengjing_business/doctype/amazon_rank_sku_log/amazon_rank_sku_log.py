# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""Amazon Catalog 排名快照存储及平台物料映射。"""

import json

import frappe
from frappe.model.document import Document
from frappe.utils import add_to_date, cint, now_datetime


class AmazonRankSKULog(Document):
	pass


def _text(value):
	return str(value or "").strip()


def 获取平台映射物料(店铺, 站点id=None, asin=None, sku=None):
	"""依次按 ASIN+SKU、ASIN、SKU 匹配店铺的平台物料。

	该公共函数也被 Amazon 订单和财务存储程序复用，不能随排名旧程序删除。
	"""
	基础条件 = {
		"启用": 1,
		"店铺": _text(店铺),
		"站点id": _text(站点id).upper(),
	}
	标准asin = _text(asin).upper()
	标准sku = _text(sku)
	if not 基础条件["店铺"]:
		return None
	if 标准asin and 标准sku:
		物料 = frappe.db.get_value(
			"Fengjing - Product Corresponding Platform - Main Table",
			{**基础条件, "平台asin": 标准asin, "平台sku": 标准sku},
			"物料id",
		)
		if 物料:
			return 物料
	if 标准asin:
		物料 = frappe.db.get_value(
			"Fengjing - Product Corresponding Platform - Main Table",
			{**基础条件, "平台asin": 标准asin, "平台sku": ["in", ["", None]]},
			"物料id",
		)
		if 物料:
			return 物料
	if 标准sku:
		return frappe.db.get_value(
			"Fengjing - Product Corresponding Platform - Main Table",
			{**基础条件, "平台sku": 标准sku},
			"物料id",
		)
	return None


def _marketplace_entry(entries, marketplace_id):
	entries = entries or []
	return next(
		(entry for entry in entries if _text(entry.get("marketplaceId")) == marketplace_id),
		entries[0] if entries else {},
	)


def _main_image(payload, marketplace_id):
	group = _marketplace_entry(payload.get("images"), marketplace_id)
	images = group.get("images") or []
	preferred = [image for image in images if _text(image.get("variant")).upper() == "MAIN"] or images
	if not preferred:
		return {}
	return max(preferred, key=lambda image: cint(image.get("width")) * cint(image.get("height")))


def _rank_details(payload, marketplace_id):
	group = _marketplace_entry(payload.get("salesRanks"), marketplace_id)
	display = (group.get("displayGroupRanks") or [{}])[0]
	classification = (group.get("classificationRanks") or [{}])[0]
	return display, classification


def _product_type(payload, marketplace_id):
	entry = _marketplace_entry(payload.get("productTypes"), marketplace_id)
	return _text(entry.get("productType"))


def save_rank_snapshot(target, config, store, catalog_payload):
	"""把一次 Catalog Items API 结果写成不可变的排名快照。"""
	payload = catalog_payload.get("payload") if isinstance(catalog_payload.get("payload"), dict) else catalog_payload
	marketplace_id = _text(target.marketplace_id or store.marketplace_id).upper()
	summary = _marketplace_entry(payload.get("summaries"), marketplace_id)
	image = _main_image(payload, marketplace_id)
	main_rank, detail_rank = _rank_details(payload, marketplace_id)
	product_type = _product_type(payload, marketplace_id)
	asin = _text(payload.get("asin") or target.asin).upper()
	item_code = target.corresponding_item or 获取平台映射物料(
		store.cost_center, marketplace_id, asin, target.sku
	)
	item_name = frappe.db.get_value("Item", item_code, "item_name") if item_code else None
	fetched_at = now_datetime()
	context = {
		"ranking_configuration": config.name,
		"ranking_product": target.name,
		"amazon_store": store.name,
		"store_name": store.store_name,
		"cost_center": store.cost_center,
		"marketplace_id": marketplace_id,
		"source": target.source,
		"is_competitor": cint(target.is_competitor),
		"fetched_at": str(fetched_at),
	}
	doc = frappe.get_doc({
		"doctype": "Amazon Rank SKU Log",
		"ranking_product": target.name,
		"商品列表api_asin": asin,
		"商品列表api_sku": target.sku,
		"商品列表api_站点id": marketplace_id,
		"商品列表api_商品标题": _text(summary.get("itemName") or target.product_title),
		"商品列表api_产品类型": product_type,
		"商品列表api_成色": _text(summary.get("conditionType")),
		"商品列表api_状态": target.listing_status,
		"商品列表api_创建时间": _text(summary.get("createdDate")),
		"商品列表api_最后更新时间": _text(summary.get("lastUpdatedDate")),
		"商品列表api_主图链接": _text(image.get("link") or target.amazon_image_url),
		"商品列表api_图片宽": image.get("width"),
		"商品列表api_图片高": image.get("height"),
		"排名api_asin": asin,
		"排名api_站点id": marketplace_id,
		"排名api_商品名称": _text(summary.get("itemName") or target.product_title),
		"排名api_品牌": _text(summary.get("brand")),
		"排名api_制造商": _text(summary.get("manufacturer")),
		"排名api_型号": _text(summary.get("modelNumber")),
		"排名api_零件编号": _text(summary.get("partNumber")),
		"排名api_颜色": _text(summary.get("color")),
		"排名api_尺寸": _text(summary.get("size")),
		"排名api_样式": _text(summary.get("style")),
		"排名api_主类目排名": cint(main_rank.get("rank")) or None,
		"排名api_主类目名称": _text(main_rank.get("title")),
		"排名api_主类目链接": _text(main_rank.get("link")),
		"排名api_细分类目排名": cint(detail_rank.get("rank")) or None,
		"排名api_细分类目名称": _text(detail_rank.get("title")),
		"排名api_细分类目链接": _text(detail_rank.get("link")),
		"排名api_分类id": _text(detail_rank.get("classificationId")),
		"排名api_浏览节点名称": _text(detail_rank.get("title")),
		"排名api_浏览节点id": _text(detail_rank.get("classificationId")),
		"排名api_网站显示分组": _text(summary.get("websiteDisplayGroup")),
		"排名api_网站显示分组名称": _text(summary.get("websiteDisplayGroupName")),
		"排名api_商品分类类型": product_type,
		"排名api_包装数量": summary.get("packageQuantity"),
		"排名api_发布日期": _text(summary.get("releaseDate")),
		"抓取数据的时间": fetched_at,
		"属于哪个店铺": store.cost_center,
		"是否同行": cint(target.is_competitor),
		"绑定的物料": item_code,
		"物料名称": item_name,
		"原始json": json.dumps(catalog_payload, ensure_ascii=False, indent=2, default=str),
		"抓取上下文json": json.dumps(context, ensure_ascii=False, indent=2, default=str),
	})
	doc.insert(ignore_permissions=True)
	updates = {}
	if item_code and not target.corresponding_item:
		updates["corresponding_item"] = item_code
	if image.get("link"):
		updates["amazon_image_url"] = image.get("link")
	if summary.get("itemName"):
		updates["product_title"] = summary.get("itemName")
	if updates:
		frappe.db.set_value("Amazon Ranking Product", target.name, updates, update_modified=False)
	return doc.name


def 清理过期排名日志():
	"""按每个新排名配置的保留天数分批清理；旧未关联记录默认保留5年。"""
	total = 0
	for config in frappe.get_all("Amazon Ranking Configuration", fields=["name", "retention_days"]):
		retention_days = cint(config.retention_days) or 1825
		cutoff = add_to_date(now_datetime(), days=-retention_days)
		while True:
			names = frappe.db.sql(
				"""
				SELECT log.name
				FROM `tabAmazon Rank SKU Log` log
				INNER JOIN `tabAmazon Ranking Product` product ON product.name = log.ranking_product
				WHERE product.ranking_configuration = %s
				  AND COALESCE(log.`抓取数据的时间`, log.creation) < %s
				LIMIT 5000
				""",
				(config.name, cutoff),
				pluck=True,
			)
			if not names:
				break
			frappe.db.delete("Amazon Rank SKU Log", {"name": ["in", names]})
			total += len(names)
			frappe.db.commit()
	legacy_cutoff = add_to_date(now_datetime(), days=-1825)
	while True:
		names = frappe.get_all(
			"Amazon Rank SKU Log",
			filters={
				"ranking_product": ["is", "not set"],
				"creation": ["<", legacy_cutoff],
			},
			pluck="name",
			limit_page_length=5000,
		)
		if not names:
			break
		frappe.db.delete("Amazon Rank SKU Log", {"name": ["in", names]})
		total += len(names)
		frappe.db.commit()
	frappe.logger("amazon_rank", allow_site=True).info("Amazon rank log cleanup removed %s records", total)
	return total
