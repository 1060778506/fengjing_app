"""Sync selected countries' public holidays into the shared calendar table."""

import hashlib
import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

import frappe
import requests
from frappe import _
from frappe.utils import getdate, now_datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


HOLIDAY_DOCTYPE = "Fengjin Calendar Holiday"
SETTINGS_DOCTYPE = "Fengjin Team Calendar Settings"
NAGER_URL = "https://nagerholidays.com/api/v4/Holidays/{country}/{year}"
CHINA_URL = "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json"
COUNTRY_MAP = (Path(__file__).resolve().parents[3] / "public" / "data" / "Vector_map"
	/ "国家边界ne_10m_admin_0_countries_chn" / "ne_10m_admin_0_countries_chn.geojson")
REQUEST_TIMEOUT = (8, 30)


@lru_cache(maxsize=1)
def _country_map():
	with COUNTRY_MAP.open(encoding="utf-8") as source:
		features = json.load(source)["features"]
	return {
		str(feature["properties"]["NE_ID"]): (
			str(feature["properties"].get("ISO_A2") or "").upper(),
			str(feature["properties"].get("name") or ""),
		)
		for feature in features
	}


def _selected_countries():
	lookup = _country_map()
	selected = {}
	for settings in frappe.get_all(SETTINGS_DOCTYPE, fields=["holiday_countries_json"]):
		try:
			countries = frappe.parse_json(settings.holiday_countries_json or "[]")
		except (TypeError, ValueError):
			continue
		if not isinstance(countries, list):
			continue
		for country in countries:
			if not isinstance(country, dict):
				continue
			code, name = lookup.get(str(country.get("key")), ("", ""))
			if re.fullmatch(r"[A-Z]{2}", code):
				selected[code] = name
	return selected


