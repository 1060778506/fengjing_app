"""Read one Temu workbook in memory and return SKU quantities without saving it."""

from collections import defaultdict
from io import BytesIO

import frappe
from frappe import _
from openpyxl import load_workbook

from fengjing_app.fengjing_business.page.temu_managed_bookkee.temu_financial_importer import (
	FINANCE_FILE_TYPES,
	PENDING_FILE_TYPES,
	_header,
	_iter_rows,
	_number,
	_require_header,
	_text,
)
from fengjing_app.fengjing_business.page.temu_managed_bookkee.temu_financial_analytics import MAPPING_DOCTYPE


PENDING_HEADERS = (
	"备货单号", "SKU ID", "SKU货号", "货品名称", "SKU属性", "销售数量",
	"单品券优惠金额", "店铺满减券优惠金额", "申报价格折扣金额",
	"预估待结算销售额", "币种",
)
TRANSACTION_HEADERS = (
	"销售订单", "售后订单", "备货单号", "备货单类型", "SKU ID", "SKU货号",
	"货品名称", "SKU属性", "数量", "单品券金额", "店铺满减券金额",
	"申报价格折扣金额", "交易类型", "金额", "币种", "账务时间",
)
AFTER_SALES_HEADERS = ("违规ID", "货品名称", "SKU", None, None, "赔付金额", "币种", "账务时间")
RESHIPMENT_HEADERS = ("订单编号", "SKU", None, None, None, "数量", "赔付金额")


def _attach_item_images(items):
	"""Use the existing Temu SKU mapping; leave ambiguous matches without an image."""
	skus = sorted({value for item in items.values() for value in (item["sku_id"], item["sku_code"]) if value})
	if not skus:
		return
	mappings = frappe.get_all(
		MAPPING_DOCTYPE,
		filters={"启用": 1, "店铺": ["like", "%temu%"], "平台sku": ["in", skus]},
		fields=["平台sku", "物料id"],
		limit_page_length=100000,
	)
	by_sku = defaultdict(set)
	for mapping in mappings:
		if mapping.get("物料id"):
			by_sku[str(mapping["平台sku"])].add(mapping["物料id"])
	item_codes = sorted({code for codes in by_sku.values() for code in codes})
	images = {
		row.name: row.image or ""
		for row in frappe.get_all("Item", filters={"name": ["in", item_codes]}, fields=["name", "image"], limit_page_length=100000)
	} if item_codes else {}
	for item in items.values():
		matches = by_sku.get(item["sku_code"], set()) | by_sku.get(item["sku_id"], set())
		if len(matches) == 1:
			item["item_code"] = next(iter(matches))
			item["item_image"] = images.get(item["item_code"], "")


@frappe.whitelist(methods=["POST"])
def analyze_file(file_type=None):
	"""Parse a single posted XLSX stream; never create a File or batch record."""
	if file_type not in PENDING_FILE_TYPES | FINANCE_FILE_TYPES:
		frappe.throw(_("只支持六类 Temu 待处理和财务文件。"))
	upload = frappe.request.files.get("file")
	if not upload or not (upload.filename or "").lower().endswith(".xlsx"):
		frappe.throw(_("请选择 XLSX 文件。"))
	try:
		workbook = load_workbook(BytesIO(upload.read()), read_only=True, data_only=True)
	except Exception:
		frappe.throw(_("无法读取 XLSX 文件，请检查文件是否损坏。"))

	items = defaultdict(lambda: {"sku_id": "", "sku_code": "", "product_name": "", "quantity": 0.0, "reship_quantity": 0.0, "item_code": "", "item_image": ""})
	row_count = 0
	try:
		for sheet in workbook.worksheets:
			if sheet.max_row == 1 and sheet.max_column == 1 and not any(_header(sheet)):
				continue
			if file_type in PENDING_FILE_TYPES:
				if sheet.title != "交易结算":
					frappe.throw(_("待处理文件包含无法识别的工作表：{0}").format(sheet.title))
				_require_header(sheet, PENDING_HEADERS)
				start_row, sku_id_col, sku_code_col, product_col, qty_col = 2, 1, 2, 3, 5
				is_reship = False
			elif sheet.title == "交易结算":
				_require_header(sheet, TRANSACTION_HEADERS)
				start_row, sku_id_col, sku_code_col, product_col, qty_col = 2, 4, 5, 6, 8
				is_reship = False
			elif sheet.title == "消费者及履约保障-售后问题":
				_require_header(sheet, AFTER_SALES_HEADERS)
				continue  # This sheet has compensation amounts but no quantity.
			elif sheet.title == "消费者及履约保障-售后补寄":
				_require_header(sheet, RESHIPMENT_HEADERS)
				start_row, sku_id_col, sku_code_col, product_col, qty_col = 3, 1, 3, 2, 5
				is_reship = True
			else:
				frappe.throw(_("财务文件包含无法识别的工作表：{0}").format(sheet.title))

			for row_number, values in _iter_rows(sheet, start_row):
				sku_id = _text(values[sku_id_col]) or ""
				sku_code = _text(values[sku_code_col]) or ""
				if not sku_id and not sku_code:
					frappe.throw(_("工作表 {0} 第 {1} 行缺少 SKU。").format(sheet.title, row_number))
				quantity = _number(values[qty_col]) or 0.0
				key = sku_id or sku_code
				item = items[key]
				item["sku_id"] = item["sku_id"] or sku_id
				item["sku_code"] = item["sku_code"] or sku_code
				item["product_name"] = item["product_name"] or (_text(values[product_col]) or "")
				item["reship_quantity" if is_reship else "quantity"] += quantity
				row_count += 1
	finally:
		workbook.close()
	_attach_item_images(items)

	return {
		"file_type": file_type,
		"file_name": upload.filename,
		"row_count": row_count,
		"quantity": round(sum(item["quantity"] for item in items.values()), 6),
		"reship_quantity": round(sum(item["reship_quantity"] for item in items.values()), 6),
		"items": [
			{**item, "quantity": round(item["quantity"], 6), "reship_quantity": round(item["reship_quantity"], 6)}
			for item in items.values()
		],
	}
