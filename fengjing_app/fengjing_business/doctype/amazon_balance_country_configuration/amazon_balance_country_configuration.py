# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from fengjing_app.fengjing_business.doctype.amazon_store_configuration.amazon_store_configuration import (
	get_region,
	get_store,
)


class AmazonBalanceCountryConfiguration(Document):
	def validate(self):
		store = get_store(self.amazon_store, require_enabled=False) if self.amazon_store else None
		if store:
			self.company = store.company
			self.cost_center = store.cost_center
			self.country = store.country
			self.seller_id = str(store.seller_id or "").strip().upper()
			self.marketplace_id = str(store.marketplace_id or "").strip().upper()
			self.api_region = get_region(store)
			if cint(self.enabled) and not cint(store.enabled):
				frappe.throw(_("启用余额国家配置前，请先启用关联的亚马逊店铺。"))
		if cint(self.enabled) and not self.master_configuration:
			frappe.throw(_("启用余额国家配置前，请先选择余额总配置。"))
		if not self.master_configuration or not store:
			return

		master = frappe.get_doc("Amazon Balance Master Configuration", self.master_configuration)
		if cint(self.enabled) and not cint(master.enabled):
			frappe.throw(_("当前余额总配置尚未启用。"))
		credential_store = get_store(master.credential_store, require_enabled=False)
		if str(credential_store.seller_id or "").strip().upper() != self.seller_id:
			frappe.throw(_("余额国家配置与总配置必须使用同一个Amazon卖家编号。"))
		if get_region(credential_store) != self.api_region:
			frappe.throw(_("余额国家配置与总配置必须属于同一个SP-API区域。"))
		if not self.is_new() and self.has_value_changed("master_configuration"):
			self.history_status = "未开始"
			self.history_checkpoint = None
			self.history_completed = 0
			self.history_summary = None
			self.history_last_error = None
