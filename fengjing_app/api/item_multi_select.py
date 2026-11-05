"""Shared item search used by the Fengjing multi-select dialog."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.query_builder.functions import Sum
from frappe.utils import cint


PURCHASE_DOCTYPES = {
	"Purchase Invoice",
	"Purchase Order",
	"Purchase Receipt",
	"Request for Quotation",
	"Supplier Quotation",
	"Subcontracting Order",
	"Subcontracting Receipt",
}

SALES_DOCTYPES = {
	"Delivery Note",
	"Opportunity",
	"POS Invoice",
	"Quotation",
	"Sales Invoice",
	"Sales Order",
}


@frappe.whitelist()
def search_items(
	doctype: str | None = None,
	txt: str | None = None,
	start: int | str = 0,
	page_length: int | str = 50,
	company: str | None = None,
	product_bundle: str | None = None,
) -> dict:
	"""Return a permission-aware, paginated item list with stock and supplier hints."""
	frappe.has_permission("Item", "read", throw=True)

	doctype = (doctype or "").strip()
	txt = (txt or "").strip()
	product_bundle = (product_bundle or "").strip()
	start = max(cint(start), 0)
	page_length = min(max(cint(page_length), 1), 100)

	filters: dict = {"disabled": 0}
	if doctype in PURCHASE_DOCTYPES:
		filters["is_purchase_item"] = 1
	elif doctype in SALES_DOCTYPES:
		filters["is_sales_item"] = 1

	bundle_qty_by_item: dict[str, float] = {}
	if product_bundle:
		bundle_doc = frappe.get_doc("Product Bundle", product_bundle)
		if bundle_doc.disabled:
			frappe.throw(_("Product Bundle {0} is disabled.").format(product_bundle))
		frappe.has_permission("Product Bundle", "read", doc=bundle_doc, throw=True)
		for row in bundle_doc.items:
			bundle_qty_by_item[row.item_code] = (
				bundle_qty_by_item.get(row.item_code, 0) + (row.qty or 0)
			)
		if not bundle_qty_by_item:
			return {
				"items": [],
				"start": 0,
				"page_length": page_length,
				"has_more": False,
				"next_start": None,
			}
		filters["name"] = ("in", list(bundle_qty_by_item))

	or_filters = None
	if txt:
		like = f"%{txt}%"
		or_filters = {
			"item_code": ("like", like),
			"item_name": ("like", like),
			"description": ("like", like),
			"item_group": ("like", like),
		}

	fields = [
		"name as item_code",
		"item_name",
		"image",
		"stock_uom",
		"item_group",
		"is_stock_item",
		"is_purchase_item",
		"is_sales_item",
	]
	items = frappe.get_list(
		"Item",
		filters=filters,
		or_filters=or_filters,
		fields=fields,
		order_by="modified desc, name asc",
		start=start,
		page_length=page_length + 1,
	)

	has_more = len(items) > page_length
	items = items[:page_length]
	item_codes = [row.item_code for row in items]

	stock_by_item: dict[str, float] = {}
	if item_codes:
		bin_table = frappe.qb.DocType("Bin")
		stock_rows = (
			frappe.qb.from_(bin_table)
			.select(bin_table.item_code, Sum(bin_table.actual_qty).as_("actual_qty"))
			.where(bin_table.item_code.isin(item_codes))
			.groupby(bin_table.item_code)
		).run(as_dict=True)
		stock_by_item = {row.item_code: row.actual_qty or 0 for row in stock_rows}

	default_supplier_by_item: dict[str, str] = {}
	if item_codes:
		item_default_filters: dict = {"parent": ("in", item_codes)}
		if company:
			item_default_filters["company"] = company

		for row in frappe.get_all(
			"Item Default",
			filters=item_default_filters,
			fields=["parent", "default_supplier"],
			order_by="idx asc",
		):
			if row.default_supplier and row.parent not in default_supplier_by_item:
				default_supplier_by_item[row.parent] = row.default_supplier

	for row in items:
		row.actual_qty = stock_by_item.get(row.item_code, 0)
		row.default_supplier = default_supplier_by_item.get(row.item_code, "")
		row.bundle_qty = bundle_qty_by_item.get(row.item_code, 0)

	return {
		"items": items,
		"start": start,
		"page_length": page_length,
		"has_more": has_more,
		"next_start": start + page_length if has_more else None,
	}


@frappe.whitelist()
def get_product_bundle_options(
	txt: str | None = None, page_length: int | str = 100
) -> list[dict]:
	"""Return enabled Product Bundles for the filter strip in the item picker."""
	frappe.has_permission("Product Bundle", "read", throw=True)
	txt = (txt or "").strip()
	page_length = min(max(cint(page_length), 1), 200)
	filters = {"disabled": 0}
	or_filters = None
	if txt:
		like = f"%{txt}%"
		or_filters = {
			"name": ("like", like),
			"new_item_code": ("like", like),
			"description": ("like", like),
		}

	bundles = frappe.get_list(
		"Product Bundle",
		filters=filters,
		or_filters=or_filters,
		fields=["name", "new_item_code", "description"],
		order_by="modified desc, name asc",
		page_length=page_length,
	)
	item_codes = [row.new_item_code for row in bundles if row.new_item_code]
	item_details = {}
	if item_codes:
		item_details = {
			row.name: row
			for row in frappe.get_all(
				"Item",
				filters={"name": ("in", item_codes)},
				fields=["name", "item_name", "image"],
			)
		}

	for row in bundles:
		item = item_details.get(row.new_item_code, frappe._dict())
		row.item = row.new_item_code
		row.item_name = item.get("item_name") or row.description or row.new_item_code
		row.image = item.get("image") or ""
	return bundles


@frappe.whitelist()
def get_items_by_codes(item_codes: list[str] | str | None = None) -> list[dict]:
	"""Resolve selected codes again before insertion; never trust browser-provided labels."""
	frappe.has_permission("Item", "read", throw=True)
	item_codes = frappe.parse_json(item_codes) if isinstance(item_codes, str) else item_codes
	item_codes = list(dict.fromkeys(code for code in (item_codes or []) if code))
	if len(item_codes) > 100:
		frappe.throw(_("A maximum of 100 items can be added at one time."))
	if not item_codes:
		return []

	rows = frappe.get_list(
		"Item",
		filters={"name": ("in", item_codes), "disabled": 0},
		fields=["name as item_code", "item_name", "image", "stock_uom"],
		page_length=len(item_codes),
	)
	by_code = {row.item_code: row for row in rows}
	return [by_code[code] for code in item_codes if code in by_code]
