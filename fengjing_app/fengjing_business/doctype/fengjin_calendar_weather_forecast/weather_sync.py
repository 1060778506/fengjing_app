"""Keep selected calendar cities' Open-Meteo weather in shared daily tables."""

import hashlib
import json
import re
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import frappe
import requests
from frappe.utils import get_datetime, now_datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from fengjing_app.fengjing_business.doctype.fengjin_calendar_holiday.holiday_sync import _country_map


SETTINGS_DOCTYPE = "Fengjin Team Calendar Settings"
FORECAST_DOCTYPE = "Fengjin Calendar Weather Forecast"
HISTORY_DOCTYPE = "Fengjin Calendar Historical Weather"
CITY_FILE = (Path(__file__).resolve().parents[3] / "public" / "data" / "Vector_map"
    / "城市位置ne_10m_populated_places" / "ne_10m_populated_places.geojson")
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HISTORY_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_FIELDS = "weather_code,temperature_2m_min,temperature_2m_max,precipitation_probability_max,precipitation_sum,wind_speed_10m_max"
HISTORY_FIELDS = "weather_code,temperature_2m_min,temperature_2m_max,temperature_2m_mean,precipitation_sum,rain_sum,snowfall_sum,wind_speed_10m_max"
CURRENT_FIELDS = "temperature_2m,weather_code,wind_speed_10m,is_day"
WEATHER_NAMES = {
    0: "晴", 1: "大部晴", 2: "局部多云", 3: "阴", 45: "雾", 48: "冻雾",
    51: "小毛毛雨", 53: "毛毛雨", 55: "强毛毛雨", 56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "较强阵雨", 82: "强阵雨", 85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷雨伴冰雹", 99: "强雷雨伴冰雹",
}


@lru_cache(maxsize=1)
def _cities():
    with CITY_FILE.open(encoding="utf-8") as source:
        features = json.load(source)["features"]
    return {str(item["properties"]["NE_ID"]): item for item in features}


def selected_weather_cities(user=None):
    """Return saved cities, or the shipped defaults for a new user or scheduler."""
    filters = {"user": user} if user else None
    rows = frappe.get_all(SETTINGS_DOCTYPE, filters=filters,
        fields=["weather_cities_json"], limit_page_length=10000)
    if not user or not rows:
        from fengjing_app.fengjing_business.page.fengjin_team_calenda.fengjin_team_calenda import _calendar_defaults
        rows.append({"weather_cities_json": json.dumps(_calendar_defaults()["weather_cities"], ensure_ascii=False)})
    cities = _cities()
    country_codes = _country_map()
    selected = {}
    for row in rows:
        try:
            entries = frappe.parse_json(row.get("weather_cities_json") or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("key") or "")
            if not re.fullmatch(r"\d+:\d+", key):
                continue
            feature = cities.get(key.split(":", 1)[1])
            if not feature or feature.get("geometry", {}).get("type") != "Point":
                continue
            longitude, latitude = feature["geometry"]["coordinates"][:2]
            zone = str(feature["properties"].get("TIMEZONE") or "")
            try:
                ZoneInfo(zone)
            except (ValueError, ZoneInfoNotFoundError):
                continue
            selected[key] = {
                "city_key": key, "country_code": country_codes.get(key.split(":", 1)[0], ("", ""))[0],
                "region_name": str(entry.get("region") or "")[:140],
                "city_name": str(entry.get("city") or "")[:140],
                "latitude": float(latitude), "longitude": float(longitude), "timezone": zone,
            }
    return selected


