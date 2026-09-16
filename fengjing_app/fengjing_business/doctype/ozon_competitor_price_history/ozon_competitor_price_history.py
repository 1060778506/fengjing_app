"""Append-only quote snapshots created by the authorized canvas import service."""
import frappe
from frappe.model.document import Document


class OzonCompetitorPriceHistory(Document):
    def validate(self):
        if not self.is_new() or not self.flags.canvas_quote_import:
            frappe.throw("报价历史只能通过运费画布导入，新版本不会覆盖已有记录")


def on_doctype_update():
    frappe.db.add_index("Ozon Competitor Price History", ["canvas", "store", "product_id", "version_number"], "ozfc_quote_lookup")
