"""Read-only view of imported Temu fulfillment bill and payment workbooks."""

import json
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.utils import cint

from fengjing_app.fengjing_business.page.temu_managed_bookkee.temu_financial_importer import (
	FREIGHT_PAYMENT_FILE_TYPE,
	FULFILLMENT_FILE_TYPE,
)


BATCH = "Temu Financial Reconciliation Batch"
FILE_ROW = "Temu Financial Ledger Detail Report"
SNAPSHOT = "Temu Financial Source JSON Snapshot"
WAYBILL_SHEET = "揽收及退回服务费"
LABEL_SHEET = "第三方揽收入库贴标服务费"
SOURCE_TYPES = {"bill": FULFILLMENT_FILE_TYPE, "payment": FREIGHT_PAYMENT_FILE_TYPE}


def _text(value):
	return "" if value is None else str(value).strip()


def _amount(value):
	try:
		return Decimal(_text(value).replace(",", "").replace("¥", "").replace("￥", ""))
	except (InvalidOperation, ValueError):
		return Decimal("0")


def _money(value):
	return str(value.quantize(Decimal("0.01")))


def _row(sheet, values, store, file_row):
	waybill = sheet == WAYBILL_SHEET
	return {
		"cost_center": store,
		"sheet": sheet,
		"fee_period": _text(values[0]),
		"payment_initiated_at": _text(values[1]),
		"billed_at": _text(values[2]),
		"fee_type": _text(values[3]),
		"reference": _text(values[4]),
		"reference_type": "运单号" if waybill else "SKU ID",
		"operation_at": _text(values[7] if waybill else values[5]),
		"receipt_at": _text(values[8]) if waybill and len(values) > 8 else "",
		"amount": _amount(values[5] if waybill else values[6]),
		"currency": _text(values[6] if waybill else values[7]) or "CNY",
		"source_file": file_row.original_file_name or "",
		"source_file_record": file_row.name,
		"source_file_url": file_row.file or "",
		"source_batch": file_row.parent,
	}


def _rows_from_snapshot(snapshot, store, file_row):
	try:
		payload = json.loads(snapshot.workbook_json)
	except (TypeError, ValueError):
		return []
	rows = []
	for sheet in payload.get("sheets", []):
		if sheet.get("name") not in {WAYBILL_SHEET, LABEL_SHEET}:
			continue
		for item in sheet.get("rows", [])[1:]:
			values = item.get("values") or []
			if len(values) < 8 or not any(_text(value) for value in values):
				continue
			rows.append(_row(sheet["name"], values, store, file_row))
	return rows


def _payment_identity(row):
	return (
		row["cost_center"], row["sheet"], row["fee_period"],
		row["payment_initiated_at"], row["billed_at"], row["fee_type"],
		row["reference"], row["operation_at"], row["receipt_at"],
		row["amount"], row["currency"],
	)


def _match_identity(row):
	return (
		row["cost_center"], row["sheet"], row["fee_period"],
		row["billed_at"], row["fee_type"], row["reference"],
		row["amount"], row["currency"],
	)


