import json
import re
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime
from openpyxl import load_workbook

from fengjing_app.fengjing_business.page.temu_managed_bookkee.temu_financial_importer import (
	FREIGHT_PAYMENT_FILE_TYPE,
	FULFILLMENT_FILE_TYPE,
	SHIPMENT_LIST_FILE_TYPE,
	_text,
	fulfillment_keys_from_payload,
	validate_freight_payment_workbook,
	validate_fulfillment_workbook,
	validate_shipment_list_workbook,
)


BATCH_DOCTYPE = "Temu Financial Reconciliation Batch"
CHILD_FIELD = "files"
CHILD_DOCTYPE = "Temu Financial Ledger Detail Report"
SNAPSHOT_DOCTYPE = "Temu Financial Source JSON Snapshot"


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
		"display_name": row.display_name,
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
		"cost_center": doc.cost_center or "",
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


def _latest_fulfillment_source(cost_center):
	"""Select by file upload time, since a later batch sync rewrites snapshot timestamps."""
	batches = frappe.get_list(
		BATCH_DOCTYPE,
		filters={"cost_center": cost_center},
		fields=["name"],
		limit_page_length=10000,
	)
	if not batches:
		return None
	file_rows = frappe.get_all(
		CHILD_DOCTYPE,
		filters={
			"parent": ["in", [row.name for row in batches]],
			"parenttype": BATCH_DOCTYPE,
			"file_type": FULFILLMENT_FILE_TYPE,
		},
		fields=["name", "parent", "file_hash", "original_file_name", "uploaded_at"],
		order_by="uploaded_at desc, creation desc",
		limit_page_length=1,
	)
	if not file_rows:
		return None
	file_row = file_rows[0]
	snapshot = frappe.db.get_value(
		SNAPSHOT_DOCTYPE,
		{
			"reconciliation_batch": file_row.parent,
			"source_file_record": file_row.name,
			"source_file_type": FULFILLMENT_FILE_TYPE,
		},
		["source_file_hash", "workbook_json"],
		as_dict=True,
	)
	return {"file": file_row, "snapshot": snapshot}


def _check_fulfillment_file(doc, file_doc):
	if file_doc.attached_to_doctype != BATCH_DOCTYPE or file_doc.attached_to_name != doc.name:
		frappe.throw(_("上传文件与当前对账批次不匹配。"), frappe.PermissionError)
	if not (file_doc.file_name or "").lower().endswith(".xlsx") or not cint(file_doc.is_private):
		frappe.throw(_("履约服务账单必须是私有的 XLSX 文件。"))
	try:
		workbook = load_workbook(Path(file_doc.get_full_path()), read_only=True, data_only=True)
	except Exception:
		frappe.throw(_("无法读取履约服务账单 XLSX 文件：{0}").format(file_doc.file_name))
	try:
		incoming = validate_fulfillment_workbook(workbook)
	finally:
		workbook.close()
	previous = _latest_fulfillment_source(doc.cost_center)
	old_keys = Counter()
	if previous and previous["snapshot"]:
		old_payload = json.loads(previous["snapshot"].workbook_json)
		old_keys = fulfillment_keys_from_payload(old_payload)
	return {
		"incoming_rows": incoming["row_count"],
		"previous_rows": sum(old_keys.values()),
		"new_rows": sum((incoming["keys"] - old_keys).values()),
		"missing_rows": sum((old_keys - incoming["keys"]).values()),
		"previous_file": previous["file"].original_file_name if previous else None,
	}


@frappe.whitelist()
def preview_fulfillment_upload(batch_name, file_doc_name):
	doc = _get_batch_doc(batch_name, "write")
	return _check_fulfillment_file(doc, frappe.get_doc("File", file_doc_name))


@frappe.whitelist()
def discard_unregistered_file(batch_name, file_doc_name):
	doc = _get_batch_doc(batch_name, "write")
	file_doc = frappe.get_doc("File", file_doc_name)
	if file_doc.attached_to_doctype != BATCH_DOCTYPE or file_doc.attached_to_name != doc.name:
		frappe.throw(_("上传文件与当前对账批次不匹配。"), frappe.PermissionError)
	if any(row.file == file_doc.file_url for row in doc.get(CHILD_FIELD) or []):
		return {"deleted": False}
	frappe.delete_doc("File", file_doc.name, ignore_permissions=True)
	return {"deleted": True}


