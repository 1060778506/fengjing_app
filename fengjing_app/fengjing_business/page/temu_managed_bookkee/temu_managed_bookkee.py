from pathlib import Path

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime


BATCH_DOCTYPE = "Temu Financial Reconciliation Batch"
CHILD_FIELD = "files"


def _as_list(value):
	if isinstance(value, str):
		value = frappe.parse_json(value)
	return value or []


def _date_text(value):
	return str(value) if value else None


def _check_date_range(start_date, end_date):
	if start_date and end_date and get_datetime(start_date) > get_datetime(end_date):
		frappe.throw(_("结束日期不能早于开始日期。"))


def _day_boundary(value, end_of_day=False):
	if not value:
		return None
	datetime_value = get_datetime(value)
	if end_of_day:
		return datetime_value.replace(hour=23, minute=59, second=59, microsecond=0)
	return datetime_value.replace(hour=0, minute=0, second=0, microsecond=0)


def _get_batch_doc(name, permission_type="read"):
	doc = frappe.get_doc(BATCH_DOCTYPE, name)
	doc.check_permission(permission_type)
	return doc


def _sync_summary(doc):
	doc.file_count = len(doc.get(CHILD_FIELD) or [])
	doc.status = "文件已保存" if doc.file_count else "草稿"


def _file_payload(row):
	return {
		"name": row.name,
		"file_type": row.file_type,
		"file_url": row.file,
		"original_file_name": row.original_file_name,
		"file_size": cint(row.file_size),
		"file_hash": row.file_hash,
		"sort_order": cint(row.sort_order),
		"uploaded_at": row.uploaded_at,
		"uploaded_by": row.uploaded_by,
		"notes": row.notes,
	}


def _batch_payload(doc):
	return {
		"name": doc.name,
		"start_date": _date_text(doc.start_date),
		"end_date": _date_text(doc.end_date),
		"status": doc.status or "草稿",
		"file_count": cint(doc.file_count),
		"remarks": doc.remarks or "",
		"creation": doc.creation,
		"modified": doc.modified,
		"owner": doc.owner,
		"can_write": bool(doc.has_permission("write")),
		"can_delete": bool(doc.has_permission("delete")),
		"files": [
			_file_payload(row)
			for row in sorted(doc.get(CHILD_FIELD) or [], key=lambda row: cint(row.sort_order))
		],
	}


def _delete_attached_file(batch_name, file_url, exclude_file_name=None):
	if not file_url:
		return
	file_names = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": BATCH_DOCTYPE,
			"attached_to_name": batch_name,
			"file_url": file_url,
		},
		pluck="name",
	)
	for file_name in file_names:
		if file_name == exclude_file_name:
			continue
		if frappe.db.exists("File", file_name):
			frappe.delete_doc("File", file_name, ignore_permissions=True)


@frappe.whitelist()
def get_batches():
	frappe.has_permission(BATCH_DOCTYPE, "read", throw=True)
	return frappe.get_list(
		BATCH_DOCTYPE,
		fields=[
			"name",
			"start_date",
			"end_date",
			"status",
			"file_count",
			"remarks",
			"creation",
			"modified",
			"owner",
		],
		order_by="modified desc",
		limit_page_length=200,
	)


@frappe.whitelist()
def get_batch(name):
	return _batch_payload(_get_batch_doc(name, "read"))


@frappe.whitelist()
def save_batch(name=None, start_date=None, end_date=None, remarks=None, file_metadata=None):
	start_date = _day_boundary(start_date)
	end_date = _day_boundary(end_date, end_of_day=True)
	_check_date_range(start_date, end_date)
	if name:
		doc = _get_batch_doc(name, "write")
	else:
		frappe.has_permission(BATCH_DOCTYPE, "create", throw=True)
		doc = frappe.new_doc(BATCH_DOCTYPE)

	doc.start_date = start_date or None
	doc.end_date = end_date or None
	doc.remarks = remarks or ""
	rows_by_name = {row.name: row for row in doc.get(CHILD_FIELD) or []}
	for values in _as_list(file_metadata):
		row = rows_by_name.get(values.get("name"))
		if not row:
			continue
		row.file_type = (values.get("file_type") or row.file_type or "").strip()
		row.sort_order = cint(values.get("sort_order")) or row.idx
		row.notes = values.get("notes") or ""

	_sync_summary(doc)
	if doc.is_new():
		doc.insert()
	else:
		doc.save()
	return _batch_payload(doc)


@frappe.whitelist()
def register_file(batch_name, file_doc_name, row_name=None, file_type=None, sort_order=0, notes=None):
	doc = _get_batch_doc(batch_name, "write")
	file_doc = frappe.get_doc("File", file_doc_name)
	if file_doc.attached_to_doctype != BATCH_DOCTYPE or file_doc.attached_to_name != doc.name:
		frappe.throw(_("上传文件与当前对账批次不匹配。"), frappe.PermissionError)
	if not (file_doc.file_name or "").lower().endswith(".xlsx"):
		frappe.throw(_("这里只允许保存 XLSX 文件。"))
	if not cint(file_doc.is_private):
		frappe.throw(_("对账文件必须使用私有文件存储。"))

	row = None
	if row_name:
		row = next((item for item in doc.get(CHILD_FIELD) or [] if item.name == row_name), None)
		if not row:
			frappe.throw(_("需要替换的文件记录不存在。"))
	old_file_url = row.file if row else None
	if not row:
		row = doc.append(CHILD_FIELD, {})

	row.file_type = (file_type or Path(file_doc.file_name).stem or _("未命名文件")).strip()
	row.file = file_doc.file_url
	row.original_file_name = file_doc.file_name
	row.file_size = cint(file_doc.file_size)
	row.file_hash = file_doc.content_hash
	row.sort_order = cint(sort_order) or len(doc.get(CHILD_FIELD) or [])
	row.uploaded_at = file_doc.creation or now_datetime()
	row.uploaded_by = file_doc.owner or frappe.session.user
	row.notes = notes or ""

	_sync_summary(doc)
	doc.save()
	if old_file_url:
		_delete_attached_file(
			doc.name,
			old_file_url,
			exclude_file_name=file_doc.name,
		)
	return _batch_payload(doc)


@frappe.whitelist()
def delete_batch_file(batch_name, row_name):
	doc = _get_batch_doc(batch_name, "write")
	row = next((item for item in doc.get(CHILD_FIELD) or [] if item.name == row_name), None)
	if not row:
		frappe.throw(_("文件记录不存在或已经被删除。"))
	file_url = row.file
	doc.remove(row)
	_sync_summary(doc)
	doc.save()
	_delete_attached_file(doc.name, file_url)
	return _batch_payload(doc)


@frappe.whitelist()
def delete_batch(name):
	doc = _get_batch_doc(name, "delete")
	file_names = frappe.get_all(
		"File",
		filters={"attached_to_doctype": BATCH_DOCTYPE, "attached_to_name": doc.name},
		pluck="name",
	)
	frappe.delete_doc(BATCH_DOCTYPE, doc.name)
	for file_name in file_names:
		if frappe.db.exists("File", file_name):
			frappe.delete_doc("File", file_name, ignore_permissions=True)
	return {"name": name}
