# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import json
import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, cint, get_datetime, now_datetime


DOCTYPE = "Fengjin Excalidraw whiteboard storage"
MAX_WHITEBOARD_BYTES = 25 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_IMAGE_COUNT = 1000
ORPHAN_GRACE_HOURS = 24
DEFAULT_TITLE = "未命名白板"
FILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
ALLOWED_IMAGE_MIME_TYPES = {
	"image/avif",
	"image/bmp",
	"image/gif",
	"image/ico",
	"image/jpeg",
	"image/jfif",
	"image/png",
	"image/svg+xml",
	"image/webp",
	"image/x-icon",
}


def _clean_text(value, default=""):
	cleaned = str(value or "").strip()
	return (cleaned or default)[:140]


def _normalise_whiteboard_data(value):
	if value in (None, ""):
		payload = {
			"type": "excalidraw",
			"version": 2,
			"source": "fengjing_app",
			"elements": [],
			"appState": {},
			"files": {},
		}
	elif isinstance(value, str):
		if len(value.encode("utf-8")) > MAX_WHITEBOARD_BYTES:
			frappe.throw(_("白板数据不能超过25MB。"))
		try:
			payload = json.loads(value)
		except (TypeError, ValueError):
			frappe.throw(_("白板数据不是有效的JSON。"))
	else:
		payload = value

	if not isinstance(payload, dict):
		frappe.throw(_("白板数据必须是JSON对象。"))
	if not isinstance(payload.get("elements", []), list):
		frappe.throw(_("白板数据中的elements必须是数组。"))
	if not isinstance(payload.get("appState", {}), dict):
		frappe.throw(_("白板数据中的appState必须是对象。"))
	if not isinstance(payload.get("files", {}), dict):
		frappe.throw(_("白板数据中的files必须是对象。"))

	payload = dict(payload)
	payload.setdefault("type", "excalidraw")
	payload.setdefault("version", 2)
	payload.setdefault("source", "fengjing_app")
	payload.setdefault("elements", [])
	payload.setdefault("appState", {})
	# 图片二进制由私有 File 附件保存，白板 JSON 永远不持久化 dataURL/Base64。
	payload["files"] = {}

	normalised = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
	if len(normalised.encode("utf-8")) > MAX_WHITEBOARD_BYTES:
		frappe.throw(_("白板数据不能超过25MB。"))
	return normalised


def _normalise_image_manifest(value):
	if value in (None, ""):
		payload = {}
	elif isinstance(value, str):
		if len(value.encode("utf-8")) > MAX_MANIFEST_BYTES:
			frappe.throw(_("白板图片索引不能超过2MB。"))
		try:
			payload = json.loads(value)
		except (TypeError, ValueError):
			frappe.throw(_("白板图片索引不是有效的JSON。"))
	else:
		payload = value

	if not isinstance(payload, dict):
		frappe.throw(_("白板图片索引必须是JSON对象。"))
	if len(payload) > MAX_IMAGE_COUNT:
		frappe.throw(_("单个白板最多保存{0}张图片。").format(MAX_IMAGE_COUNT))

	normalised = {}
	for file_id, raw_entry in payload.items():
		file_id = str(file_id or "").strip()
		if not FILE_ID_PATTERN.fullmatch(file_id) or not isinstance(raw_entry, dict):
			frappe.throw(_("白板图片索引中存在无效记录。"))
		mime_type = str(raw_entry.get("mime_type") or "").strip().lower()
		if mime_type and mime_type not in ALLOWED_IMAGE_MIME_TYPES:
			frappe.throw(_("不支持图片格式：{0}").format(mime_type))
		normalised[file_id] = {
			"file_document": _clean_text(raw_entry.get("file_document")),
			"file_url": str(raw_entry.get("file_url") or "").strip()[:500],
			"file_name": str(raw_entry.get("file_name") or "").strip()[:255],
			"mime_type": mime_type,
			"size": max(cint(raw_entry.get("size")), 0),
			"created": max(cint(raw_entry.get("created")), 0),
			"version": max(cint(raw_entry.get("version")), 1),
		}
		if raw_entry.get("orphaned_at"):
			try:
				normalised[file_id]["orphaned_at"] = str(get_datetime(raw_entry.get("orphaned_at")))
			except (TypeError, ValueError):
				frappe.throw(_("白板图片索引中存在无效的清理时间。"))

	encoded = json.dumps(normalised, ensure_ascii=False, separators=(",", ":"))
	if len(encoded.encode("utf-8")) > MAX_MANIFEST_BYTES:
		frappe.throw(_("白板图片索引不能超过2MB。"))
	return normalised


def _manifest_json(value):
	return json.dumps(_normalise_image_manifest(value), ensure_ascii=False, separators=(",", ":"))


def _active_file_ids(whiteboard_data):
	payload = json.loads(_normalise_whiteboard_data(whiteboard_data))
	return {
		str(element.get("fileId"))
		for element in payload.get("elements", [])
		if isinstance(element, dict)
		and element.get("type") == "image"
		and not element.get("isDeleted")
		and element.get("fileId")
	}


def _get_attached_file(docname, entry):
	file_document = entry.get("file_document")
	if not file_document:
		return None
	file_doc = frappe.db.get_value(
		"File",
		file_document,
		[
			"name",
			"file_name",
			"file_url",
			"file_size",
			"is_private",
			"attached_to_doctype",
			"attached_to_name",
		],
		as_dict=True,
	)
	if not file_doc:
		return None
	if (
		file_doc.attached_to_doctype != DOCTYPE
		or file_doc.attached_to_name != docname
		or not cint(file_doc.is_private)
		or not str(file_doc.file_url or "").startswith("/private/files/")
	):
		return None
	return file_doc