def _fetch(url, params):
    # Commercial plans use the same paths on the reserved customer hosts.
    api_key = (getattr(frappe, "conf", None) or {}).get("open_meteo_api_key")
    if api_key:
        url = url.replace("https://", "https://customer-", 1)
        params = {**params, "apikey": api_key}
    retry = Retry(total=2, connect=2, read=1, status=2, backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
    with requests.Session() as session:
        session.mount("https://", HTTPAdapter(max_retries=retry))
        try:
            response = session.get(url, params=params, timeout=(8, 25),
                headers={"Accept": "application/json"})
        except requests.RequestException as exc:
            raise ValueError(f"Open-Meteo 请求失败：{type(exc).__name__}") from None
        if response.status_code >= 400:
            raise ValueError(f"Open-Meteo 请求失败：HTTP {response.status_code}")
        if len(response.content) > 2_000_000:
            raise ValueError("Open-Meteo 天气数据超过允许大小")
        payload = response.json()
    if not isinstance(payload, dict) or payload.get("error"):
        raise ValueError("Open-Meteo 天气数据格式无效")
    return payload


def _daily_rows(payload, expected_days):
    daily = payload.get("daily")
    if not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
        raise ValueError("Open-Meteo 未返回每日天气")
    days = daily["time"]
    if len(days) != expected_days or len(set(days)) != expected_days:
        raise ValueError("Open-Meteo 返回的天气日期不完整")
    for values in daily.values():
        if not isinstance(values, list) or len(values) != expected_days:
            raise ValueError("Open-Meteo 每日天气字段不完整")
    return [{field: values[index] for field, values in daily.items()} for index in range(expected_days)]


def _record_key(kind, city_key, date):
    return hashlib.sha256(f"Open-Meteo:{kind}:{city_key}:{date}".encode()).hexdigest()


def _save_daily(doctype, values):
    name = frappe.db.get_value(doctype, {"record_key": values["record_key"]}, "name")
    if name:
        doc = frappe.get_doc(doctype, name)
        doc.update(values)
        doc.save(ignore_permissions=True)
    else:
        frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)


def _weather_name(code):
    return WEATHER_NAMES.get(code, "未知天气")


def _qweather_credentials(city_key):
    """Use a configured API key for a user who selected this city."""
    rows = frappe.get_all(SETTINGS_DOCTYPE,
        filters={"qweather_api_key": ["is", "set"], "qweather_api_host": ["is", "set"]},
        fields=["weather_cities_json", "qweather_api_key", "qweather_api_host"],
        limit_page_length=10000)
    for row in rows:
        try:
            cities = frappe.parse_json(row.weather_cities_json or "[]")
        except (TypeError, ValueError):
            continue
        if not any(isinstance(item, dict) and item.get("key") == city_key for item in cities):
            continue
        host = (row.qweather_api_host or "").strip().lower()
        if re.fullmatch(r"(?:[a-z0-9-]+\.)+qweatherapi\.com", host):
            return host, row.qweather_api_key
    return None


def _fetch_qweather(city, path, params=None):
    credentials = _qweather_credentials(city["city_key"])
    if not credentials:
        raise ValueError("未配置和风天气专属 API Host 与 API Key")
    host, api_key = credentials
    try:
        response = requests.get(f"https://{host}{path}", params=params or {},
            headers={"Accept": "application/json", "X-QW-Api-Key": api_key}, timeout=(8, 25))
        if response.status_code >= 400:
            raise ValueError(f"和风天气请求失败：HTTP {response.status_code}")
        if len(response.content) > 2_000_000:
            raise ValueError("和风天气响应超过允许大小")
        payload = response.json()
    except requests.RequestException as exc:
        raise ValueError(f"和风天气请求失败：{type(exc).__name__}") from None
    if not isinstance(payload, dict) or payload.get("code", "200") != "200":
        raise ValueError("和风天气响应格式无效")
    return payload


def _qweather_code(code):
    """Convert QWeather condition codes to the WMO codes used by the calendar."""
    try:
        value = int(code)
    except (TypeError, ValueError):
        return None
    if value == 100:
        return 0
    if value in (101, 102, 103):
        return 2
    if value == 104:
        return 3
    if 300 <= value < 400:
        return 95 if 302 <= value <= 304 else (65 if value in (307, 308, 310, 311, 312) else 61)
    if 400 <= value < 500:
        return 73
    if 500 <= value < 600:
        return 45
    return None


def _metric(data):
    return data.get("value") if isinstance(data, dict) else None