def _fetch_json(url):
	with requests.Session() as session:
		retry = Retry(total=2, connect=2, read=1, status=2, backoff_factor=0.5,
			status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
		session.mount("https://", HTTPAdapter(max_retries=retry))
		response = session.get(url, timeout=REQUEST_TIMEOUT, headers={"Accept": "application/json"})
		response.raise_for_status()
		if len(response.content) > 2_000_000:
			raise ValueError("节假日数据超过允许大小")
		return response.json()


def _valid_date(value, year):
	if not isinstance(value, str):
		raise ValueError("节假日日期无效")
	day = date.fromisoformat(value)
	if day.year != year:
		raise ValueError("节假日日期超出请求年份")
	return day.isoformat()


def _text(value, limit=140):
	return value.strip()[:limit] if isinstance(value, str) else ""


def _parse_china(payload, year, country_name):
	if not isinstance(payload, dict) or payload.get("year") != year or not isinstance(payload.get("days"), list):
		raise ValueError("中国节假日数据格式无效")
	papers = payload.get("papers") or []
	if not isinstance(papers, list):
		raise ValueError("中国节假日来源格式无效")
	rows = []
	for item in payload["days"]:
		if not isinstance(item, dict) or not isinstance(item.get("isOffDay"), bool):
			raise ValueError("中国节假日记录格式无效")
		name = _text(item.get("name"))
		if not name:
			raise ValueError("中国节假日名称为空")
		rows.append({
			"country_code": "CN", "country_name": country_name,
			"holiday_date": _valid_date(item.get("date"), year),
			"day_type": "节假日" if item["isOffDay"] else "补班工作日",
			"holiday_name": name, "local_name": name, "english_name": "",
			"scope": "全国", "region_code": "", "region_name": "",
			"source_name": "holiday-cn",
			"source_url": CHINA_URL.format(year=year),
			"notes": "国务院公告：" + "；".join(_text(url, 250) for url in papers if isinstance(url, str)),
		})
	return rows


def _parse_nager(payload, country_code, year, country_name):
	if not isinstance(payload, list):
		raise ValueError("Nager.Date 节假日数据格式无效")
	rows = []
	for item in payload:
		if not isinstance(item, dict):
			raise ValueError("Nager.Date 节假日记录格式无效")
		if item.get("nationalHoliday") is not True:
			continue
		types = item.get("holidayTypes") or []
		if not isinstance(types, list):
			raise ValueError("Nager.Date 节假日类型格式无效")
		if types and "Public" not in types:
			continue
		if item.get("countryCode", "").upper() != country_code:
			raise ValueError("Nager.Date 返回的国家与请求不一致")
		name = _text(item.get("name"))
		if not name:
			raise ValueError("Nager.Date 节假日名称为空")
		rows.append({
			"country_code": country_code, "country_name": country_name,
			"holiday_date": _valid_date(item.get("date"), year),
			"day_type": "节假日", "holiday_name": name,
			"local_name": name, "english_name": name,
			"scope": "全国", "region_code": "", "region_name": "",
			"source_name": "Nager.Date",
			"source_url": NAGER_URL.format(country=country_code, year=year),
			"notes": "",
		})
	return rows


def _record_key(row):
	identity = [row[field] for field in (
		"source_name", "country_code", "holiday_date", "day_type", "region_code", "holiday_name"
	)]
	return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()


def _save_rows(rows, country_code, year, source_name):
	"""Upsert a complete source response; retain withdrawn records as inactive."""
	if not rows:
		return {"status": "暂无已公布数据", "count": 0}
	frappe.db.savepoint("calendar_holiday_sync")
	updated_at = now_datetime()
	keys = set()
	try:
		for row in rows:
			key = _record_key(row)
			keys.add(key)
			name = frappe.db.get_value(HOLIDAY_DOCTYPE, {"record_key": key}, "name")
			values = {**row, "record_key": key, "source_updated_at": updated_at, "active": 1}
			if name:
				doc = frappe.get_doc(HOLIDAY_DOCTYPE, name)
				doc.update(values)
				doc.save(ignore_permissions=True)
			else:
				frappe.get_doc({"doctype": HOLIDAY_DOCTYPE, "verification_status": "待核对", **values}).insert(
					ignore_permissions=True
				)
		for old in frappe.get_all(HOLIDAY_DOCTYPE, filters={
			"country_code": country_code, "source_name": source_name,
			"holiday_date": ["between", [f"{year}-01-01", f"{year}-12-31"]],
			"active": 1,
		}, fields=["name", "record_key"]):
			if old.record_key and old.record_key not in keys:
				frappe.db.set_value(HOLIDAY_DOCTYPE, old.name, "active", 0)
	except Exception:
		frappe.db.rollback(save_point="calendar_holiday_sync")
		raise
	return {"status": "已同步", "count": len(keys)}


def sync_country_holidays(country_code, year, country_name=""):
	"""Fetch one country/year. An incomplete or failed response never replaces saved data."""
	country_code = str(country_code or "").upper()
	if not re.fullmatch(r"[A-Z]{2}", country_code):
		frappe.throw(_("国家代码必须是两个英文字母。"))
	year = int(year)
	if not 2000 <= year <= 2100:
		frappe.throw(_("节假日年份超出允许范围。"))
	try:
		if country_code == "CN":
			payload = _fetch_json(CHINA_URL.format(year=year))
			rows = _parse_china(payload, year, _text(country_name) or "中华人民共和国")
			source_name = "holiday-cn"
		else:
			payload = _fetch_json(NAGER_URL.format(country=country_code, year=year))
			rows = _parse_nager(payload, country_code, year, _text(country_name))
			source_name = "Nager.Date"
	except requests.HTTPError as exc:
		if exc.response is not None and exc.response.status_code == 404:
			return {"country_code": country_code, "year": year, "status": "数据源暂无该年份", "count": 0}
		raise
	result = _save_rows(rows, country_code, year, source_name)
	return {"country_code": country_code, "year": year, **result}


@frappe.whitelist()
def sync_holidays_now(country_code, year):
	"""Administrator-triggered sync for a specific country/year."""
	frappe.only_for("System Manager")
	return sync_country_holidays(country_code, year)


def sync_selected_holidays():
	"""Daily sync for countries selected by at least one calendar user."""
	year = getdate().year
	results = []
	for country_code, country_name in _selected_countries().items():
		for target_year in (year, year + 1):
			try:
				results.append(sync_country_holidays(country_code, target_year, country_name))
			except (requests.RequestException, ValueError, KeyError) as exc:
				frappe.log_error(
					title=f"日历节假日同步失败：{country_code} {target_year}",
					message=str(exc),
				)
	return results
