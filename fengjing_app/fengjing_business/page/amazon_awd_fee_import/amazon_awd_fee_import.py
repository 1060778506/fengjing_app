"""Import monthly AWD fee reports into a traceable batch."""

import json
import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import frappe
from frappe import _
from openpyxl import load_workbook


BATCH = "Amazon AWD Fee Batch"
MAX_ROWS = 20000
MAX_FILE_BYTES = 20 * 1024 * 1024
MONTHS = {name.lower(): number for number, name in enumerate(
    ("January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"), 1
)}


def _key(value):
    return re.sub(r"[\s_\-/()（）]+", "", str(value or "")).casefold()


def _value(row, *names):
    for name in names:
        value = row.get(_key(name))
        if value is not None and value != "":
            return value
    return None


def _text(value):
    return str(value).strip() if value is not None else ""


def _decimal(value):
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", "").strip())
    except Exception:
        frappe.throw(_("AWD 报告包含无法识别的数字：{0}").format(value))


def _money(value):
    return float(_decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _number(value):
    return float(_decimal(value)) if value is not None and value != "" else None


def _date(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    value = _text(value)
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    frappe.throw(_("AWD 报告包含无法识别的交易日期：{0}").format(value))


def _reported_period(row):
    year = _text(_value(row, "收费年份", "charge_year", "fee_year"))
    month = _text(_value(row, "收费月份", "charge_month", "fee_month"))
    if year and month:
        month_number = MONTHS.get(month.lower())
        if not month_number and re.fullmatch(r"\d{1,2}月?", month):
            month_number = int(month.rstrip("月"))
        if not month_number or not 1 <= month_number <= 12:
            frappe.throw(_("AWD 报告包含无法识别的收费月份：{0}").format(month))
        return f"{int(year):04d}-{month_number:02d}"
    transaction_date = _value(row, "交易日期", "transaction_date")
    return _date(transaction_date)[:7] if transaction_date else None


def _source_file(file_name):
    file_doc = frappe.get_doc("File", file_name)
    if file_doc.owner != frappe.session.user or file_doc.attached_to_doctype or file_doc.attached_to_name:
        frappe.throw(_("只能导入当前用户新上传、尚未关联单据的文件。"), frappe.PermissionError)
    if not file_doc.is_private or not (file_doc.file_name or "").lower().endswith(".xlsx"):
        frappe.throw(_("请上传私有的 XLSX 报告文件。"))
    path = Path(file_doc.get_full_path())
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        frappe.throw(_("AWD 文件不存在或超过 20 MB。"))
    return file_doc, path


def _read_rows(path, source_type, period):
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception:
        frappe.throw(_("无法读取 XLSX 文件：{0}").format(path.name))
    try:
        if len(workbook.worksheets) != 1:
            frappe.throw(_("AWD 月度报告应只有一个工作表。"))
        sheet = workbook.active
        iterator = sheet.iter_rows(values_only=True)
        header = next(iterator, None)
        if not header:
            frappe.throw(_("AWD 文件没有表头。"))
        header = [_text(value) for value in header]
        keys = [_key(value) for value in header]
        if source_type == "Storage":
            required_groups = [
                ("收取的总金额", "total_charged_amount"),
                ("多年期定价订单编号", "multi_year_pricing_order_id"),
                ("计费体积", "billed_volume"),
                ("ASIN", "FNSKU", "MSKU"),
            ]
        else:
            required_groups = [
                ("收费金额", "charged_amount", "charge_amount"),
                ("货件编号", "shipment_id"),
                ("出库订单编号", "outbound_order_id", "采购订单编号", "purchase_order_id") if source_type == "Processing"
                else ("箱子总体积", "total_box_volume", "box_volume"),
            ]
        if not all(any(_key(alias) in keys for alias in group) for group in required_groups):
            label = {"Processing": "处理费", "Transportation": "运输费", "Storage": "仓储费"}[source_type]
            frappe.throw(_("文件列与{0}报告不符，请检查所选文件。").format(label))
        if len(set(key for key in keys if key)) != len([key for key in keys if key]):
            frappe.throw(_("AWD 文件存在重复列名。"))
        rows = []
        for source_row, values in enumerate(iterator, 2):
            if not any(value is not None and value != "" for value in values):
                continue
            if len(rows) >= MAX_ROWS:
                frappe.throw(_("单份 AWD 报告最多导入 {0} 条。").format(MAX_ROWS))
            original = dict(zip(header, values))
            row = {_key(key): value for key, value in original.items() if key}
            reported_period = _reported_period(row)
            if reported_period != period:
                frappe.throw(_("第 {0} 行收费月份为 {1}，与所选 {2} 不一致。").format(
                    source_row, reported_period or "空", period))
            currency = _text(_value(row, "货币", "currency"))
            if currency != "USD":
                frappe.throw(_("第 {0} 行货币不是 USD：{1}").format(source_row, currency))
            charged = (_value(row, "收取的总金额", "total_charged_amount")
                       if source_type == "Storage"
                       else _value(row, "收费金额", "charged_amount", "charge_amount"))
            if charged is None:
                frappe.throw(_("第 {0} 行缺少收费金额。").format(source_row))
            detail = {
                "source_type": source_type,
                "source_row": source_row,
                "charge_type": _text(_value(row, "收费类型", "charge_type")),
                "transaction_date": (
                    _date(_value(row, "交易日期", "transaction_date"))
                    if source_type != "Storage" else None
                ),
                "fee_type": _text(_value(row, "费用类型", "fee_type")),
                "inbound_order_id": _text(_value(row, "采购订单编号", "入库购买订单编号", "purchase_order_id", "inbound_purchase_order_id")),
                "replenishment_order_id": _text(_value(row, "出库订单编号", "补货订单编号", "outbound_order_id", "replenishment_order_id")),
                "shipment_id": _text(_value(row, "货件编号", "shipment_id")),
                "tracking_id": _text(_value(row, "出库货件追踪编码", "货件追踪编码", "outbound_shipment_tracking_id", "tracking_id")),
                "outbound_purchase_order_id": _text(_value(row, "出库购买订单编号", "outbound_purchase_order_id")),
                "billing_order_id": _text(_value(row, "多年期定价订单编号", "multi_year_pricing_order_id")),
                "asin": _text(_value(row, "ASIN")),
                "fnsku": _text(_value(row, "FNSKU")),
                "msku": _text(_value(row, "MSKU")),
                "carrier_type": _text(_value(row, "入库承运人类型", "inbound_carrier_type")),
                "facility_id": _text(_value(row, "库房编号", "facility_id")),
                "destination_code": _text(_value(row, "目的地代码", "destination_code")),
                "storage_region": _text(_value(row, "区域", "region")),
                "average_used_capacity": _number(_value(row, "月平均使用容量", "average_used_capacity")),
                "billed_volume": _number(_value(row, "计费体积", "billed_volume")),
                "box_count": _number(_value(row, "箱子数量", "number_of_boxes", "box_count")),
                "box_volume": _number(_value(row, "箱子总体积", "total_box_volume", "box_volume")),
                "volume_unit": _text(_value(row, "体积单位", "volume_unit")),
                "rate": _number(_value(row, "费率", "rate")),
                "rate_unit": _text(_value(row, "费率单位", "rate_unit")),
                "rate_type": _text(_value(row, "费率类型", "rate_type")),
                "calculated_amount": _money(_value(
                    row, "费用金额", "fee_amount"
                ) if source_type == "Storage" else _value(
                    row, "计算得出的费用金额", "calculated_fee_amount", "calculated_amount"
                )),
                "promotion_amount": _money(_value(row, "促销金额", "promotion_amount")),
                "tax_amount": _money(_value(row, "税额", "tax_amount")),
                "charged_amount": _money(charged),
                "currency": currency,
                "raw_json": json.dumps(original, ensure_ascii=False, default=str),
            }
            if source_type != "Storage" and not detail["shipment_id"]:
                frappe.throw(_("第 {0} 行缺少货件编号。").format(source_row))
            if source_type == "Processing" and not (
                detail["inbound_order_id"] or detail["replenishment_order_id"]
            ):
                frappe.throw(_("第 {0} 行缺少入库或补货订单编号。").format(source_row))
            if source_type == "Transportation" and not detail["replenishment_order_id"]:
                frappe.throw(_("第 {0} 行缺少补货订单编号。").format(source_row))
            if source_type == "Storage" and not detail["billing_order_id"]:
                frappe.throw(_("第 {0} 行缺少仓储账单编号。").format(source_row))
            if source_type == "Storage" and not (detail["asin"] or detail["fnsku"] or detail["msku"]):
                frappe.throw(_("第 {0} 行缺少商品标识。").format(source_row))
            rows.append(detail)
        return rows
    finally:
        workbook.close()


def _file_for_batch(batch, fieldname):
    file_url = batch.get(fieldname)
    if not file_url:
        frappe.throw(_("批次 {0} 缺少原始报告：{1}").format(batch.name, fieldname))
    matches = frappe.get_all(
        "File",
        filters={
            "attached_to_doctype": BATCH,
            "attached_to_name": batch.name,
            "file_url": file_url,
        },
        fields=["name"],
        order_by="creation desc",
        limit_page_length=1,
    )
    if not matches:
        frappe.throw(_("找不到批次 {0} 的原始报告：{1}").format(batch.name, fieldname))
    file_doc = frappe.get_doc("File", matches[0].name)
    path = Path(file_doc.get_full_path())
    if not path.is_file():
        frappe.throw(_("原始报告文件不存在：{0}").format(file_doc.file_name))
    return file_doc, path


def _seller_from_details(details):
    sellers = set()
    for detail in details:
        original = json.loads(detail["raw_json"])
        normalized = {_key(key): value for key, value in original.items()}
        seller = _text(_value(normalized, "卖家客户编号", "merchant_customer_id"))
        if seller:
            sellers.add(seller)
    if len(sellers) != 1:
        frappe.throw(_("同一批次各报告的卖家客户编号必须一致，且不能留空。"))
    return next(iter(sellers))


def _link_uploaded_files(batch, uploaded):
    # Saving Attach fields may update File.modified before this method runs.
    for name in uploaded:
        file_doc = frappe.get_doc("File", name)
        if file_doc.attached_to_doctype == BATCH and file_doc.attached_to_name == batch.name:
            continue
        if file_doc.attached_to_doctype or file_doc.attached_to_name:
            frappe.throw(_("上传文件已关联其他单据，请重新上传。"))
        file_doc.attached_to_doctype = BATCH
        file_doc.attached_to_name = batch.name
        file_doc.save(ignore_permissions=True)


def _make_current(batch, seller, period):
    existing = frappe.get_list(
        BATCH,
        filters={"period": period, "seller_customer_id": seller, "status": "当前"},
        fields=["name"],
        limit_page_length=1000,
    )
    for old in existing:
        if old.name == batch.name:
            continue
        prior = frappe.get_doc(BATCH, old.name)
        prior.check_permission("write")
        prior.status = "历史"
        prior.save()


def _batch_payload(batch):
    payload = {
        "name": batch.name,
        "period": batch.period,
        "seller_customer_id": batch.seller_customer_id,
        "status": batch.status,
        "modified": str(batch.modified),
        "processing_total": batch.processing_total,
        "transportation_total": batch.transportation_total,
        "storage_total": batch.storage_total,
        "total_amount": batch.total_amount,
        "row_count": batch.row_count,
    }
    for field in ("processing_file", "transportation_file", "storage_file"):
        if batch.get(field):
            file_doc, _ = _file_for_batch(batch, field)
            payload[field] = {"url": file_doc.file_url, "name": file_doc.file_name}
        else:
            payload[field] = None
    return payload


@frappe.whitelist()
def list_batches():
    return frappe.get_list(
        BATCH,
        fields=["name", "period", "seller_customer_id", "status", "processing_total",
                "transportation_total", "storage_total", "total_amount", "row_count", "creation"],
        order_by="creation desc",
        limit_page_length=100,
    )


@frappe.whitelist()
def get_batch(name):
    batch = frappe.get_doc(BATCH, name)
    batch.check_permission("read")
    return _batch_payload(batch)


@frappe.whitelist()
def discard_uploads(file_names):
    if isinstance(file_names, str):
        file_names = frappe.parse_json(file_names)
    for name in (file_names or [])[:3]:
        file_doc = frappe.get_doc("File", name)
        if file_doc.owner == frappe.session.user and not file_doc.attached_to_doctype and not file_doc.attached_to_name:
            frappe.delete_doc("File", name, ignore_permissions=True)
    return True


@frappe.whitelist()
def import_fee_batch(period, processing_file=None, transportation_file=None, storage_file=None):
    if not frappe.has_permission(BATCH, "create"):
        frappe.throw(_("没有创建 AWD 费用批次的权限。"), frappe.PermissionError)
    period = _text(period)
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period):
        frappe.throw(_("费用月份必须是 YYYY-MM 格式。"))
    names = [name for name in (processing_file, transportation_file, storage_file) if name]
    if not names or len(names) != len(set(names)):
        frappe.throw(_("请至少上传一份 AWD 费用报告，且不同报告不能使用同一个文件。"))
    sources = {}
    details = []
    for fieldname, source_type, name in (
        ("processing_file", "Processing", processing_file),
        ("transportation_file", "Transportation", transportation_file),
        ("storage_file", "Storage", storage_file),
    ):
        if not name:
            continue
        file_doc, path = _source_file(name)
        sources[fieldname] = file_doc
        details.extend(_read_rows(path, source_type, period))
    if not details:
        frappe.throw(_("上传的报告没有费用明细。"))
    seller = _seller_from_details(details)
    if frappe.get_list(BATCH, filters={
        "period": period, "seller_customer_id": seller, "status": "当前",
    }, fields=["name"], limit_page_length=1):
        frappe.throw(_("该卖家和月份已有当前批次，请在左侧选择该批次添加或替换报告。"))
    batch = frappe.get_doc({
        "doctype": BATCH,
        "period": period,
        "seller_customer_id": seller,
        "status": "当前",
        "currency": "USD",
        **{field: file_doc.file_url for field, file_doc in sources.items()},
        "details": details,
    })
    batch.insert()
    _make_current(batch, seller, period)
    _link_uploaded_files(batch, [file_doc.name for file_doc in sources.values()])
    return _batch_payload(batch)


@frappe.whitelist()
def update_fee_batch(name, expected_modified, processing_file=None, transportation_file=None, storage_file=None):
    batch = frappe.get_doc(BATCH, name)
    batch.check_permission("write")
    if str(batch.modified) != _text(expected_modified):
        frappe.throw(_("批次已被修改，请刷新后再保存。"))
    if not processing_file and not transportation_file and not storage_file:
        frappe.throw(_("请先选择需要替换的报告。"))
    names = [name for name in (processing_file, transportation_file, storage_file) if name]
    if len(names) != len(set(names)):
        frappe.throw(_("不同报告不能使用同一个文件。"))
    uploaded = []
    sources = {}
    for fieldname, source_type, new_name in (
        ("processing_file", "Processing", processing_file),
        ("transportation_file", "Transportation", transportation_file),
        ("storage_file", "Storage", storage_file),
    ):
        if new_name:
            file_doc, path = _source_file(new_name)
            uploaded.append(file_doc.name)
        elif batch.get(fieldname):
            file_doc, path = _file_for_batch(batch, fieldname)
        else:
            continue
        sources[source_type] = (file_doc, path)
    details = []
    for source_type, (_, path) in sources.items():
        details.extend(_read_rows(path, source_type, batch.period))
    if not details:
        frappe.throw(_("该批次没有费用明细。"))
    seller = _seller_from_details(details)
    if seller != batch.seller_customer_id:
        frappe.throw(_("替换文件的卖家客户编号与当前批次不一致，请新建批次。"))
    for fieldname, source_type, new_name in (
        ("processing_file", "Processing", processing_file),
        ("transportation_file", "Transportation", transportation_file),
        ("storage_file", "Storage", storage_file),
    ):
        if new_name:
            batch.set(fieldname, sources[source_type][0].file_url)
    batch.set("details", [])
    for detail in details:
        batch.append("details", detail)
    batch.status = "当前"
    batch.save()
    _make_current(batch, seller, batch.period)
    _link_uploaded_files(batch, uploaded)
    return _batch_payload(batch)