def _qweather_forecast(city, today):
    path = f"/weather/v1/daily/{city['latitude']:.2f}/{city['longitude']:.2f}"
    payload = _fetch_qweather(city, path, {"days": 7, "localTime": "true", "lang": "zh"})
    days = payload.get("days")
    if not isinstance(days, list) or len(days) < 7:
        raise ValueError("和风天气预报日期不完整")
    current = _fetch_qweather(city,
        f"/weather/v1/current/{city['latitude']:.2f}/{city['longitude']:.2f}",
        {"localTime": "true", "lang": "zh"})
    if not isinstance(current.get("condition"), dict) or _metric(current.get("temperature")) is None:
        raise ValueError("和风天气未返回当前天气")
    fetched_at = now_datetime()
    rows = []
    for index, day in enumerate(days[:7]):
        date = (today + timedelta(days=index)).isoformat()
        local_day = datetime.fromisoformat(day["forecastStartTime"].replace("Z", "+00:00"))
        if local_day.astimezone(ZoneInfo(city["timezone"])).date().isoformat() != date:
            raise ValueError("和风天气预报日期与当地日期不符")
        condition = day.get("daytime", {}).get("condition") or {}
        precipitation = day.get("daytime", {}).get("precipitation") or {}
        wind = day.get("daytime", {}).get("wind") or {}
        wind_speed = _metric(wind.get("speed"))
        wind_unit = (wind.get("speed") or {}).get("unit") if isinstance(wind.get("speed"), dict) else ""
        if wind_speed is not None and wind_unit == "m/s":
            wind_speed = round(float(wind_speed) * 3.6, 2)
        probability = precipitation.get("probability")
        rows.append({
            **city, "forecast_date": date, "fetched_at": fetched_at,
            "weather_code": _qweather_code(condition.get("code")),
            "weather_description": condition.get("text") or "未知天气",
            "temperature_min_c": _metric(day.get("temperatureMin")),
            "temperature_max_c": _metric(day.get("temperatureMax")),
            "precipitation_probability_pct": round(float(probability) * 100) if probability is not None else None,
            "precipitation_sum_mm": _metric(precipitation.get("amount")),
            "wind_speed_max_kmh": wind_speed, "source_name": "QWeather",
            "model_name": "QWeather v1", "record_key": _record_key("forecast", city["city_key"], date),
            "raw_json": json.dumps({"daily": day, "current": current if index == 0 else None}, ensure_ascii=False),
        })
    condition = current["condition"]
    speed = _metric((current.get("wind") or {}).get("speed"))
    if speed is not None and ((current.get("wind") or {}).get("speed") or {}).get("unit") == "m/s":
        speed = round(float(speed) * 3.6, 2)
    rows[0].update({
        "current_temperature_c": _metric(current.get("temperature")),
        "current_weather_code": _qweather_code(condition.get("code")),
        "current_weather_description": condition.get("text") or "当前天气",
        "current_wind_speed_kmh": speed,
        "current_is_day": int(6 <= datetime.now(ZoneInfo(city["timezone"])).hour < 18),
    })
    for row in rows:
        _save_daily(FORECAST_DOCTYPE, row)
    return len(rows)


def _sync_open_meteo_forecast(city, today):
    payload = _fetch(FORECAST_URL, {
        "latitude": city["latitude"], "longitude": city["longitude"],
        "timezone": city["timezone"], "forecast_days": 7,
        "daily": FORECAST_FIELDS, "current": CURRENT_FIELDS,
    })
    rows = _daily_rows(payload, 7)
    if rows[0]["time"] != today.isoformat():
        raise ValueError("Open-Meteo 返回的预报日期与城市当地日期不符")
    current = payload.get("current") or {}
    fetched_at = now_datetime()
    for row in rows:
        day = row["time"]
        code = row.get("weather_code")
        values = {
            **city, "forecast_date": day, "fetched_at": fetched_at,
            "weather_code": code, "weather_description": _weather_name(code),
            "temperature_min_c": row.get("temperature_2m_min"),
            "temperature_max_c": row.get("temperature_2m_max"),
            "precipitation_probability_pct": row.get("precipitation_probability_max"),
            "precipitation_sum_mm": row.get("precipitation_sum"),
            "wind_speed_max_kmh": row.get("wind_speed_10m_max"),
            "source_name": "Open-Meteo", "model_name": "best_match",
            "raw_json": json.dumps({"daily": row, "current": current if day == today.isoformat() else None}, ensure_ascii=False),
            "record_key": _record_key("forecast", city["city_key"], day),
        }
        if day == today.isoformat():
            values.update({
                "current_temperature_c": current.get("temperature_2m"),
                "current_weather_code": current.get("weather_code"),
                "current_weather_description": _weather_name(current.get("weather_code")),
                "current_wind_speed_kmh": current.get("wind_speed_10m"),
                "current_is_day": current.get("is_day"),
            })
        _save_daily(FORECAST_DOCTYPE, values)
    return len(rows)