def _sources(cost_center):
	frappe.has_permission(BATCH, "read", throw=True)
	batches = frappe.get_list(
		BATCH, fields=["name", "cost_center"],
		limit_page_length=5000,
	)
	all_stores = sorted({row.cost_center for row in batches if row.cost_center})
	if cost_center:
		batches = [row for row in batches if row.cost_center == cost_center]
	if not batches:
		return [], [], [], all_stores
	stores_by_batch = {row.name: row.cost_center or "" for row in batches}
	files = frappe.get_all(
		FILE_ROW,
		filters={"parent": ["in", list(stores_by_batch)], "parenttype": BATCH,
			"file_type": ["in", list(SOURCE_TYPES.values())]},
		fields=["name", "parent", "file", "file_type", "original_file_name", "file_hash", "uploaded_at", "creation"],
		limit_page_length=10000,
	)
	if not files:
		return [], [], [], all_stores
	file_by_id = {row.name: row for row in files}
	snapshots = frappe.get_all(
		SNAPSHOT,
		filters={"source_file_record": ["in", list(file_by_id)], "conversion_status": "成功"},
		fields=["name", "source_file_record", "source_file_hash"],
		limit_page_length=10000,
	)
	snapshot_by_file = {
		row.source_file_record: row.name for row in snapshots
		if row.source_file_record in file_by_id
		and row.source_file_hash == file_by_id[row.source_file_record].file_hash
	}
	valid_files = [row for row in files if row.name in snapshot_by_file]
	valid_files.sort(key=lambda row: (_text(row.uploaded_at), _text(row.creation), row.name), reverse=True)
	latest_bill = {}
	for row in valid_files:
		if row.file_type == FULFILLMENT_FILE_TYPE:
			latest_bill.setdefault(stores_by_batch[row.parent], row)
	selected = list(latest_bill.values()) + [row for row in valid_files if row.file_type == FREIGHT_PAYMENT_FILE_TYPE]
	bill_rows, payment_rows, source_files = [], [], []
	seen_payment = Counter()
	for file_row in selected:
		snapshot = frappe.get_doc(SNAPSHOT, snapshot_by_file[file_row.name])
		store = stores_by_batch[file_row.parent]
		parsed = _rows_from_snapshot(snapshot, store, file_row)
		kind = "bill" if file_row.file_type == FULFILLMENT_FILE_TYPE else "payment"
		source_files.append({
			"id": file_row.name, "kind": kind, "cost_center": store,
			"name": file_row.original_file_name or "", "url": file_row.file or "",
			"batch": file_row.parent, "uploaded_at": _text(file_row.uploaded_at),
			"row_count": len(parsed), "totals": _totals(parsed),
			"billed_from": min((_text(item["billed_at"]) for item in parsed if item["billed_at"]), default="")[:10],
			"billed_to": max((_text(item["billed_at"]) for item in parsed if item["billed_at"]), default="")[:10],
			"payment_from": min((_text(item["payment_initiated_at"]) for item in parsed if item["payment_initiated_at"]), default="")[:10],
			"payment_to": max((_text(item["payment_initiated_at"]) for item in parsed if item["payment_initiated_at"]), default="")[:10],
		})
		if kind == "bill":
			bill_rows.extend(parsed)
			continue
		occurrences = Counter()
		for row in parsed:
			key = _payment_identity(row)
			occurrences[key] += 1
			if occurrences[key] > seen_payment[key]:
				payment_rows.append(row)
		seen_payment.update({key: max(seen_payment[key], count) - seen_payment[key] for key, count in occurrences.items()})
	for file in source_files:
		effective_rows = [row for row in (bill_rows if file["kind"] == "bill" else payment_rows)
			if row["source_file_record"] == file["id"]]
		file["effective_count"] = len(effective_rows)
		file["effective_totals"] = _totals(effective_rows)
	return bill_rows, payment_rows, source_files, all_stores


def _totals(rows):
	amounts = defaultdict(lambda: Decimal("0"))
	for row in rows:
		amounts[row["currency"]] += row["amount"]
	return [{"currency": currency, "amount": _money(amount)} for currency, amount in sorted(amounts.items())]


def _groups(rows, field):
	groups = defaultdict(list)
	for row in rows:
		groups[row[field]].append(row)
	return [
		{"name": name, "count": len(items), "totals": _totals(items)}
		for name, items in sorted(groups.items(), reverse=field == "fee_period")
	]


