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


DOCTYPE = "Amazon FBA Inbound Shipment Country Configuration"
MASTER_DOCTYPE = "Amazon FBA Inbound Shipment Master Configuration"


class AmazonFBAInboundShipmentCountryConfiguration(Document):
	def validate(self):
		store = get_store(self.amazon_store, require_enabled=False) if self.amazon_store else None
		if store:
			self.company = store.company
			self.cost_center = store.cost_center
			self.country = store.country
			self.seller_id = store.seller_id
			self.marketplace_id = store.marketplace_id
			self.api_region = get_region(store)
			if cint(self.enabled) and not cint(store.enabled):
				frappe.throw(_("启用国家配置前，请先启用关联的Amazon店铺。"))
		if cint(self.enabled) and not self.master_configuration:
			frappe.throw(_("启用国家配置前，请先选择入库货件总配置。"))
		if self.master_configuration:
			master = frappe.get_doc(MASTER_DOCTYPE, self.master_configuration)
			if cint(self.enabled) and not cint(master.enabled):
				frappe.throw(_("当前入库货件总配置尚未启用。"))
			if store and str(master.api_region or "") != get_region(store):
				frappe.throw(_("国家配置的Amazon API区域与入库货件总配置不一致。"))
			if store:
				other_stores = frappe.get_all(
					DOCTYPE,
					filters={
						"master_configuration": self.master_configuration,
						"enabled": 1,
						"name": ["!=", self.name or ""],
					},
					pluck="amazon_store",
				)
				seller_ids = {
					str(get_store(name, require_enabled=False).seller_id or "").strip().upper()
					for name in other_stores if name
				}
				seller_ids.add(str(store.seller_id or "").strip().upper())
				if "" in seller_ids or len(seller_ids) != 1:
					frappe.throw(_("同一个入库货件总配置只能关联同一个Amazon卖家的店铺。"))
		if not self.is_new() and self.has_value_changed("master_configuration"):
			self._reset_runtime()

	def _reset_runtime(self):
		self.history_status = "Not Started"
		self.history_checkpoint = None
		self.history_completed = 0
		self.history_last_error = ""
		self.incremental_last_at = None
		self.incremental_next_at = None
		self.incremental_checkpoint = None
		self.incremental_summary = ""
		self.current_status = "Not Started"
		self.current_execution_type = ""
		self.started_at = None
		self.completed_at = None
		self.last_success_at = None
		self.last_sync_result = ""
		self.last_error = ""
		for days in (7, 14, 30, 90, 180):
			self.set(f"recheck_{days}_last_at", None)
			self.set(f"recheck_{days}_next_at", None)


@frappe.whitelist()
def start_history_sync(name):
	from .amazon_fba_inbound_sync import start_country_sync

	return start_country_sync(name, "history")


@frappe.whitelist()
def start_incremental_sync(name):
	from .amazon_fba_inbound_sync import start_country_sync

	return start_country_sync(name, "incremental")


@frappe.whitelist()
def start_recheck_sync(name, days):
	from .amazon_fba_inbound_sync import start_country_sync

	return start_country_sync(name, "recheck", days=days)


def run_scheduled_inbound_shipment_sync():
	from .amazon_fba_inbound_sync import run_scheduled_inbound_sync

	return run_scheduled_inbound_sync()
