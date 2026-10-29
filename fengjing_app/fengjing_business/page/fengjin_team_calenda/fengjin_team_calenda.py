import json
import re
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.desk.doctype.event.event import get_events
from frappe.utils import add_days, add_months, add_years, cint, get_datetime, getdate, now_datetime

from fengjing_app.fengjing_business.doctype.fengjin_calendar_holiday.holiday_sync import _country_map


DOCTYPE = "Event"
SETTINGS_DOCTYPE = "Fengjin Team Calendar Settings"
CITY_TIMEZONE_FILE = (Path(__file__).resolve().parents[3] / "public" / "data" / "Vector_map"
	/ "城市位置ne_10m_populated_places" / "ne_10m_populated_places.geojson")
CALENDAR_DEFAULTS_FILE = Path(__file__).with_name("calendar_defaults.json")
HOLIDAY_TRANSLATIONS_FILE = (Path(__file__).resolve().parents[3] / "data" / "calendar"
	/ "holiday_names_zh.json")
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
def _calendar_defaults():
	with CALENDAR_DEFAULTS_FILE.open(encoding="utf-8") as source:
		return json.load(source)


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
		 "default_holiday_country", "default_weather_city", "default_time_city",
		 "qweather_credential_id", "qweather_api_key", "qweather_api_host"], as_dict=True,
	)
	if not stored:
		defaults = _calendar_defaults()
		holiday_countries = _normalize_map_entries(defaults["holiday_countries"], "holiday")
		weather_cities = _normalize_map_entries(defaults["weather_cities"], "weather")
		time_cities = _normalize_map_entries(defaults["time_cities"], "time")
		city_timezones = _city_timezones()
		country_codes = _country_map()
		return {
			"holiday_countries": holiday_countries,
			"weather_cities": weather_cities,
			"time_cities": [{**entry,
				"time_zone": city_timezones.get(entry["key"].split(":", 1)[1], ""),
				"country_code": country_codes.get(entry["key"].split(":", 1)[0], ("", ""))[0]}
				for entry in time_cities],
			"default_holiday_country": _selected_default(defaults.get("default_holiday_country"), holiday_countries),
			"default_weather_city": _selected_default(defaults.get("default_weather_city"), weather_cities),
			"default_time_city": _selected_default(defaults.get("default_time_city"), time_cities),
			"qweather_credential_id": "", "qweather_api_key": "", "qweather_api_host": "",
		}
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
		"default_time_city": _selected_default(stored.default_time_city, time_cities),
		"qweather_credential_id": stored.qweather_credential_id or "",
		"qweather_api_key": stored.qweather_api_key or "",
		"qweather_api_host": stored.qweather_api_host or "",
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
	selected = (_normalize_map_entries(_calendar_defaults()["holiday_countries"], "holiday")
		if stored is None else _normalize_map_entries(frappe.parse_json(stored or "[]"), "holiday"))
	lookup = _country_map()
	country_names = {}
	for entry in selected:
		code, name = lookup.get(entry["key"], ("", ""))
		if re.fullmatch(r"[A-Z]{2}", code):
			country_names.setdefault(code, name)
	countries = list(country_names)
	if not countries:
		return {"countries": [], "holidays": [], "sync_pending": []}
	from fengjing_app.fengjing_business.doctype.fengjin_calendar_holiday.holiday_sync import ensure_holidays_for_view
	pending = ensure_holidays_for_view(country_names, start_date, end_date)
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
	with HOLIDAY_TRANSLATIONS_FILE.open(encoding="utf-8") as source:
		translations = json.load(source)
	return {"countries": countries, "holidays": [
		{
			**row,
			"holiday_date": str(row.holiday_date),
			"holiday_name_zh": translations.get(row.country_code, {}).get(
				row.holiday_name, row.holiday_name),
		} for row in rows
	], "sync_pending": pending}


