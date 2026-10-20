import json
import re
from functools import lru_cache
from pathlib import Path

import frappe
from frappe import _
from frappe.desk.doctype.event.event import get_events
from frappe.utils import add_days, cint, get_datetime, getdate

from fengjing_app.fengjing_business.doctype.fengjin_calendar_holiday.holiday_sync import _country_map


DOCTYPE = "Event"
SETTINGS_DOCTYPE = "Fengjin Team Calendar Settings"
CITY_TIMEZONE_FILE = (Path(__file__).resolve().parents[3] / "public" / "data" / "Vector_map"
	/ "城市位置ne_10m_populated_places" / "ne_10m_populated_places.geojson")
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


def _settings_user():
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("请先登录后再修改日历设置。"), frappe.PermissionError)
	return user


def _normalize_map_entries(entries, kind):
	if not isinstance(entries, list) or len(entries) > 1000:
		frappe.throw(_("地区设置格式无效。"))
	fields = ("name",) if kind == "holiday" else ("country", "region", "city")
	pattern = r"\d+" if kind == "holiday" else r"\d+:\d+"
	result = []
	seen = set()
	for entry in entries:
		if not isinstance(entry, dict) or not isinstance(entry.get("key"), str):
			frappe.throw(_("地区设置格式无效。"))
		key = entry["key"]
		if not re.fullmatch(pattern, key):
			frappe.throw(_("地区设置的地图编号无效。"))
		values = {}
		for field in fields:
			value = entry.get(field)
			if not isinstance(value, str) or not value.strip() or len(value) > 200:
				frappe.throw(_("地区设置名称无效。"))
			values[field] = value.strip()
		if key not in seen:
			result.append({"key": key, **values})
			seen.add(key)
	return result


def _selected_default(value, entries):
	keys = {entry["key"] for entry in entries}
	return value if isinstance(value, str) and value in keys else (entries[0]["key"] if entries else "")


@lru_cache(maxsize=1)
def _city_timezones():
	with CITY_TIMEZONE_FILE.open(encoding="utf-8") as source:
		features = json.load(source)["features"]
	return {str(feature["properties"]["NE_ID"]): feature["properties"].get("TIMEZONE") or ""
		for feature in features}


@frappe.whitelist()
def get_calendar_map_settings():
	user = _settings_user()
	stored = frappe.db.get_value(
		SETTINGS_DOCTYPE, {"user": user},
		["holiday_countries_json", "weather_cities_json", "time_cities_json",
		 "default_holiday_country", "default_weather_city", "default_time_city"], as_dict=True,
	)
	if not stored:
		return {"holiday_countries": [], "weather_cities": [], "time_cities": [],
			"default_holiday_country": "", "default_weather_city": "", "default_time_city": ""}
	holiday_countries = _normalize_map_entries(
		frappe.parse_json(stored.holiday_countries_json or "[]"), "holiday"
	)
	weather_cities = _normalize_map_entries(
		frappe.parse_json(stored.weather_cities_json or "[]"), "weather"
	)
	time_cities = _normalize_map_entries(
		frappe.parse_json(stored.time_cities_json or "[]"), "time"
	)
	city_timezones = _city_timezones() if time_cities else {}
	country_codes = _country_map() if time_cities else {}
	return {
		"holiday_countries": holiday_countries,
		"weather_cities": weather_cities,
		"time_cities": [{**entry,
			"time_zone": city_timezones.get(entry["key"].split(":", 1)[1], ""),
			"country_code": country_codes.get(entry["key"].split(":", 1)[0], ("", ""))[0]}
			for entry in time_cities],
		"default_holiday_country": _selected_default(stored.default_holiday_country, holiday_countries),
		"default_weather_city": _selected_default(stored.default_weather_city, weather_cities),
		"default_time_city": "",
	}


@frappe.whitelist()
def get_calendar_holidays(start, end):
	"""Return the current user's selected national holidays for a month grid.

	Holiday dates are local civil dates, not timestamps to convert to the site timezone.
	The end date is exclusive, matching FullCalendar's visible range.
	"""
	user = _settings_user()
	start_date, end_date = getdate(start), getdate(end)
	if not 0 < (end_date - start_date).days <= 62:
		frappe.throw(_("节假日查询日期范围无效。"))
	stored = frappe.db.get_value(SETTINGS_DOCTYPE, {"user": user}, "holiday_countries_json")
	if not stored:
		return {"countries": [], "holidays": []}
	selected = _normalize_map_entries(frappe.parse_json(stored), "holiday")
	lookup = _country_map()
	countries = list(dict.fromkeys(lookup[entry["key"]][0] for entry in selected
		if entry["key"] in lookup
		and re.fullmatch(r"[A-Z]{2}", lookup[entry["key"]][0])))
	if not countries:
		return {"countries": [], "holidays": []}
	rows = frappe.get_all(
		"Fengjin Calendar Holiday",
		filters=[
			["country_code", "in", countries],
			["holiday_date", ">=", start_date],
			["holiday_date", "<", end_date],
			["active", "=", 1],
			["scope", "=", "全国"],
		],
		fields=["country_code", "country_name", "holiday_date", "day_type", "holiday_name"],
		order_by="holiday_date asc, country_code asc, holiday_name asc",
		limit_page_length=1000,
	)
	return {"countries": countries, "holidays": [
		{**row, "holiday_date": str(row.holiday_date)} for row in rows
	]}


@frappe.whitelist()
def save_calendar_map_settings(values):
	user = _settings_user()
	if isinstance(values, str) and len(values) > 200000:
		frappe.throw(_("地区设置过大。"))
	values = frappe.parse_json(values)
	if not isinstance(values, dict):
		frappe.throw(_("地区设置格式无效。"))
	name = frappe.db.get_value(SETTINGS_DOCTYPE, {"user": user}, "name")
	doc = frappe.get_doc(SETTINGS_DOCTYPE, name) if name else None
	holiday_countries = _normalize_map_entries(values.get("holiday_countries"), "holiday")
	weather_cities = _normalize_map_entries(values.get("weather_cities"), "weather")
	# Pages opened before this feature was deployed do not send time_cities.
	raw_time_cities = values.get("time_cities")
	if raw_time_cities is None:
		raw_time_cities = frappe.parse_json(doc.time_cities_json or "[]") if doc else []
	time_cities = _normalize_map_entries(raw_time_cities, "time")
	data = {
		"holiday_countries_json": json.dumps(holiday_countries, ensure_ascii=False),
		"weather_cities_json": json.dumps(weather_cities, ensure_ascii=False),
		"time_cities_json": json.dumps(time_cities, ensure_ascii=False),
		"default_holiday_country": _selected_default(values.get("default_holiday_country"), holiday_countries),
		"default_weather_city": _selected_default(values.get("default_weather_city"), weather_cities),
		"default_time_city": "",
	}
	if doc:
		doc.update(data)
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc({"doctype": SETTINGS_DOCTYPE, "user": user, **data})
		try:
			doc.insert(ignore_permissions=True)
		except frappe.DuplicateEntryError:
			# Another browser tab may have created this user's record first.
			doc = frappe.get_doc(SETTINGS_DOCTYPE, user)
			doc.update(data)
			doc.save(ignore_permissions=True)
	return {"name": doc.name}


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
