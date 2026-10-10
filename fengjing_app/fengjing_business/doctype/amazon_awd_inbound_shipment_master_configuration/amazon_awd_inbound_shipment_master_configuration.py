# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_datetime


class AmazonAWDInboundShipmentMasterConfiguration(Document):
	def validate(self):
		self.history_segment_days = max(cint(self.history_segment_days), 1)
		self.record_retention_days = max(cint(self.record_retention_days), 0)
		self.sync_interval_minutes = max(cint(self.sync_interval_minutes), 5)
		self.incremental_lookback_hours = max(cint(self.incremental_lookback_hours), 1)
		for days in (7, 14, 30, 90, 180):
			self.set(f"recheck_{days}_interval_days", max(cint(self.get(f"recheck_{days}_interval_days")), 1))
		if self.history_start_datetime and self.history_end_datetime:
			if get_datetime(self.history_start_datetime) >= get_datetime(self.history_end_datetime):
				frappe.throw(_("历史开始时间必须早于历史结束时间。"))
