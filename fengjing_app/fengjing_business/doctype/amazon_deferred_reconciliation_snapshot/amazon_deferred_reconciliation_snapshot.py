import frappe
from frappe.model.document import Document


class AmazonDeferredReconciliationSnapshot(Document):
	def validate(self):
		if not self.is_new() and frappe.db.get_value(self.doctype, self.name, "snapshot_status") == "已完成":
			frappe.throw("已完成的递延对账快照不可修改；请重新抓取生成新快照。")

	def on_trash(self):
		if self.snapshot_status in {"等待中", "抓取中"} or any(
			self.get(field) in {"等待中", "运行中"}
			for field in ("balance_status", "transactions_status", "orders_status")
		):
			frappe.throw("抓取中的快照暂不能删除，请等待任务结束。")