def _delete_attached_file(docname, file_document):
	file_doc = frappe.db.get_value(
		"File",
		file_document,
		["name", "attached_to_doctype", "attached_to_name"],
		as_dict=True,
	)
	if not file_doc:
		return
	if file_doc.attached_to_doctype == DOCTYPE and file_doc.attached_to_name == docname:
		frappe.delete_doc("File", file_doc.name, ignore_permissions=True)


def _reconcile_image_manifest(doc, incoming_manifest, whiteboard_data):
	active_ids = _active_file_ids(whiteboard_data)
	existing = _normalise_image_manifest(doc.image_manifest)
	incoming = _normalise_image_manifest(incoming_manifest)
	combined = {**existing, **incoming}
	result = {}
	now = now_datetime()

	for file_id, entry in combined.items():
		file_doc = _get_attached_file(doc.name, entry)
		if not file_doc:
			if file_id in active_ids:
				frappe.throw(_("白板图片{0}不是当前白板的有效私有附件。").format(file_id))
			continue

		entry.update(
			{
				"file_document": file_doc.name,
				"file_url": file_doc.file_url,
				"file_name": file_doc.file_name,
				"size": cint(file_doc.file_size),
			}
		)
		if file_id in active_ids:
			entry.pop("orphaned_at", None)
			result[file_id] = entry
			continue

		orphaned_at = entry.get("orphaned_at")
		if orphaned_at and add_to_date(get_datetime(orphaned_at), hours=ORPHAN_GRACE_HOURS) <= now:
			_delete_attached_file(doc.name, file_doc.name)
			continue
		entry["orphaned_at"] = orphaned_at or str(now)
		result[file_id] = entry

	missing_ids = active_ids - set(result)
	if missing_ids:
		frappe.throw(_("以下白板图片尚未上传：{0}").format(", ".join(sorted(missing_ids))))
	return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


def _document_payload(doc, include_data=False):
	result = {
		"name": doc.name,
		"whiteboard_title": doc.whiteboard_title,
		"folder_name": doc.folder_name or "",
		"revision": cint(doc.revision),
		"preview_image": doc.preview_image or "",
		"image_manifest": _normalise_image_manifest(doc.image_manifest),
		"owner": doc.owner,
		"modified": str(doc.modified),
	}
	if include_data:
		result["whiteboard_data"] = doc.whiteboard_data or _normalise_whiteboard_data(None)
	return result


class FengjinExcalidrawwhiteboardstorage(Document):
	def validate(self):
		self.whiteboard_title = _clean_text(self.whiteboard_title, DEFAULT_TITLE)
		self.folder_name = _clean_text(self.folder_name)
		self.revision = max(cint(self.revision), 0)
		self.whiteboard_data = _normalise_whiteboard_data(self.whiteboard_data)
		self.image_manifest = _manifest_json(self.image_manifest)


@frappe.whitelist()
def create_whiteboard(whiteboard_title=None, folder_name=None, whiteboard_data=None, image_manifest=None):
	frappe.has_permission(DOCTYPE, "create", throw=True)
	doc = frappe.new_doc(DOCTYPE)
	doc.whiteboard_title = _clean_text(whiteboard_title, DEFAULT_TITLE)
	doc.folder_name = _clean_text(folder_name)
	doc.revision = 1
	doc.whiteboard_data = _normalise_whiteboard_data(whiteboard_data)
	# 新白板尚未拥有附件，客户端不能预先写入任意文件地址。
	doc.image_manifest = _manifest_json({})
	doc.insert()
	return _document_payload(doc, include_data=True)


@frappe.whitelist()
def get_whiteboard(name):
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	return _document_payload(doc, include_data=True)


@frappe.whitelist()
def delete_whiteboard(name):
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("delete")
	whiteboard_title = doc.whiteboard_title or DEFAULT_TITLE
	frappe.delete_doc(DOCTYPE, doc.name)
	return {"name": doc.name, "whiteboard_title": whiteboard_title}


@frappe.whitelist()
def save_whiteboard(
	name,
	whiteboard_data,
	expected_revision=None,
	whiteboard_title=None,
	folder_name=None,
	image_manifest=None,
):
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("write")
	current_revision = cint(doc.revision)
	if expected_revision is not None and cint(expected_revision) != current_revision:
		frappe.throw(
			_("白板已在其他窗口中更新。请重新打开后再保存。"),
			exc=frappe.TimestampMismatchError,
		)

	if whiteboard_title is not None:
		doc.whiteboard_title = _clean_text(whiteboard_title, DEFAULT_TITLE)
	if folder_name is not None:
		doc.folder_name = _clean_text(folder_name)
	doc.whiteboard_data = _normalise_whiteboard_data(whiteboard_data)
	doc.image_manifest = _reconcile_image_manifest(doc, image_manifest, doc.whiteboard_data)
	doc.revision = current_revision + 1
	doc.save()
	return _document_payload(doc, include_data=False)


@frappe.whitelist()
def list_whiteboards(search_text=None, limit=100):
	frappe.has_permission(DOCTYPE, "read", throw=True)
	filters = {}
	search_text = _clean_text(search_text)
	if search_text:
		filters["whiteboard_title"] = ["like", f"%{search_text}%"]
	return frappe.get_list(
		DOCTYPE,
		filters=filters,
		fields=[
			"name",
			"whiteboard_title",
			"folder_name",
			"revision",
			"preview_image",
			"owner",
			"modified",
		],
		order_by="modified desc",
		limit_page_length=min(max(cint(limit), 1), 200),
	)