def _sync_open_meteo_history(city, start, end):
    payload = _fetch(HISTORY_URL, {
        "latitude": city["latitude"], "longitude": city["longitude"],
        "timezone": city["timezone"], "start_date": start.isoformat(), "end_date": end.isoformat(),
        "daily": HISTORY_FIELDS,
    })
    rows = _daily_rows(payload, (end - start).days + 1)
    if rows[0]["time"] != start.isoformat() or rows[-1]["time"] != end.isoformat():
        raise ValueError("Open-Meteo 返回的历史天气日期不完整")
    fetched_at = now_datetime()
    for row in rows:
        day = row["time"]
        code = row.get("weather_code")
        _save_daily(HISTORY_DOCTYPE, {
            **city, "weather_date": day, "data_kind": "历史补取",
            "temperature_min_c": row.get("temperature_2m_min"),
            "temperature_max_c": row.get("temperature_2m_max"),
            "temperature_mean_c": row.get("temperature_2m_mean"),
            "weather_code": code, "weather_description": _weather_name(code),
            "precipitation_sum_mm": row.get("precipitation_sum"),
            "rain_sum_mm": row.get("rain_sum"), "snowfall_sum_cm": row.get("snowfall_sum"),
            "wind_speed_max_kmh": row.get("wind_speed_10m_max"),
            "source_name": "Open-Meteo", "source_model": "best_match",
            "fetched_at": fetched_at, "raw_json": json.dumps(row, ensure_ascii=False),
            "record_key": _record_key("history", city["city_key"], day),
        })
    return len(rows)