@frappe.whitelist()
def get_latest_fulfillment_bill(cost_center, period=None, page=1):
	if not cost_center:
		return {"available": False}
	if period and period != "all" and not re.fullmatch(r"\d{6}", str(period)):
		frappe.throw(_("费用周期格式无效。"))
	source = _latest_fulfillment_source(cost_center)
	if not source:
		return {"available": False}
	file_row, snapshot = source["file"], source["snapshot"]
	if not snapshot or (file_row.file_hash and snapshot.source_file_hash != file_row.file_hash):
		return {"available": False, "pending": True, "source_file": file_row.original_file_name}
	payload = json.loads(snapshot.workbook_json)
	all_rows = []
	for sheet in payload.get("sheets", []):
		if sheet.get("name") not in {"揽收及退回服务费", "第三方揽收入库贴标服务费"}:
			continue
		for row in sheet.get("rows", [])[1:]:
			values = row.get("values") or []
			if not any(value is not None and str(value).strip() for value in values):
				continue
			is_waybill = sheet["name"] == "揽收及退回服务费"
			all_rows.append({
				"sheet": sheet["name"],
				"fee_period": _text(values[0]) or "",
				"payment_initiated_at": str(values[1] or ""),
				"billed_at": str(values[2] or ""),
				"fee_type": str(values[3] or ""),
				"reference": str(values[4] or ""),
				"reference_type": "运单号" if is_waybill else "SKU ID",
				"operation_at": str(values[7] or "") if is_waybill else str(values[5] or ""),
				"amount": str(values[5] if is_waybill else values[6]),
				"currency": str(values[6] if is_waybill else values[7]),
			})
	periods = sorted({row["fee_period"] for row in all_rows}, reverse=True)
	selected = str(period) if period else (periods[0] if periods else "all")
	selected_rows = [row for row in all_rows if selected == "all" or row["fee_period"] == selected]
	selected_rows.sort(key=lambda row: (row["billed_at"], row["reference"]), reverse=True)
	currency = selected_rows[0]["currency"] if selected_rows else (all_rows[0]["currency"] if all_rows else "CNY")
	fee_types = defaultdict(lambda: {"count": 0, "amount": Decimal("0")})
	for row in selected_rows:
		fee_types[row["fee_type"]]["count"] += 1
		fee_types[row["fee_type"]]["amount"] += Decimal(row["amount"])
	page_length = 50
	page = max(1, cint(page))
	page_count = max(1, (len(selected_rows) + page_length - 1) // page_length)
	page = min(page, page_count)
	return {
		"available": True,
		"source_file": file_row.original_file_name,
		"source_batch": file_row.parent,
		"uploaded_at": str(file_row.uploaded_at or ""),
		"periods": periods,
		"period": selected,
		"all_rows": len(all_rows),
		"row_count": len(selected_rows),
		"currency": currency,
		"total_amount": str(sum((item["amount"] for item in fee_types.values()), Decimal("0"))),
		"fee_types": [
			{"name": name, "count": item["count"], "amount": str(item["amount"])}
			for name, item in sorted(fee_types.items())
		],
		"page": page,
		"page_count": page_count,
		"rows": selected_rows[(page - 1) * page_length:page * page_length],
	}


@frappe.whitelist()
def get_batches():
	frappe.has_permission(BATCH_DOCTYPE, "read", throw=True)
	return frappe.get_list(
		BATCH_DOCTYPE,
		fields=[
			"name",
			"start_date",
			"end_date",
			"cost_center",
			"status",
			"file_count",
			"remarks",
			"creation",
			"modified",
			"owner",
		],
		order_by="creation desc, name desc",
		limit_page_length=200,
	)


@frappe.whitelist()
def get_batch(name):
	return _batch_payload(_get_batch_doc(name, "read"))


@frappe.whitelist()
def save_batch(
	name=None,
	start_date=None,
	end_date=None,
	cost_center=None,
	remarks=None,
	file_metadata=None,
):
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
	if not cost_center:
		frappe.throw(_("请选择代表Temu店铺的成本中心。"))
	doc.cost_center = cost_center
	doc.remarks = remarks or ""
	rows_by_name = {row.name: row for row in doc.get(CHILD_FIELD) or []}
	for values in _as_list(file_metadata):
		row = rows_by_name.get(values.get("name"))
		if not row:
			continue
		row.file_type = (values.get("file_type") or row.file_type or "").strip()
		if "display_name" in values:
			row.display_name = (values.get("display_name") or "").strip()
		row.sort_order = cint(values.get("sort_order")) or row.idx
		row.notes = values.get("notes") or ""

	_sync_summary(doc)
	if doc.is_new():
		doc.insert()
	else:
		doc.save()
	return _batch_payload(doc)


@frappe.whitelist()
def register_file(batch_name, file_doc_name, row_name=None, file_type=None, display_name=None, sort_order=0, notes=None, confirm_missing=0):
	doc = _get_batch_doc(batch_name, "write")
	file_doc = frappe.get_doc("File", file_doc_name)
	if file_doc.attached_to_doctype != BATCH_DOCTYPE or file_doc.attached_to_name != doc.name:
		frappe.throw(_("上传文件与当前对账批次不匹配。"), frappe.PermissionError)
	if not (file_doc.file_name or "").lower().endswith(".xlsx"):
		frappe.throw(_("这里只允许保存 XLSX 文件。"))
	if not cint(file_doc.is_private):
		frappe.throw(_("对账文件必须使用私有文件存储。"))
	if file_type == FULFILLMENT_FILE_TYPE:
		preview = _check_fulfillment_file(doc, file_doc)
		if preview["missing_rows"] and not cint(confirm_missing):
			frappe.throw(_("新账单缺少上一版的 {0} 条费用，请确认下载的是完整累计账单。").format(preview["missing_rows"]))
	elif file_type == FREIGHT_PAYMENT_FILE_TYPE:
		try:
			workbook = load_workbook(Path(file_doc.get_full_path()), read_only=True, data_only=True)
		except Exception:
			frappe.throw(_("无法读取店铺运费缴费详情 XLSX 文件：{0}").format(file_doc.file_name))
		try:
			validate_freight_payment_workbook(workbook)
		finally:
			workbook.close()
	elif file_type == SHIPMENT_LIST_FILE_TYPE:
		try:
			workbook = load_workbook(Path(file_doc.get_full_path()), read_only=True, data_only=True)
		except Exception:
			frappe.throw(_("无法读取发货单列表 XLSX 文件：{0}").format(file_doc.file_name))
		try:
			validate_shipment_list_workbook(workbook)
		finally:
			workbook.close()

	row = None
	if row_name:
		row = next((item for item in doc.get(CHILD_FIELD) or [] if item.name == row_name), None)
		if not row:
			frappe.throw(_("需要替换的文件记录不存在。"))
	old_file_url = row.file if row else None
	if not row:
		row = doc.append(CHILD_FIELD, {})

	row.file_type = (file_type or Path(file_doc.file_name).stem or _("未命名文件")).strip()
	row.display_name = (display_name if display_name is not None else row.display_name or "").strip()
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
	file_type = row.file_type
	doc.remove(row)
	_sync_summary(doc)
	doc.save()
	if file_type in {FREIGHT_PAYMENT_FILE_TYPE, SHIPMENT_LIST_FILE_TYPE}:
		frappe.db.delete(SNAPSHOT_DOCTYPE, {"reconciliation_batch": doc.name, "source_file_record": row_name})
	_delete_attached_file(doc.name, file_url)
	return _batch_payload(doc)


@frappe.whitelist()
def delete_batch(name):
	doc = _get_batch_doc(name, "delete")
	from fengjing_app.fengjing_business.page.temu_managed_bookkee.temu_financial_importer import (
		delete_batch_data,
	)

	delete_batch_data(doc.name)
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


@frappe.whitelist()
def sync_batch_data(batch_name, force=0):
	doc = _get_batch_doc(batch_name, "write")
	from fengjing_app.fengjing_business.page.temu_managed_bookkee.temu_financial_importer import (
		sync_batch_data as run_sync,
	)

	return run_sync(doc.name, force=bool(cint(force)))
