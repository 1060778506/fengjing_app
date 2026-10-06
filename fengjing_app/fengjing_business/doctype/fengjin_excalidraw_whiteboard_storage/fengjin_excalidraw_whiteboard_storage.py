# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


DOCTYPE = "Fengjin Excalidraw whiteboard storage"
MAX_WHITEBOARD_BYTES = 25 * 1024 * 1024
DEFAULT_TITLE = "未命名白板"


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

	payload.setdefault("type", "excalidraw")
	payload.setdefault("version", 2)
	payload.setdefault("source", "fengjing_app")
	payload.setdefault("elements", [])
	payload.setdefault("appState", {})
	payload.setdefault("files", {})

	normalised = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
	if len(normalised.encode("utf-8")) > MAX_WHITEBOARD_BYTES:
		frappe.throw(_("白板数据不能超过25MB。"))
	return normalised


def _document_payload(doc, include_data=False):
	result = {
		"name": doc.name,
		"whiteboard_title": doc.whiteboard_title,
		"folder_name": doc.folder_name or "",
		"revision": cint(doc.revision),
		"preview_image": doc.preview_image or "",
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


@frappe.whitelist()
def create_whiteboard(whiteboard_title=None, folder_name=None, whiteboard_data=None):
	frappe.has_permission(DOCTYPE, "create", throw=True)
	doc = frappe.new_doc(DOCTYPE)
	doc.whiteboard_title = _clean_text(whiteboard_title, DEFAULT_TITLE)
	doc.folder_name = _clean_text(folder_name)
	doc.revision = 1
	doc.whiteboard_data = _normalise_whiteboard_data(whiteboard_data)
	doc.insert()
	return _document_payload(doc, include_data=True)


@frappe.whitelist()
def get_whiteboard(name):
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	return _document_payload(doc, include_data=True)


@frappe.whitelist()
def save_whiteboard(
	name,
	whiteboard_data,
	expected_revision=None,
	whiteboard_title=None,
	folder_name=None,
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
