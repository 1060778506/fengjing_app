# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate

from fengjing_app.fengjing_business.doctype.amazon_store_configuration.amazon_store_configuration import (
	_get_password,
	get_region,
	get_store,
)

from .amazon_advertising_api import ADS_ENDPOINTS
from .amazon_advertising_sync import discover_profiles, run_scheduled_sync


class AmazonAdvertisingMasterConfiguration(Document):
	def validate(self):
		self.configuration_name = str(self.configuration_name or "").strip()
		self.history_segment_days = min(max(cint(self.history_segment_days), 1), 31)
		self.routine_lookback_days = max(cint(self.routine_lookback_days), 1)
		self.sync_interval_hours = max(flt(self.sync_interval_hours), 1)
		self.retention_days = max(cint(self.retention_days), 0)
		if self.history_end_date and getdate(self.history_end_date) < getdate(self.history_start_date):
			frappe.throw(_("历史结束日期不能早于历史开始日期。"))
		if self.api_region not in ADS_ENDPOINTS:
			frappe.throw(_("请选择有效的Amazon Ads API区域。"))
		self.advertising_api_endpoint = ADS_ENDPOINTS[self.api_region]
		advertising_credentials = (
			str(self.advertising_client_id or "").strip(),
			_get_password(self, "advertising_client_secret", raise_exception=False),
			_get_password(self, "advertising_refresh_token", raise_exception=False),
		)
		if any(advertising_credentials) and not all(advertising_credentials):
			frappe.throw(_("广告专用Client ID、Client Secret和Refresh Token必须同时填写。"))
		if not self.credential_store:
			return
		store = get_store(self.credential_store, require_enabled=False)
		if get_region(store) != self.api_region:
			frappe.throw(_("请求凭证店铺与广告总配置必须属于同一个API区域。"))
		if cint(self.enabled) and not cint(store.enabled):
			frappe.throw(_("启用广告总配置前，请先启用请求凭证店铺。"))


@frappe.whitelist()
def discover_advertising_profiles(name):
	"""读取广告Profile，并按Marketplace自动建立或更新站点配置。"""
	frappe.only_for("System Manager")
	return discover_profiles(name)


def run_scheduled_advertising_sync():
	return run_scheduled_sync()
