# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_datetime


API_REGIONS = {"North America", "Europe", "Far East"}
RECHECK_DAYS = (7, 14, 30, 90, 180)


class AmazonFBAInboundShipmentMasterConfiguration(Document):
	def validate(self):
		self.configuration_name = str(self.configuration_name or "").strip()
		self.api_region = str(self.api_region or "").strip()
		if self.api_region not in API_REGIONS:
			frappe.throw(_("API区域必须是北美、欧洲或远东。"))

		self.history_segment_days = min(max(cint(self.history_segment_days), 1), 366)
		self.record_retention_days = max(cint(self.record_retention_days), 0)
		self.sync_interval_minutes = max(cint(self.sync_interval_minutes), 5)
		self.incremental_lookback_hours = min(max(cint(self.incremental_lookback_hours), 1), 24 * 180)
		for days in RECHECK_DAYS:
			fieldname = f"recheck_{days}_interval_days"
			self.set(fieldname, max(cint(self.get(fieldname)), 1))

		if not self.history_start_datetime or not self.history_end_datetime:
			if cint(self.enabled):
				frappe.throw(_("启用入库货件总配置前，请填写历史开始时间和历史结束时间。"))
			return
		start = get_datetime(self.history_start_datetime)
		end = get_datetime(self.history_end_datetime)
		if end < start:
			frappe.throw(_("历史结束时间不能早于历史开始时间。"))