@frappe.whitelist()
def get_calendar_weather(refresh=0):
	"""Read this user's cities; request a background refresh for stale data."""
	from fengjing_app.fengjing_business.doctype.fengjin_calendar_weather_forecast.weather_sync import (
		FORECAST_DOCTYPE, HISTORY_DOCTYPE, WEATHER_NAMES, selected_weather_cities, weather_needs_sync,
	)

	selected = selected_weather_cities(user=_settings_user())
	keys = list(selected)
	if not keys:
		return {"cities": []}
	if cint(refresh):
		for key in keys:
			forecast = weather_needs_sync(key)
			history = weather_needs_sync(key, history=True)
			if forecast or history:
				frappe.enqueue(
					"fengjing_app.fengjing_business.doctype.fengjin_calendar_weather_forecast.weather_sync.sync_city_weather",
					queue="short", city_key=key, forecast=forecast, history=history,
				)
	forecast_rows = frappe.get_all(FORECAST_DOCTYPE,
		filters={"city_key": ["in", keys], "forecast_date": [">=", add_days(getdate(), -1)]},
		fields=["city_key", "forecast_date", "fetched_at", "weather_code", "weather_description",
			"temperature_min_c", "temperature_max_c", "precipitation_probability_pct",
			"precipitation_sum_mm", "wind_speed_max_kmh", "current_temperature_c",
			"current_weather_code", "current_weather_description", "current_wind_speed_kmh", "current_is_day", "source_name"],
		order_by="fetched_at desc", limit_page_length=10000)
	history_rows = frappe.get_all(HISTORY_DOCTYPE,
		filters={"city_key": ["in", keys], "weather_date": [">=", "2026-10-01"]},
		fields=["city_key", "weather_date", "fetched_at", "weather_code", "weather_description",
			"temperature_min_c", "temperature_max_c", "precipitation_sum_mm", "wind_speed_max_kmh", "source_name"],
		order_by="fetched_at desc", limit_page_length=10000)
	result = {key: {"city_key": key, "days": [], "current": None} for key in keys}
	seen = set()
	for row in forecast_rows:
		day = str(row.forecast_date)
		local_today = datetime.now(ZoneInfo(selected[row.city_key]["timezone"])).date().isoformat()
		if day < local_today:
			continue
		identity = (row.city_key, day)
		if identity in seen:
			continue
		seen.add(identity)
		result[row.city_key]["days"].append({
			"date": day, "kind": "forecast", "weather_code": row.weather_code,
			"description": row.weather_description, "temperature_min_c": row.temperature_min_c,
			"temperature_max_c": row.temperature_max_c,
			"precipitation_probability_pct": row.precipitation_probability_pct,
			"precipitation_sum_mm": row.precipitation_sum_mm,
			"wind_speed_max_kmh": row.wind_speed_max_kmh,
			"source_name": row.source_name,
		})
		if (row.current_temperature_c is not None and result[row.city_key]["current"] is None
				and row.fetched_at and now_datetime() - get_datetime(row.fetched_at) <= timedelta(hours=6)
				and day == local_today):
			result[row.city_key]["current"] = {
				"temperature_c": row.current_temperature_c,
				"weather_code": row.current_weather_code,
				"description": row.current_weather_description or WEATHER_NAMES.get(row.current_weather_code, "当前天气"),
				"wind_speed_kmh": row.current_wind_speed_kmh,
				"source_name": row.source_name,
				"is_day": row.current_is_day,
				"fetched_at": str(row.fetched_at), "date": day,
			}
	for row in history_rows:
		day = str(row.weather_date)
		if day >= datetime.now(ZoneInfo(selected[row.city_key]["timezone"])).date().isoformat():
			continue
		identity = (row.city_key, day)
		if identity in seen:
			continue
		seen.add(identity)
		result[row.city_key]["days"].append({
			"date": day, "kind": "history", "weather_code": row.weather_code,
			"description": row.weather_description, "temperature_min_c": row.temperature_min_c,
			"temperature_max_c": row.temperature_max_c,
			"precipitation_sum_mm": row.precipitation_sum_mm,
			"wind_speed_max_kmh": row.wind_speed_max_kmh,
			"source_name": row.source_name,
		})
	return {"cities": list(result.values())}


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
	default_time_city = values.get("default_time_city")
	if default_time_city is None and doc:
		default_time_city = doc.default_time_city
	qweather_fields = {}
	for field in ("qweather_credential_id", "qweather_api_key", "qweather_api_host"):
		value = values.get(field, getattr(doc, field, "") if doc else "")
		if not isinstance(value, str) or len(value.strip()) > 140:
			frappe.throw(_("和风天气凭据格式无效。"))
		qweather_fields[field] = value.strip()
	if qweather_fields["qweather_api_host"] and not re.fullmatch(
			r"(?:[a-z0-9-]+\.)+qweatherapi\.com", qweather_fields["qweather_api_host"], re.I):
		frappe.throw(_("和风天气 API Host 必须是控制台提供的 qweatherapi.com 专属域名。"))
	data = {
		"holiday_countries_json": json.dumps(holiday_countries, ensure_ascii=False),
		"weather_cities_json": json.dumps(weather_cities, ensure_ascii=False),
		"time_cities_json": json.dumps(time_cities, ensure_ascii=False),
		"default_holiday_country": _selected_default(values.get("default_holiday_country"), holiday_countries),
		"default_weather_city": _selected_default(values.get("default_weather_city"), weather_cities),
		"default_time_city": _selected_default(default_time_city, time_cities),
		**qweather_fields,
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
	color = event.get("color") or "#7da7d4"
	if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
		color = "#7da7d4"
	red, green, blue = (int(color[index:index + 2], 16) for index in (1, 3, 5))
	contrast_color = "#111827" if (red * 299 + green * 587 + blue * 114) / 1000 >= 150 else "#ffffff"
	instance_id = f"{name}::{starts_on}" if is_repeating else name

	return {
		"id": instance_id,
		"title": event.get("subject") or _("未命名日程"),
		"start": starts_on,
		"end": _calendar_end(event),
		"allDay": bool(cint(event.get("all_day"))),
		"color": color,
		"contrastColor": contrast_color,
		"editable": can_write,
		"extendedProps": {
			"event_name": name,
			"status": event.get("status") or "Open",
			"description": event.get("description") or "",
			"event_type": event.get("event_type") or "Private",
			"repeat_this_event": is_repeating,
			"can_write": can_write,
			"can_delete": can_delete,
		},
	}


def _finished_event_occurrences(start, end):
	"""Include finished Events without changing Frappe's Open-only calendar API."""
	start_date, end_date = getdate(start), getdate(end)
	fields = [
		"name", "subject", "description", "color", "starts_on", "ends_on", "all_day",
		"event_type", "status", "repeat_this_event", "repeat_on", "repeat_till",
		"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
	]
	rows = frappe.get_list(
		DOCTYPE,
		filters={"status": ["in", ["Completed", "Closed"]], "starts_on": ["<=", f"{end_date} 23:59:59"]},
		fields=fields,
		limit_page_length=0,
	)
	occurrences = []
	weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
	for row in rows:
		first_day = getdate(row.starts_on)
		last_day = getdate(row.ends_on or row.starts_on)
		if not cint(row.repeat_this_event):
			if first_day <= end_date and last_day >= start_date:
				occurrences.append(row)
			continue

		repeat_till = getdate(row.repeat_till or "3000-01-01")
		if repeat_till < start_date:
			continue
		day = max(first_day, start_date)
		while day <= min(end_date, repeat_till):
			months = (day.year - first_day.year) * 12 + day.month - first_day.month
			if row.repeat_on == "Daily":
				matches = True
			elif row.repeat_on == "Weekly":
				matches = bool(cint(row.get(weekdays[day.weekday()])))
			elif row.repeat_on in ("Monthly", "Quarterly", "Half Yearly"):
				step = {"Monthly": 1, "Quarterly": 3, "Half Yearly": 6}[row.repeat_on]
				matches = months >= 0 and months % step == 0 and getdate(add_months(first_day, months)) == day
			elif row.repeat_on == "Yearly":
				years = day.year - first_day.year
				matches = years >= 0 and getdate(add_years(first_day, years)) == day
			else:
				matches = False
			if matches:
				instance = row.copy()
				instance.starts_on = datetime.combine(day, row.starts_on.time())
				if row.ends_on:
					instance.ends_on = datetime.combine(add_days(day, (row.ends_on - row.starts_on).days), row.ends_on.time())
				occurrences.append(instance)
			day = add_days(day, 1)
	return occurrences


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
	"""Return visible Event records, including finished and recurring Events."""
	rows = get_events(start=getdate(start), end=getdate(end))
	rows.extend(_finished_event_occurrences(start, end))
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
