import frappe
from frappe.utils.password import Auth, get_decrypted_password, remove_encrypted_password


def execute():
	doctype = "TikTok Order Configuration"
	fields = ("app_secret", "auth_code")
	entries = (
		frappe.qb.from_(Auth)
		.select(Auth.name, Auth.fieldname)
		.where((Auth.doctype == doctype) & Auth.fieldname.isin(fields) & (Auth.encrypted == 1))
		.run(as_dict=True)
	)
	if not entries:
		return

	meta = frappe.get_meta(doctype, cached=False)
	if any(meta.get_field(field).fieldtype != "Small Text" for field in fields):
		frappe.throw("TikTok 凭证必须先切换为文本字段。")

	for entry in entries:
		field = entry.fieldname
		current = frappe.db.get_value(doctype, entry.name, field, for_update=True)
		value = get_decrypted_password(doctype, entry.name, field)
		if current and current.strip("*") and current != value:
			frappe.throw("TikTok 配置已有不同的文本凭证，迁移已停止。")
		frappe.db.set_value(doctype, entry.name, field, value, update_modified=False)
		if frappe.db.get_value(doctype, entry.name, field) != value:
			frappe.throw("TikTok 凭证迁移校验失败。")
		remove_encrypted_password(doctype, entry.name, field)
