import frappe


def execute():
	legacy_doctype = "Ozon Store API Sub-table"
	if frappe.db.exists("DocType", legacy_doctype):
		frappe.delete_doc(
			"DocType",
			legacy_doctype,
			force=True,
			ignore_permissions=True,
		)

	# Frappe 删除孤立标准 DocType 时会有意保留物理表；本迁移明确清除旧子表数据。
	frappe.db.sql_ddl("DROP TABLE IF EXISTS `tabOzon Store API Sub-table`")
