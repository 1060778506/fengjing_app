"""Amazon 排名抓取商品池。"""

import hashlib
import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, cint, now_datetime


class AmazonRankingProduct(Document):
	def validate(self):
		self.asin = str(self.asin or "").strip().upper()
		self.sku = str(self.sku or "").strip()
		if not self.asin:
			frappe.throw(_("ASIN is required"))
		config = frappe.get_doc("Amazon Ranking Configuration", self.ranking_configuration)
		store = frappe.get_doc("Amazon Store Configuration", config.amazon_store)
		self.amazon_store = store.name
		self.marketplace_id = str(store.marketplace_id or "").strip().upper()
		self.target_key = make_target_key(config.name, self.asin)
		if self.source == "Manual Entry":
			self.is_competitor = 1


def make_target_key(configuration, asin):
	value = f"{configuration}|{str(asin or '').strip().upper()}"
	return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text(value):
	return str(value or "").strip()


def _first_summary(item, marketplace_id):
	summaries = item.get("summaries") or []
	return next(
		(summary for summary in summaries if _text(summary.get("marketplaceId")) == marketplace_id),
		summaries[0] if summaries else {},
	)


def upsert_discovered_product(config, store, item, discovered_at=None):
	"""把 Listings Items API 的一个商品写入商品池。"""
	discovered_at = discovered_at or now_datetime()
	marketplace_id = _text(store.marketplace_id).upper()
	summary = _first_summary(item, marketplace_id)
	asin = _text(summary.get("asin") or item.get("asin")).upper()
	sku = _text(item.get("sku") or summary.get("sellerSku"))
	if not asin:
		return None
	key = make_target_key(config.name, asin)
	name = frappe.db.get_value("Amazon Ranking Product", {"target_key": key}, "name")
	product = frappe.get_doc("Amazon Ranking Product", name) if name else frappe.new_doc("Amazon Ranking Product")
	if not name:
		product.ranking_configuration = config.name
		product.asin = asin
		product.source = "Automatic Discovery"
		product.is_competitor = 0
		product.first_discovered_at = discovered_at
	product.amazon_store = store.name
	product.marketplace_id = marketplace_id
	product.sku = sku or product.sku
	product.product_title = _text(summary.get("itemName") or product.product_title)
	status = summary.get("status") or []
	product.listing_status = ", ".join(status) if isinstance(status, list) else _text(status)
	main_image = summary.get("mainImage") or {}
	product.amazon_image_url = _text(main_image.get("link") or main_image.get("url") or product.amazon_image_url)
	product.last_discovered_at = discovered_at
	product.deleted_from_store = 0
	product.enabled = 1
	product.latest_discovery_json = json.dumps(item, ensure_ascii=False, indent=2, default=str)
	if not product.corresponding_item:
		from fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log import 获取平台映射物料

		product.corresponding_item = 获取平台映射物料(
			store.cost_center, marketplace_id, asin, sku
		)
	product.save(ignore_permissions=True)
	return product.name


def mark_missing_automatic_products(config_name, discovered_names):
	"""只处理自动发现的自有商品，不动手工添加的同行 ASIN。"""
	filters = {
		"ranking_configuration": config_name,
		"source": "Automatic Discovery",
		"deleted_from_store": 0,
	}
	for row in frappe.get_all("Amazon Ranking Product", filters=filters, fields=["name"]):
		if row.name not in discovered_names:
			frappe.db.set_value(
				"Amazon Ranking Product",
				row.name,
				{"deleted_from_store": 1, "enabled": 0},
				update_modified=False,
			)


def get_due_products(config, include_all=False):
	rows = frappe.get_all(
		"Amazon Ranking Product",
		filters={
			"ranking_configuration": config.name,
			"enabled": 1,
			"deleted_from_store": 0,
		},
		fields=["name", "next_fetch_at"],
		order_by="next_fetch_at asc, creation asc",
		limit_page_length=0,
	)
	if include_all:
		return [row.name for row in rows]
	now_value = now_datetime()
	return [row.name for row in rows if not row.next_fetch_at or row.next_fetch_at <= now_value]


def set_fetch_result(product_name, success, interval_minutes, error=None):
	now_value = now_datetime()
	frappe.db.set_value(
		"Amazon Ranking Product",
		product_name,
		{
			"last_fetch_status": "Success" if success else "Failed",
			"last_fetch_at": now_value,
			"next_fetch_at": add_to_date(now_value, minutes=max(cint(interval_minutes), 1)),
			"last_error": "" if success else _text(error)[:2000],
		},
		update_modified=False,
	)
