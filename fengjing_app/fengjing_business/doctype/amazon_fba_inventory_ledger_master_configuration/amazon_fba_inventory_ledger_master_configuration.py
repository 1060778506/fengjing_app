# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import calendar

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, cint, getdate, now_datetime


TIME_AGGREGATIONS = {"DAILY", "WEEKLY", "MONTHLY"}
LOCATION_AGGREGATIONS = {"COUNTRY", "FC"}
RECHECK_DAYS = (7, 14, 30, 90, 180)


class AmazonFBAInventoryLedgerMasterConfiguration(Document):
	def validate(self):
		self.configuration_name = str(self.configuration_name or "").strip()
		self.history_segment_days = min(max(cint(self.history_segment_days), 1), 366)
		self.snapshot_retention_days = max(cint(self.snapshot_retention_days), 0)
		self.sync_interval_hours = max(cint(self.sync_interval_hours), 1)
		self.routine_lookback_days = min(max(cint(self.routine_lookback_days), 1), 366)
		for days in RECHECK_DAYS:
			fieldname = f"recheck_{days}_interval_days"
			self.set(fieldname, max(cint(self.get(fieldname)), 1))

		self.summary_time_aggregation = str(self.summary_time_aggregation or "DAILY").upper()
		self.summary_location_aggregation = str(
			self.summary_location_aggregation or "COUNTRY"
		).upper()
		if self.summary_time_aggregation not in TIME_AGGREGATIONS:
			frappe.throw(_("汇总时间粒度必须是每日、每周或每月。"))
		if self.summary_location_aggregation not in LOCATION_AGGREGATIONS:
			frappe.throw(_("汇总位置粒度必须是国家或配送中心。"))
		if cint(self.enabled) and not cint(self.summary_enabled) and not cint(self.detail_enabled):
			frappe.throw(_("请至少启用一种库存分类账报告。"))

		if not self.history_start_date or not self.history_end_date:
			if cint(self.enabled):
				frappe.throw(_("启用总配置前，请填写历史开始日期和历史结束日期。"))
			return

		start_date = getdate(self.history_start_date)
		end_date = getdate(self.history_end_date)
		if end_date < start_date:
			frappe.throw(_("历史结束日期不能早于历史开始日期。"))
		if cint(self.detail_enabled) and start_date < getdate(add_months(getdate(now_datetime()), -18)):
			frappe.throw(_("Amazon 库存分类账明细最多只能查询最近18个月。"))
		if cint(self.summary_enabled) and self.summary_time_aggregation == "WEEKLY":
			if start_date.weekday() != 0 or end_date.weekday() != 6:
				frappe.throw(_("按周汇总时，开始日期必须是周一，结束日期必须是周日。"))
		if cint(self.summary_enabled) and self.summary_time_aggregation == "MONTHLY":
			last_day = calendar.monthrange(end_date.year, end_date.month)[1]
			if start_date.day != 1 or end_date.day != last_day:
				frappe.throw(_("按月汇总时，开始日期必须是月初，结束日期必须是月末。"))
