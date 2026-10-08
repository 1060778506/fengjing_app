import frappe
from frappe import _
from frappe.desk.doctype.event.event import get_events
from frappe.utils import add_days, cint, get_datetime, getdate


DOCTYPE = "Event"
EVENT_FIELDS = {
	"subject",
	"starts_on",
	"ends_on",
	"all_day",
	"event_type",
	"event_category",
	"color",
	"description",
	"status",
	"send_reminder",
	"custom_店铺",
	"custom_物料",
}


def _datetime_text(value):
	if not value:
		return None
	return get_datetime(value).strftime("%Y-%m-%dT%H:%M:%S")


def _calendar_end(event):
	"""FullCalendar expects an exclusive end for all-day events."""
	if not cint(event.get("all_day")):
		return _datetime_text(event.get("ends_on"))

	end_value = event.get("ends_on") or event.get("starts_on")
	return add_days(getdate(end_value), 1).isoformat() if end_value else None


def _event_payload(event):
	name = event.get("name")
	starts_on = _datetime_text(event.get("starts_on"))
	is_repeating = cint(event.get("repeat_this_event"))
	can_write = bool(frappe.has_permission(DOCTYPE, "write", doc=name)) and not is_repeating
	can_delete = bool(frappe.has_permission(DOCTYPE, "delete", doc=name))
	color = event.get("color") or ("#16a34a" if event.get("event_type") == "Public" else "#2563eb")
	instance_id = f"{name}::{starts_on}" if is_repeating else name

	return {
		"id": instance_id,
		"title": event.get("subject") or _("未命名日程"),
		"start": starts_on,
		"end": _calendar_end(event),
		"allDay": bool(cint(event.get("all_day"))),
		"backgroundColor": color,
		"borderColor": color,
		"editable": can_write,
		"extendedProps": {
			"event_name": name,
			"description": event.get("description") or "",
			"event_type": event.get("event_type") or "Private",
			"repeat_this_event": is_repeating,
			"can_write": can_write,
			"can_delete": can_delete,
		},
	}


def _parse_values(values):
	if isinstance(values, str):
		values = frappe.parse_json(values)
	return frappe._dict(values or {})


def _apply_values(doc, values):
	values = _parse_values(values)
	for fieldname in EVENT_FIELDS:
		if fieldname in values:
			doc.set(fieldname, values.get(fieldname))

	if not doc.subject:
		frappe.throw(_("请输入日程标题。"))
	if not doc.starts_on:
		frappe.throw(_("请选择开始时间。"))
	if not doc.event_type:
		doc.event_type = "Private"
	if not doc.event_category:
		doc.event_category = "Event"
	if not doc.status:
		doc.status = "Open"


@frappe.whitelist()
def get_calendar_events(start, end):
	"""Return standard Event records, including Frappe's recurring occurrences."""
	rows = get_events(start=getdate(start), end=getdate(end))
	return [_event_payload(row) for row in rows]


@frappe.whitelist()
def get_event(name):
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	return {
		"name": doc.name,
		"subject": doc.subject,
		"starts_on": doc.starts_on,
		"ends_on": doc.ends_on,
		"all_day": cint(doc.all_day),
		"event_type": doc.event_type,
		"event_category": doc.event_category,
		"color": doc.color,
		"description": doc.description,
		"status": doc.status,
		"send_reminder": cint(doc.send_reminder),
		"custom_店铺": doc.get("custom_店铺") or "",
		"custom_物料": doc.get("custom_物料") or "",
		"repeat_this_event": cint(doc.repeat_this_event),
		"can_write": bool(doc.has_permission("write")),
		"can_delete": bool(doc.has_permission("delete")),
	}


@frappe.whitelist()
def create_event(values):
	frappe.has_permission(DOCTYPE, "create", throw=True)
	doc = frappe.new_doc(DOCTYPE)
	_apply_values(doc, values)
	doc.insert()
	return {"name": doc.name}


@frappe.whitelist()
def update_event(name, values):
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("write")
	_apply_values(doc, values)
	doc.save()
	return {"name": doc.name}


@frappe.whitelist()
def move_event(name, starts_on, ends_on=None, all_day=0):
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("write")
	if cint(doc.repeat_this_event):
		frappe.throw(_("重复日程需要在完整日程表单中修改。"))
	doc.starts_on = starts_on
	doc.ends_on = ends_on or None
	doc.all_day = cint(all_day)
	doc.save()
	return {"name": doc.name}


@frappe.whitelist()
def delete_event(name):
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("delete")
	frappe.delete_doc(DOCTYPE, doc.name)
	return {"name": name}
