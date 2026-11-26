import frappe


def execute():
	detail_doctype = "Fengjin Team Calendar Event Detail"
	detail = frappe.qb.DocType(detail_doctype)
	item = frappe.qb.DocType("Item")
	rows = (
		frappe.qb.from_(detail)
		.join(item).on(item.name == detail.item_code)
		.select(detail.name, item.item_name)
		.where(detail.item_name.isnull() | (detail.item_name == ""))
		.run(as_dict=True)
	)
	for row in rows:
		frappe.db.set_value(detail_doctype, row.name, "item_name", row.item_name, update_modified=False)