def _sync_qweather_history(city, start, end):
    if start < datetime.now(ZoneInfo(city["timezone"])).date() - timedelta(days=10):
        raise ValueError("和风天气常规历史接口只支持最近10天")
    location = f"{city['longitude']:.2f},{city['latitude']:.2f}"
    geo = _fetch_qweather(city, "/geo/v2/city/lookup", {"location": location, "number": 1})
    locations = geo.get("location") or []
    if not locations or not locations[0].get("id"):
        raise ValueError("和风天气未找到城市 LocationID")
    location_id = locations[0]["id"]
    fetched_at = now_datetime()
    count = 0
    day = start
    while day <= end:
        payload = _fetch_qweather(city, "/v7/historical/weather",
            {"location": location_id, "date": day.strftime("%Y%m%d"), "lang": "zh"})
        daily = payload.get("weatherDaily") or {}
        if daily.get("date") != day.isoformat():
            raise ValueError("和风天气历史日期不匹配")
        hourly = payload.get("weatherHourly") or []
        middle = hourly[len(hourly) // 2] if hourly else {}
        code = _qweather_code(middle.get("icon"))
        _save_daily(HISTORY_DOCTYPE, {
            **city, "weather_date": day.isoformat(), "data_kind": "历史补取",
            "temperature_min_c": daily.get("tempMin"), "temperature_max_c": daily.get("tempMax"),
            "temperature_mean_c": None, "weather_code": code,
            "weather_description": middle.get("text") or _weather_name(code),
            "precipitation_sum_mm": daily.get("precip"),
            "wind_speed_max_kmh": max((float(item.get("windSpeed") or 0) for item in hourly), default=0),
            "source_name": "QWeather", "source_model": "QWeather v7 historical",
            "fetched_at": fetched_at, "raw_json": json.dumps(payload, ensure_ascii=False),
            "record_key": _record_key("history", city["city_key"], day.isoformat()),
        })
        count += 1
        day += timedelta(days=1)
    return count


def _sync_forecast(city, today):
    try:
        return _sync_open_meteo_forecast(city, today)
    except (ValueError, requests.RequestException) as primary_error:
        try:
            return _qweather_forecast(city, today)
        except (ValueError, requests.RequestException) as fallback_error:
            raise ValueError(f"Open-Meteo 不可用（{primary_error}）；和风天气不可用（{fallback_error}）") from None


def _sync_history(city, today):
    start, end = today - timedelta(days=10), today - timedelta(days=1)
    try:
        return _sync_open_meteo_history(city, start, end)
    except (ValueError, requests.RequestException) as primary_error:
        try:
            return _sync_qweather_history(city, start, end)
        except (ValueError, requests.RequestException) as fallback_error:
            raise ValueError(f"Open-Meteo 不可用（{primary_error}）；和风天气不可用（{fallback_error}）") from None


def backfill_october_2026_weather(city_key=None):
    """One-time backfill through yesterday; regular jobs still revisit only ten days."""
    results = []
    for city in selected_weather_cities().values():
        if city_key and city["city_key"] != city_key:
            continue
        today = datetime.now(ZoneInfo(city["timezone"])).date()
        start = datetime(2026, 10, 1).date()
        end = today - timedelta(days=1)
        if end < start:
            continue
        savepoint = "calendar_weather_october_backfill"
        frappe.db.savepoint(savepoint)
        try:
            count = _sync_open_meteo_history(city, start, end)
            results.append({"city_key": city["city_key"], "saved": count})
        except Exception:
            frappe.db.rollback(save_point=savepoint)
            frappe.log_error(title=f"日历十月天气补齐失败：{city['city_key']}",
                message=frappe.get_traceback())
            results.append({"city_key": city["city_key"], "saved": 0, "status": "失败"})
    return results


def weather_needs_sync(city_key, history=False):
    doctype = HISTORY_DOCTYPE if history else FORECAST_DOCTYPE
    city = selected_weather_cities().get(city_key)
    if not city:
        return False
    today = datetime.now(ZoneInfo(city["timezone"])).date()
    target = today - timedelta(days=1) if history else today
    fetched = frappe.db.get_value(doctype, {"record_key": _record_key(
        "history" if history else "forecast", city_key, target.isoformat())}, "fetched_at")
    if not fetched:
        return True
    return now_datetime() - get_datetime(fetched) >= timedelta(hours=24 if history else 3)


def sync_city_weather(city_key, forecast=True, history=True):
    """Refresh a configured city; one failed endpoint does not discard the other."""
    city = selected_weather_cities().get(city_key)
    if not city:
        return {"city_key": city_key, "status": "未选择此城市"}
    today = datetime.now(ZoneInfo(city["timezone"])).date()
    result = {"city_key": city_key}
    for kind, requested, sync in (("forecast", forecast, _sync_forecast), ("history", history, _sync_history)):
        if not requested:
            continue
        savepoint = f"calendar_weather_{kind}"
        frappe.db.savepoint(savepoint)
        try:
            result[kind] = sync(city, today)
        except Exception:
            frappe.db.rollback(save_point=savepoint)
            frappe.log_error(title=f"日历天气同步失败：{kind} {city_key}",
                message=frappe.get_traceback())
            result[kind] = "失败"
    return result


def sync_selected_weather():
    """Hourly scheduler entry; forecast refreshes every 3h, history every 24h."""
    results = []
    for city_key in selected_weather_cities():
        forecast = weather_needs_sync(city_key)
        history = weather_needs_sync(city_key, history=True)
        if forecast or history:
            results.append(sync_city_weather(city_key, forecast=forecast, history=history))
    return results
