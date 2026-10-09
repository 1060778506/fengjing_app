# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

from collections import Counter

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class AnalysisMaterialmovement(Document):
	pass


def _as_rows(value):
	"""兼容前端传入的 JSON 字符串和已解析列表。"""
	if isinstance(value, str):
		value = frappe.parse_json(value)
	return value or []


def _expand_bundle_rows(bundle_rows, preserve_package=False):
	"""将套件展开为真实库存物料，并按业务需要合并重复物料。"""
	source_rows = _as_rows(bundle_rows)
	bundle_cache = {}
	item_cache = {}
	result_by_key = {}

	for index, source in enumerate(source_rows, start=1):
		bundle_name = (source.get("套件") or "").strip()
		bundle_qty = flt(source.get("数量"))
		package_number = (source.get("temu包裹号") or "").strip()

		if not bundle_name:
			frappe.throw(_(f"套件物料解析第 {index} 行：请选择物料套件。"))
		if bundle_qty <= 0:
			frappe.throw(_(f"套件物料解析第 {index} 行：数量必须大于 0。"))

		if bundle_name not in bundle_cache:
			if not frappe.db.exists("Product Bundle", bundle_name):
				frappe.throw(_(f"物料套件 {bundle_name} 不存在。"))
			bundle = frappe.get_doc("Product Bundle", bundle_name)
			if cint(bundle.disabled):
				frappe.throw(_(f"物料套件 {bundle_name} 已停用。"))
			if not bundle.items:
				frappe.throw(_(f"物料套件 {bundle_name} 没有组件。"))
			bundle_cache[bundle_name] = bundle
		bundle = bundle_cache[bundle_name]

		for component in bundle.items:
			item_code = component.item_code
			if item_code not in item_cache:
				item_cache[item_code] = frappe.db.get_value(
					"Item",
					item_code,
					["item_name", "stock_uom", "disabled", "is_stock_item"],
					as_dict=True,
				)
			item = item_cache[item_code]
			if not item:
				frappe.throw(_(f"套件 {bundle_name} 中的物料 {item_code} 不存在。"))
			if cint(item.disabled):
				frappe.throw(_(f"套件 {bundle_name} 中的物料 {item_code} 已停用。"))
			if not cint(item.is_stock_item):
				frappe.throw(_(f"套件 {bundle_name} 中的物料 {item_code} 不是库存物料，不能进行物料移动。"))

			quantity = flt(bundle_qty * flt(component.qty), 6)
			if quantity <= 0:
				frappe.throw(_(f"套件 {bundle_name} 中的物料 {item_code} 数量必须大于 0。"))

			# 物料移动只需按物料合并；物料需求还要保留每个包裹的归属。
			result_key = (package_number, item_code) if preserve_package else item_code
			if result_key not in result_by_key:
				result_by_key[result_key] = {
					"item_code": item_code,
					"item_name": item.item_name or "",
					"stock_uom": item.stock_uom,
					"qty": 0,
				}
				if preserve_package:
					result_by_key[result_key]["temu_package_no"] = package_number
			result_by_key[result_key]["qty"] = flt(result_by_key[result_key]["qty"] + quantity, 6)

	return list(result_by_key.values())


def _check_stock_entry_permission():
	if not (
		frappe.has_permission("Stock Entry", ptype="create")
		or frappe.has_permission("Stock Entry", ptype="write")
	):
		frappe.throw(_("您没有解析物料移动套件的权限。"), frappe.PermissionError)


def _check_material_request_permission():
	if not (
		frappe.has_permission("Material Request", ptype="create")
		or frappe.has_permission("Material Request", ptype="write")
	):
		frappe.throw(_("您没有解析物料需求套件的权限。"), frappe.PermissionError)


@frappe.whitelist()
def parse_product_bundles(bundle_rows):
	"""供物料移动的“解析套件”按钮调用。"""
	_check_stock_entry_permission()
	source_rows = _as_rows(bundle_rows)
	rows = _expand_bundle_rows(source_rows)
	return {
		"rows": rows,
		"bundle_row_count": len(source_rows),
		"item_row_count": len(rows),
	}


@frappe.whitelist()
def parse_product_bundles_for_material_request(bundle_rows):
	"""供物料需求的“解析套件”按钮调用，并保留 Temu 包裹归属。"""
	_check_material_request_permission()
	source_rows = _as_rows(bundle_rows)
	rows = _expand_bundle_rows(source_rows, preserve_package=True)
	return {
		"rows": rows,
		"bundle_row_count": len(source_rows),
		"item_row_count": len(rows),
	}


def validate_product_bundle_movement(doc, method=None):
	"""保存或提交前校验套件配置与程序生成的物料明细。"""
	bundle_rows = doc.get("custom_物料套件移动") or []
	if not bundle_rows:
		return

	if doc.get("purpose") and doc.purpose != "Material Transfer":
		frappe.throw(_("“套件物料解析”只能用于“物料转移”类型的库存凭证。"))

	# 从物料需求创建物料移动时，同一套件可能属于多个 Temu 包裹。
	# 这些明细必须继续按包裹分行，不能仅按物料编码合并。
	expected_rows = _expand_bundle_rows(bundle_rows, preserve_package=True)
	generated_rows = [row for row in (doc.get("items") or []) if cint(row.get("custom_是否程序生成"))]

	if not generated_rows:
		frappe.throw(_("已填写套件物料解析，请先点击“解析套件”生成物料明细。"))

	def row_key(row):
		return (
			(row.get("temu_package_no") or row.get("custom_temu包裹号") or "").strip(),
			row.get("item_code") or "",
			flt(row.get("qty"), 6),
		)

	if Counter(row_key(row) for row in expected_rows) != Counter(row_key(row) for row in generated_rows):
		frappe.throw(_("套件配置、包裹号与已生成的物料明细不一致，请重新点击“解析套件”。"))


def validate_material_request_product_bundles(doc, method=None):
	"""保存或提交物料需求前，核对套件、包裹号与程序生成的明细。"""
	bundle_rows = doc.get("custom_物料套件移动") or []
	if not bundle_rows:
		return

	if doc.get("material_request_type") != "Material Transfer":
		frappe.throw(_("“物料套件移动”只能用于“物料转移”类型的物料需求。"))

	expected_rows = _expand_bundle_rows(bundle_rows, preserve_package=True)
	generated_rows = [row for row in (doc.get("items") or []) if cint(row.get("custom_是否程序生成"))]

	if not generated_rows:
		frappe.throw(_("已填写物料套件移动，请先点击“解析套件”生成物料明细。"))

	def expected_key(row):
		return (
			(row.get("temu_package_no") or "").strip(),
			row.get("item_code") or "",
			flt(row.get("qty"), 6),
		)

	def generated_key(row):
		return (
			(row.get("custom_temu包裹号") or "").strip(),
			row.get("item_code") or "",
			flt(row.get("qty"), 6),
		)

	if Counter(expected_key(row) for row in expected_rows) != Counter(
		generated_key(row) for row in generated_rows
	):
		frappe.throw(_("套件配置、包裹号与已生成的物料需求明细不一致，请重新点击“解析套件”。"))
