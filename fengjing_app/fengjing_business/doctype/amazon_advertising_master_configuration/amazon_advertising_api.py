# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""Amazon Ads API 请求、报告轮询和下载解析。"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import time
from datetime import date

import frappe
import requests
from frappe.utils import cint

from fengjing_app.fengjing_business.doctype.amazon_store_configuration.amazon_store_configuration import (
	RETRYABLE_STATUS_CODES,
	TOKEN_URL,
	_get_password,
)


ADS_ENDPOINTS = {
	"North America": "https://advertising-api.amazon.com",
	"Europe": "https://advertising-api-eu.amazon.com",
	"Far East": "https://advertising-api-fe.amazon.com",
}
RETRY_DELAYS = (2, 5, 15, 30, 60)


class AmazonAdvertisingAPIError(RuntimeError):
	def __init__(self, message, *, status_code=None, response_text=""):
		super().__init__(message)
		self.status_code = status_code
		self.response_text = response_text


def _advertising_credentials(store):
	"""优先读取广告专用凭证，三项未全部填写时完整回退到店铺凭证。"""
	master = getattr(store, "_advertising_master", None)
	if master:
		client_id = str(master.advertising_client_id or "").strip()
		client_secret = _get_password(master, "advertising_client_secret", raise_exception=False)
		refresh_token = _get_password(master, "advertising_refresh_token", raise_exception=False)
		provided = [bool(client_id), bool(client_secret), bool(refresh_token)]
		if any(provided) and not all(provided):
			raise ValueError("广告专用Client ID、Client Secret和Refresh Token必须同时填写。")
		if all(provided):
			return client_id, client_secret, refresh_token, f"master:{master.name}"
	return (
		str(store.client_id or "").strip(),
		_get_password(store, "client_secret"),
		_get_password(store, "refresh_token"),
		f"store:{store.name}",
	)


def get_advertising_access_token(store, force_refresh=False):
	client_id, client_secret, refresh_token, source = _advertising_credentials(store)
	if not client_id or not client_secret or not refresh_token:
		raise ValueError("Amazon Ads API凭证不完整。")
	fingerprint = hashlib.sha256(f"{client_id}|{refresh_token}".encode()).hexdigest()[:20]
	cache_key = f"fengjing:amazon-ads-access-token:{source}:{fingerprint}"
	if not force_refresh:
		cached = frappe.cache().get_value(cache_key)
		if cached:
			return str(cached)
	response = requests.post(
		TOKEN_URL,
		data={
			"grant_type": "refresh_token",
			"refresh_token": refresh_token,
			"client_id": client_id,
			"client_secret": client_secret,
		},
		timeout=30,
	)
	if response.status_code != 200:
		raise AmazonAdvertisingAPIError(
			f"Amazon Ads授权失败（HTTP {response.status_code}）：{response.text[:500]}",
			status_code=response.status_code,
			response_text=response.text[:500],
		)
	payload = response.json() or {}
	token = payload.get("access_token")
	if not token:
		raise AmazonAdvertisingAPIError("Amazon Ads授权成功但没有返回Access Token。")
	expires_in = max(cint(payload.get("expires_in")) - 300, 300)
	frappe.cache().set_value(cache_key, token, expires_in_sec=expires_in)
	return token


def endpoint_for_region(region):
	endpoint = ADS_ENDPOINTS.get(str(region or "").strip())
	if not endpoint:
		raise ValueError(f"不支持的Amazon Ads API区域：{region or '空'}")
	return endpoint


