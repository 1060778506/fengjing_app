"""Server operations for the business idea board."""

import frappe
from frappe import _


DOCTYPE = "Fengjing Business Idea"
STATUSES = ("收集箱", "待评估", "待做", "进行中", "已完成")
PRIORITIES = ("普通", "重要", "紧急")
FIELDS = ("title", "details", "status", "priority")


def _check(permission, doc=None):
	if doc:
		doc.check_permission(permission)
	elif not frappe.has_permission(DOCTYPE, permission):
		frappe.throw(_("没有操作经营蓝图的权限"), frappe.PermissionError)


def _normalize(data):
	if isinstance(data, str):
		data = frappe.parse_json(data)
	data = data or {}
	if not isinstance(data, dict):
		frappe.throw(_("卡片数据格式不正确"))
	result = {field: data.get(field) for field in FIELDS}
	result["title"] = str(result["title"] or "").strip()
	result["details"] = str(result["details"] or "").strip()
	result["status"] = result["status"] or STATUSES[0]
	result["priority"] = result["priority"] or PRIORITIES[0]
	if not result["title"]:
		frappe.throw(_("请填写想法标题"))
	if result["status"] not in STATUSES or result["priority"] not in PRIORITIES:
		frappe.throw(_("状态或优先级不正确"))
	return result


def _as_card(doc):
	return {
		"id": doc.name,
		"label": doc.title,
		"description": doc.details or "",
		"column": doc.status,
		"priority": doc.priority or "普通",
		"creation": str(doc.creation)[:16] if doc.creation else "",
		"modified": str(doc.modified)[:16] if doc.modified else "",
	}


@frappe.whitelist()
def get_cards():
	_check("read")
	return [
		_as_card(doc)
		for doc in frappe.get_list(
			DOCTYPE,
			fields=["name", *FIELDS, "sort_order", "creation", "modified"],
			order_by="sort_order asc, creation asc",
			limit_page_length=0,
		)
	]


@frappe.whitelist()
def save_card(data, name=None):
	values = _normalize(data)
	if name:
		doc = frappe.get_doc(DOCTYPE, name)
		_check("write", doc)
		# Column changes go through move_card so every affected order is saved.
		values.pop("status")
		doc.update(values)
		doc.save()
	else:
		_check("create")
		last = frappe.get_list(
			DOCTYPE,
			filters={"status": values["status"]},
			fields=["sort_order"],
			order_by="sort_order desc",
			limit_page_length=1,
		)
		order = (last[0].sort_order if last else 0) + 10
		doc = frappe.get_doc({"doctype": DOCTYPE, **values, "sort_order": order})
		doc.insert()
	return _as_card(doc)


@frappe.whitelist()
def move_card(name, column, before=None):
	if before in ("", "null", "None"):
		before = None
	if column not in STATUSES:
		frappe.throw(_("目标列不正确"))
	doc = frappe.get_doc(DOCTYPE, name)
	_check("write", doc)
	old_column = doc.status
	# Lock board rows while rebuilding order to avoid overlapping drags losing positions.
	frappe.db.sql(
		"SELECT name FROM `tabFengjing Business Idea` WHERE status IN %s FOR UPDATE",
		((old_column, column),),
	)
	def names_for(status):
		return [row.name for row in frappe.get_list(
			DOCTYPE,
			filters={"status": status},
			fields=["name"],
			order_by="sort_order asc, creation asc",
			limit_page_length=0,
		)]

	old_names = [item for item in names_for(old_column) if item != name]
	new_names = old_names if column == old_column else names_for(column)
	if before and before not in new_names:
		frappe.throw(_("目标卡片不在指定列中，请刷新后重试"))
	position = new_names.index(before) if before else len(new_names)
	new_names.insert(position, name)
	if old_column != column:
		doc.status = column
		doc.save()
	groups = ((column, new_names),) if old_column == column else ((old_column, old_names), (column, new_names))
	for status, names in groups:
		for index, item in enumerate(names, 1):
			frappe.db.set_value(DOCTYPE, item, "sort_order", index * 10, update_modified=False)
	return {"name": name, "column": column, "modified": str(doc.modified)[:16] if doc.modified else ""}


@frappe.whitelist()
def delete_card(name):
	doc = frappe.get_doc(DOCTYPE, name)
	_check("delete", doc)
	frappe.delete_doc(DOCTYPE, name)
	return {"name": name}
