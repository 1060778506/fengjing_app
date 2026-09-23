# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

from collections import Counter

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class TemuMaterialmovement(Document):
	pass


def _as_rows(value):
	"""兼容前端传入的 JSON 字符串和已解析列表。"""
	if isinstance(value, str):
		value = frappe.parse_json(value)
	return value or []


def _expand_bundle_rows(bundle_rows):
	"""将 TEMU 包裹子表展开为真实库存物料。"""
	result = []
	seen_packages = set()
	bundle_cache = {}
	item_cache = {}

	for index, source in enumerate(_as_rows(bundle_rows), start=1):
		package_no = (source.get("temu包裹号") or "").strip()
		bundle_name = (source.get("套件") or "").strip()
		bundle_qty = flt(source.get("数量"))

		if not package_no:
			frappe.throw(_(f"TEMU 物料套件移动第 {index} 行：请填写 TEMU 包裹号。"))
		if package_no in seen_packages:
			frappe.throw(_(f"TEMU 包裹号 {package_no} 在套件子表中重复，一个包裹号只能对应一行套件。"))
		seen_packages.add(package_no)

		if not bundle_name:
			frappe.throw(_(f"TEMU 物料套件移动第 {index} 行：请选择物料套件。"))
		if bundle_qty <= 0:
			frappe.throw(_(f"TEMU 物料套件移动第 {index} 行：数量必须大于 0。"))

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

			result.append(
				{
					"temu_package_no": package_no,
					"bundle": bundle_name,
					"bundle_description": bundle.description or "",
					"item_code": item_code,
					"item_name": item.item_name or "",
					"stock_uom": item.stock_uom,
					"qty": quantity,
				}
			)

	return result


def _check_stock_entry_permission():
	if not (
		frappe.has_permission("Stock Entry", ptype="create")
		or frappe.has_permission("Stock Entry", ptype="write")
	):
		frappe.throw(_("您没有解析物料移动套件的权限。"), frappe.PermissionError)


@frappe.whitelist()
def parse_temu_product_bundles(bundle_rows):
	"""供物料移动的“解析套件”按钮调用。"""
	_check_stock_entry_permission()
	rows = _expand_bundle_rows(bundle_rows)
	return {
		"rows": rows,
		"package_count": len({row["temu_package_no"] for row in rows}),
		"item_row_count": len(rows),
	}


def validate_temu_product_bundle_movement(doc, method=None):
	"""
	保存或提交前的服务端校验。

	既防止绕过前端，也能发现解析后又修改了套件、数量或包裹号的情况。
	"""
	bundle_rows = doc.get("custom_temu_物料套件移动") or []
	if not bundle_rows:
		return

	if doc.get("purpose") and doc.purpose != "Material Transfer":
		frappe.throw(_("“TEMU 物料套件移动”只能用于“物料转移”类型的库存凭证。"))

	# 套件名称当前是 Link 字段，保存有效的 Product Bundle 编号，
	# 避免 fetch_from 带入描述文本后造成无效链接。
	for source in bundle_rows:
		if source.get("套件"):
			source.set("套件名称", source.get("套件"))

	expected_rows = _expand_bundle_rows(bundle_rows)
	generated_rows = [row for row in (doc.get("items") or []) if cint(row.get("custom_是否程序生成"))]

	if not generated_rows:
		frappe.throw(_("已填写 TEMU 物料套件移动，请先点击“解析套件”生成物料明细。"))

	def expected_key(row):
		return (row["temu_package_no"], row["item_code"], flt(row["qty"], 6))

	def actual_key(row):
		return (
			(row.get("custom_temu包裹号") or "").strip(),
			row.get("item_code") or "",
			flt(row.get("qty"), 6),
		)

	if Counter(expected_key(row) for row in expected_rows) != Counter(actual_key(row) for row in generated_rows):
		frappe.throw(_("TEMU 套件配置与已生成的物料明细不一致，请重新点击“解析套件”。"))