def advertising_request(
	store,
	region,
	method,
	path,
	*,
	profile_id=None,
	advertiser_id=None,
	json_body=None,
	params=None,
	headers=None,
	timeout=90,
	expected_statuses=(200,),
):
	"""调用Amazon Ads API，并统一处理令牌刷新、限流及错误信息。"""
	url = path if str(path).startswith("http") else f"{endpoint_for_region(region)}{path}"
	last_response = None
	force_refresh = False
	for attempt, delay in enumerate(RETRY_DELAYS):
		token = get_advertising_access_token(store, force_refresh=force_refresh)
		client_id, _client_secret, _refresh_token, _source = _advertising_credentials(store)
		force_refresh = False
		request_headers = {
			"Authorization": f"Bearer {token}",
			"Amazon-Advertising-API-ClientId": client_id,
			"Amazon-Ads-ClientId": client_id,
			"Accept": "application/json",
			"User-Agent": "FengjingAmazonAdvertising/1.0",
		}
		if profile_id:
			request_headers["Amazon-Advertising-API-Scope"] = str(profile_id)
		if advertiser_id:
			request_headers["Amazon-Ads-AccountId"] = str(advertiser_id)
		request_headers.update(headers or {})
		try:
			response = requests.request(
				method,
				url,
				headers=request_headers,
				params=params,
				json=json_body,
				timeout=timeout,
			)
		except requests.RequestException:
			if attempt == len(RETRY_DELAYS) - 1:
				raise
			time.sleep(delay)
			continue
		last_response = response
		if response.status_code == 401 and attempt == 0:
			force_refresh = True
			continue
		if response.status_code == 401:
			raise AmazonAdvertisingAPIError(
				"Amazon Ads API拒绝当前授权（HTTP 401）。请在广告总配置中填写已获Amazon Ads API权限的LWA凭证。",
				status_code=401,
				response_text=(response.text or "")[:2000],
			)
		if response.status_code in RETRYABLE_STATUS_CODES and attempt < len(RETRY_DELAYS) - 1:
			try:
				wait = max(float(response.headers.get("Retry-After") or 0), delay)
			except (TypeError, ValueError):
				wait = delay
			time.sleep(min(wait, 120))
			continue
		if response.status_code not in set(expected_statuses):
			text = (response.text or "")[:2000]
			raise AmazonAdvertisingAPIError(
				f"Amazon Ads API请求失败（HTTP {response.status_code}）：{text}",
				status_code=response.status_code,
				response_text=text,
			)
		return response
	if last_response is None:
		raise AmazonAdvertisingAPIError("Amazon Ads API没有返回响应。")
	raise AmazonAdvertisingAPIError(
		f"Amazon Ads API请求失败（HTTP {last_response.status_code}）：{last_response.text[:2000]}",
		status_code=last_response.status_code,
		response_text=last_response.text[:2000],
	)


def advertising_json(*args, **kwargs):
	response = advertising_request(*args, **kwargs)
	try:
		return response.json() or {}
	except ValueError as exc:
		raise AmazonAdvertisingAPIError("Amazon Ads API返回了无效JSON。") from exc


def list_profiles(store, region):
	"""读取当前LWA授权下的广告Profile。"""
	payload = advertising_json(store, region, "GET", "/v2/profiles")
	return payload if isinstance(payload, list) else payload.get("profiles") or payload.get("success") or []


def _response_items(payload, plural_name):
	value = payload.get(plural_name) if isinstance(payload, dict) else None
	if isinstance(value, list):
		return value
	if isinstance(value, dict):
		for key in ("success", "results", plural_name, "items"):
			if isinstance(value.get(key), list):
				return value[key]
	for key in ("success", "results", "items"):
		if isinstance(payload.get(key), list):
			return payload[key]
	return []