def _detail_page(rows, page):
	rows.sort(key=lambda row: (row["billed_at"], row["reference"]), reverse=True)
	page_size = 50
	pages = max(1, (len(rows) + page_size - 1) // page_size)
	page = min(max(1, cint(page)), pages)
	return {
		"count": len(rows), "page": page, "page_count": pages,
		"rows": [{**row, "amount": _money(row["amount"])}
			for row in rows[(page - 1) * page_size:page * page_size]],
	}


@frappe.whitelist()
def get_analysis(cost_center=None, payment_month=None, fee_type=None, search=None,
		bill_file=None, payment_file=None, bill_page=1, payment_page=1):
	search = _text(search)[:100].casefold()
	bill_all, payment_all, files, stores = _sources(cost_center)
	month_values = [row["billed_at"][:7] for row in bill_all]
	month_values.extend(row["payment_initiated_at"][:7] for row in payment_all)
	months = sorted({value for value in month_values if re.fullmatch(r"\d{4}-\d{2}", value)}, reverse=True)
	if payment_month and payment_month != "all" and not re.fullmatch(r"\d{4}-\d{2}", _text(payment_month)):
		frappe.throw(_("缴费发起月份格式无效。"))
	selected_month = payment_month or (months[0] if months else "all")
	if selected_month != "all":
		bill_all = [row for row in bill_all if row["billed_at"].startswith(selected_month)]
		payment_all = [row for row in payment_all if row["payment_initiated_at"].startswith(selected_month)]
		active_rows = bill_all + payment_all
		for file in files:
			file_rows = [row for row in active_rows if row["source_file_record"] == file["id"]]
			file["effective_count"] = len(file_rows)
			file["effective_totals"] = _totals(file_rows)
			file["billed_from"] = min((row["billed_at"] for row in file_rows), default="")[:10]
			file["billed_to"] = max((row["billed_at"] for row in file_rows), default="")[:10]
			file["payment_from"] = min((row["payment_initiated_at"] for row in file_rows), default="")[:10]
			file["payment_to"] = max((row["payment_initiated_at"] for row in file_rows), default="")[:10]
		files = [file for file in files if file["effective_count"]]
	files.sort(key=lambda file: (file["billed_to"] if file["kind"] == "bill" else file["payment_to"], file["uploaded_at"]), reverse=True)
	fee_types = sorted({row["fee_type"] for row in bill_all + payment_all})
	valid_bill_files = {file["id"] for file in files if file["kind"] == "bill"}
	valid_payment_files = {file["id"] for file in files if file["kind"] == "payment"}
	if bill_file and bill_file not in valid_bill_files:
		frappe.throw(_("账单文件不属于当前店铺。"))
	if payment_file and payment_file not in valid_payment_files:
		frappe.throw(_("缴费文件不属于当前店铺。"))
	def select(rows, file_id):
		return [row for row in rows if
			(not file_id or row["source_file_record"] == file_id)
			and (not fee_type or row["fee_type"] == fee_type)
			and (not search or search in " ".join((row["cost_center"], row["fee_type"], row["reference"], row["source_file"])).casefold())]
	bill_rows, payment_rows = select(bill_all, bill_file), select(payment_all, payment_file)
	bill_counts = Counter(_match_identity(row) for row in bill_rows)
	matched = []
	unmatched = []
	for row in payment_rows:
		key = _match_identity(row)
		if bill_counts[key]:
			bill_counts[key] -= 1
			matched.append(row)
		else:
			unmatched.append(row)
	return {
		"stores": stores, "months": months, "selected_month": selected_month, "fee_types": fee_types,
		"bill": {"count": len(bill_rows), "totals": _totals(bill_rows), "by_type": _groups(bill_rows, "fee_type"), "detail": _detail_page(bill_rows, bill_page)},
		"payment": {"count": len(payment_rows), "totals": _totals(payment_rows), "by_type": _groups(payment_rows, "fee_type"), "detail": _detail_page(payment_rows, payment_page)},
		"matched": {"count": len(matched), "totals": _totals(matched)},
		"unmatched_payment": {"count": len(unmatched), "totals": _totals(unmatched)},
		"files": files,
	}
