# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class FengjingProductCorrespondingPlatformMainTable(Document):
	def validate(self):
		self.店铺 = str(self.店铺 or "").strip()
		self.站点id = str(self.站点id or "").strip().upper()
		self.平台asin = str(self.平台asin or "").strip().upper()
		self.平台sku = str(self.平台sku or "").strip()

		if not self.平台asin and not self.平台sku:
			frappe.throw(_("平台ASIN/商品ID和平台SKU至少填写一个。"))

		if self.店铺 and frappe.db.get_value("Cost Center", self.店铺, "is_group"):
			frappe.throw(_("店铺必须选择末级成本中心，不能选择成本中心组。"))

		基础条件 = {
			"店铺": self.店铺,
			"站点id": self.站点id,
			"name": ["!=", self.name],
		}

		if self.平台asin and self.平台sku:
			重复条件 = {
				**基础条件,
				"平台asin": self.平台asin,
				"平台sku": self.平台sku,
			}
			重复说明 = f"ASIN/商品ID {self.平台asin} 与 SKU {self.平台sku}"
		elif self.平台asin:
			重复条件 = {**基础条件, "平台asin": self.平台asin, "平台sku": ["in", ["", None]]}
			重复说明 = f"ASIN/商品ID {self.平台asin}"
		else:
			重复条件 = {**基础条件, "平台sku": self.平台sku}
			重复说明 = f"SKU {self.平台sku}"

		if frappe.db.exists(self.doctype, 重复条件):
			frappe.throw(_("当前店铺和站点下的{0}已经存在映射。").format(frappe.bold(重复说明)))
