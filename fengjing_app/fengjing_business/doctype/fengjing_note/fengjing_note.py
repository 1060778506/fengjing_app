import json

import frappe
from frappe import _
from frappe.model.document import Document


class FengjingNote(Document):
	def validate(self):
		try:
			blocks = json.loads(self.blocks or "[]")
		except (TypeError, ValueError):
			frappe.throw(_("笔记正文格式不正确"))

		if not isinstance(blocks, list) or any(
			not isinstance(block, dict) or not isinstance(block.get("type"), str)
			for block in blocks
		):
			frappe.throw(_("笔记正文格式不正确"))