def iter_unified_entities(store, site, resource, *, ad_product=None, extra_body=None):
	"""分页读取统一Campaign Management API中的结构对象。"""
	next_token = None
	seen_tokens = set()
	while True:
		body = {"maxResults": 1000}
		if ad_product:
			body["adProductFilter"] = {"include": [ad_product]}
		if resource in {"campaigns", "adGroups", "ads", "targets"}:
			body["stateFilter"] = {"include": ["ENABLED", "PAUSED", "ARCHIVED"]}
		if next_token:
			body["nextToken"] = next_token
		body.update(extra_body or {})
		payload = advertising_json(
			store,
			site.master_api_region,
			"POST",
			f"/adsApi/v1/query/{resource}",
			profile_id=site.profile_id,
			advertiser_id=site.advertiser_id,
			json_body=body,
			headers={"Content-Type": "application/json"},
		)
		items = _response_items(payload, resource)
		yield items
		next_token = None
		if isinstance(payload, dict):
			next_token = payload.get("nextToken")
			if not next_token and isinstance(payload.get(resource), dict):
				next_token = payload[resource].get("nextToken")
		if not next_token:
			break
		if next_token in seen_tokens:
			raise AmazonAdvertisingAPIError(f"Amazon Ads {resource}接口返回重复分页标记。")
		seen_tokens.add(next_token)


def create_v3_report(store, site, definition, start_date, end_date):
	name = f"fengjing-{definition['key']}-{start_date}-{end_date}"
	body = {
		"name": name[:200],
		"startDate": _date_text(start_date),
		"endDate": _date_text(end_date),
		"configuration": {
			"adProduct": definition["ad_product"],
			"groupBy": definition["group_by"],
			"columns": definition["columns"],
			"reportTypeId": definition["report_type_id"],
			"timeUnit": "DAILY",
			"format": "GZIP_JSON",
		},
	}
	payload = advertising_json(
		store,
		site.master_api_region,
		"POST",
		"/reporting/reports",
		profile_id=site.profile_id,
		json_body=body,
		headers={"Content-Type": "application/vnd.createasyncreportrequest.v3+json"},
		expected_statuses=(200, 202),
	)
	report_id = payload.get("reportId") or payload.get("report_id")
	if not report_id:
		raise AmazonAdvertisingAPIError(f"Amazon广告报告创建成功但没有返回reportId：{payload}")
	return str(report_id)


def wait_for_v3_report(store, site, report_id, *, timeout_seconds=1200, poll_seconds=8):
	deadline = time.monotonic() + timeout_seconds
	while time.monotonic() < deadline:
		payload = advertising_json(
			store,
			site.master_api_region,
			"GET",
			f"/reporting/reports/{report_id}",
			profile_id=site.profile_id,
		)
		status = str(payload.get("status") or "").upper()
		if status in {"COMPLETED", "SUCCESS"}:
			url = payload.get("url") or payload.get("downloadUrl")
			if not url:
				raise AmazonAdvertisingAPIError(f"广告报告{report_id}完成但没有下载地址。")
			return payload
		if status in {"FAILURE", "FAILED", "CANCELLED"}:
			reason = payload.get("failureReason") or payload.get("statusDetails") or status
			raise AmazonAdvertisingAPIError(f"广告报告{report_id}生成失败：{reason}")
		time.sleep(max(poll_seconds, 2))
	raise AmazonAdvertisingAPIError(f"广告报告{report_id}等待超过{timeout_seconds}秒。")


def download_report_rows(url):
	response = requests.get(url, timeout=180)
	response.raise_for_status()
	content = response.content
	if content[:2] == b"\x1f\x8b":
		content = gzip.decompress(content)
	text = content.decode("utf-8-sig", errors="replace").strip()
	if not text:
		return []
	try:
		payload = json.loads(text)
		if isinstance(payload, list):
			return [row for row in payload if isinstance(row, dict)]
		if isinstance(payload, dict):
			for key in ("rows", "data", "results"):
				if isinstance(payload.get(key), list):
					return [row for row in payload[key] if isinstance(row, dict)]
			return [payload]
	except json.JSONDecodeError:
		return list(csv.DictReader(io.StringIO(text)))


def fetch_v3_report(store, site, definition, start_date, end_date):
	report_id = create_v3_report(store, site, definition, start_date, end_date)
	status = wait_for_v3_report(store, site, report_id)
	return report_id, download_report_rows(status.get("url") or status.get("downloadUrl"))


def _date_text(value):
	if isinstance(value, date):
		return value.isoformat()
	return str(value)[:10]
