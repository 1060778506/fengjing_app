import frappe
from frappe.query_builder import Criterion


CHILD_DOCTYPE = "Fengjin Team Calendar Event Detail"
PARENT_LINK = {"parenttype": "Event", "parentfield": "custom_calendar_event_details"}
LEGACY_FIELDS = ("custom_店铺", "custom_物料")


def execute():
	legacy_fields = frappe.get_all(
		"Custom Field",
		filters={"dt": "Event", "fieldname": ["in", LEGACY_FIELDS]},
		fields=["name", "fieldname"],
	)
	if not legacy_fields:
		return

	columns = set(frappe.db.get_table_columns("Event"))
	if any(field.fieldname not in columns for field in legacy_fields):
		frappe.throw("日程旧字段的存储列缺失，无法迁移明细。")

	event = frappe.qb.DocType("Event")
	source_fields = [event[field] for field in LEGACY_FIELDS if field in columns]
	events = (
		frappe.qb.from_(event)
		.select(event.name, *source_fields)
		.where(Criterion.any([field != "" for field in source_fields]))
		.orderby(event.name)
		.for_update()
		.run(as_dict=True)
	)
	for old_event in events:
		store, item_code = (old_event.get(field) or "" for field in LEGACY_FIELDS)
		filters = {**PARENT_LINK, "parent": old_event.name}
		rows = frappe.get_all(CHILD_DOCTYPE, filters=filters, fields=["idx", "store", "item_code"])
		if any((row.store or "", row.item_code or "") == (store, item_code) for row in rows):
			continue
		frappe.get_doc({
			"doctype": CHILD_DOCTYPE,
			**filters,
			"idx": max((row.idx for row in rows), default=0) + 1,
			"store": store or None,
			"item_code": item_code or None,
		}).db_insert()

	# Verify all legacy values are present before removing either field.
	for old_event in events:
		store, item_code = (old_event.get(field) or "" for field in LEGACY_FIELDS)
		rows = frappe.get_all(
			CHILD_DOCTYPE, filters={**PARENT_LINK, "parent": old_event.name}, fields=["store", "item_code"]
		)
		if not any((row.store or "", row.item_code or "") == (store, item_code) for row in rows):
			frappe.throw(f"日程 {old_event.name} 的明细迁移校验失败。")

	for field in legacy_fields:
		frappe.delete_doc("Custom Field", field.name, ignore_permissions=True)
