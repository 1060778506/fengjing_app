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

from fengjing_app.fengjing_business.doctype.amazon_advertising_master_configuration.amazon_advertising_sync import (
	start_site_sync,
	test_site_access,
)


class AmazonAdvertisingSiteConfiguration(Document):
	def validate(self):
		self.configuration_name = str(self.configuration_name or "").strip()
		self.profile_id = str(self.profile_id or "").strip()
		self.advertiser_id = str(self.advertiser_id or "").strip()
		if not self.amazon_store:
			return
		store = get_store(self.amazon_store, require_enabled=False)
		self.company = store.company
		self.cost_center = store.cost_center
		self.seller_id = str(store.seller_id or "").strip().upper()
		self.marketplace_id = str(store.marketplace_id or "").strip().upper()
		self.country = store.country
		if cint(self.enabled) and not cint(store.enabled):
			frappe.throw(_("启用广告站点配置前，请先启用关联的亚马逊店铺。"))
		if not self.master_configuration:
			return
		master = frappe.get_doc("Amazon Advertising Master Configuration", self.master_configuration)
		if cint(self.enabled) and not cint(master.enabled):
			frappe.throw(_("当前广告总配置尚未启用。"))
		credential_store = get_store(master.credential_store, require_enabled=False)
		if str(credential_store.seller_id or "").strip().upper() != self.seller_id:
			frappe.throw(_("广告站点配置与总配置必须使用同一个Amazon卖家编号。"))
		if get_region(store) != master.api_region:
			frappe.throw(_("广告站点配置与总配置必须属于同一个API区域。"))


@frappe.whitelist()
def test_advertising_access(name):
	frappe.only_for("System Manager")
	profile = test_site_access(name)
	return {"message": _("广告Profile连接成功。"), "profile": profile}


@frappe.whitelist()
def start_structure_sync(name):
	frappe.only_for("System Manager")
	return start_site_sync(name, "structure")


@frappe.whitelist()
def start_latest_sync(name):
	frappe.only_for("System Manager")
	return start_site_sync(name, "latest")


@frappe.whitelist()
def start_history_sync(name):
	frappe.only_for("System Manager")
	return start_site_sync(name, "history")


@frappe.whitelist()
def start_recheck_sync(name, days):
	frappe.only_for("System Manager")
	days = cint(days)
	if days not in {7, 14, 30, 60, 90}:
		frappe.throw(_("不支持的广告核对周期。"))
	return start_site_sync(name, "recheck", days)
