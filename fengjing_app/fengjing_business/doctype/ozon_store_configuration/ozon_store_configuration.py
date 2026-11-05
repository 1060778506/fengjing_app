# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""Ozon store configuration and shared Seller/Performance API clients.

Order, ranking, price and finance modules should reuse the request helpers in
this module instead of reading credentials or implementing retries themselves.
"""

import hashlib
import json
import time

import frappe
import requests
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime


DOCTYPE = "Ozon Store Configuration"
SELLER_API_ENDPOINT = "https://api-seller.ozon.ru"
PERFORMANCE_API_ENDPOINT = "https://api-performance.ozon.ru"
PERFORMANCE_TOKEN_PATH = "/api/client/token"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
RETRY_DELAYS = (2, 5, 15, 30, 60, 120)


class OzonStoreConfiguration(Document):
	def validate(self):
		self.store_name = str(self.store_name or "").strip()
		self.ozon_id = str(self.ozon_id or "").strip()
		self.performance_client_id = str(self.performance_client_id or "").strip()

		if cint(self.enabled):
			missing = []
			for fieldname, label in (
				("store_name", _("Store Name")),
				("cost_center", _("Cost Center")),
				("company", _("Company")),
				("country", _("Country")),
				("ozon_id", _("Ozon ID")),
			):
				if not self.get(fieldname):
					missing.append(label)
			if not _get_password(self, "seller_api_key", raise_exception=False):
				missing.append(_("Seller API Key"))
			if missing:
				frappe.throw(_("Enabled Ozon store is missing: {0}").format(", ".join(missing)))

		performance_key = _get_password(self, "performance_api_key", raise_exception=False)
		if bool(self.performance_client_id) != bool(performance_key):
			frappe.throw(
				_("Performance Client ID and Performance API Key must either both be filled or both be empty")
			)


def ensure_database_connection():
	"""Reconnect after long API waits if the MariaDB connection has expired."""
	try:
		frappe.db.sql("select 1")
	except Exception:
		try:
			frappe.db.close()
		except Exception:
			pass
		frappe.connect()


def _get_password(store, fieldname, raise_exception=True):
	field = store.meta.get_field(fieldname) if getattr(store, "meta", None) else None
	if not field or field.fieldtype != "Password":
		return str(store.get(fieldname) or "").strip()
	try:
		return store.get_password(fieldname, raise_exception=raise_exception) or ""
	except TypeError:
		try:
			return store.get_password(fieldname) or ""
		except Exception:
			if raise_exception:
				raise
			return ""
	except Exception:
		if raise_exception:
			raise
		return ""


def get_store(store_name, require_enabled=True):
	if not store_name:
		raise ValueError("Ozon configuration is not linked to a store")
	store = frappe.get_doc(DOCTYPE, store_name)
	if require_enabled and not cint(store.enabled):
		raise ValueError(f"Ozon store {store.store_name or store.name} is disabled")
	if not str(store.ozon_id or "").strip():
		raise ValueError(f"Ozon store {store.store_name or store.name} has no Ozon ID")
	if not _get_password(store, "seller_api_key", raise_exception=False):
		raise ValueError(f"Ozon store {store.store_name or store.name} has no Seller API Key")
	return store


def response_error_summary(response, limit=1000):
	if response is None:
		return "No response"
	try:
		payload = response.json()
		content = (
			payload.get("message")
			or payload.get("error_description")
			or payload.get("error")
			or payload
			if isinstance(payload, dict)
			else payload
		)
		if isinstance(content, (dict, list)):
			content = json.dumps(content, ensure_ascii=False)
		return str(content)[:limit]
	except (ValueError, TypeError, AttributeError):
		return str(response.text or "No response content")[:limit]


def _credential_fingerprint(store, service):
	if service == "performance":
		value = "|".join(
			(
				str(store.performance_client_id or "").strip(),
				_get_password(store, "performance_api_key", raise_exception=False),
			)
		)
	else:
		value = "|".join(
			(
				str(store.ozon_id or "").strip(),
				_get_password(store, "seller_api_key", raise_exception=False),
			)
		)
	return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _request_with_retry(method, url, *, headers=None, json_data=None, params=None, timeout=60, max_retries=None):
	retry_count = len(RETRY_DELAYS) - 1 if max_retries is None else max(cint(max_retries), 0)
	attempt_count = min(retry_count + 1, len(RETRY_DELAYS))
	last_response = None
	for attempt, delay in enumerate(RETRY_DELAYS[:attempt_count]):
		try:
			response = requests.request(
				method,
				url,
				headers=headers,
				json=json_data,
				params=params,
				timeout=timeout,
			)
		except requests.RequestException:
			if attempt == attempt_count - 1:
				raise
			time.sleep(delay)
			continue
		last_response = response
		if response.status_code not in RETRYABLE_STATUS_CODES or attempt == attempt_count - 1:
			return response
		wait_seconds = delay
		if response.status_code == 429:
			try:
				wait_seconds = max(float(response.headers.get("Retry-After")), delay)
			except (TypeError, ValueError):
				pass
		time.sleep(min(wait_seconds, 300))
	return last_response


def ozon_seller_request(
	store,
	method,
	path,
	*,
	json_data=None,
	params=None,
	timeout=60,
	max_retries=None,
):
	"""Send an authenticated Ozon Seller API request with shared retry rules."""
	client_id = str(store.ozon_id or "").strip()
	api_key = _get_password(store, "seller_api_key")
	if not client_id or not api_key:
		raise ValueError(f"Ozon store {store.store_name or store.name} has incomplete Seller API credentials")
	url = path if str(path).startswith("http") else f"{SELLER_API_ENDPOINT}{path}"
	return _request_with_retry(
		method,
		url,
		headers={
			"Client-Id": client_id,
			"Api-Key": api_key,
			"Accept": "application/json",
			"Content-Type": "application/json",
		},
		json_data=json_data,
		params=params,
		timeout=timeout,
		max_retries=max_retries,
	)


def _performance_token_cache_key(store):
	return f"fengjing:ozon-performance-token:{store.name}:{_credential_fingerprint(store, 'performance')}"


def invalidate_performance_access_token(store):
	frappe.cache().delete_value(_performance_token_cache_key(store))


def get_performance_access_token(store, force_refresh=False):
	"""Return a cached Performance API token for ranking/advertising modules."""
	cache_key = _performance_token_cache_key(store)
	if not force_refresh:
		cached = frappe.cache().get_value(cache_key)
		if cached:
			return str(cached)

	client_id = str(store.performance_client_id or "").strip()
	client_secret = _get_password(store, "performance_api_key")
	if not client_id or not client_secret:
		raise ValueError(f"Ozon store {store.store_name or store.name} has incomplete Performance API credentials")

	response = _request_with_retry(
		"POST",
		f"{PERFORMANCE_API_ENDPOINT}{PERFORMANCE_TOKEN_PATH}",
		headers={"Accept": "application/json", "Content-Type": "application/json"},
		json_data={
			"client_id": client_id,
			"client_secret": client_secret,
			"grant_type": "client_credentials",
		},
		timeout=30,
	)
	if response is None or response.status_code != 200:
		status = response.status_code if response is not None else "no response"
		raise RuntimeError(
			f"Ozon Performance authorization failed (HTTP {status}): {response_error_summary(response)}"
		)
	payload = response.json() or {}
	token = payload.get("access_token")
	if not token:
		raise RuntimeError("Ozon Performance authorization succeeded but returned no access token")
	expires_in = max(cint(payload.get("expires_in")) - 300, 300)
	frappe.cache().set_value(cache_key, token, expires_in_sec=expires_in)
	return token


def ozon_performance_request(
	store,
	method,
	path,
	*,
	json_data=None,
	params=None,
	timeout=60,
	max_retries=None,
):
	"""Send an Ozon Performance API request and refresh an expired token once."""
	url = path if str(path).startswith("http") else f"{PERFORMANCE_API_ENDPOINT}{path}"
	response = None
	for force_refresh in (False, True):
		token = get_performance_access_token(store, force_refresh=force_refresh)
		response = _request_with_retry(
			method,
			url,
			headers={
				"Authorization": f"Bearer {token}",
				"Accept": "application/json",
				"Content-Type": "application/json",
			},
			json_data=json_data,
			params=params,
			timeout=timeout,
			max_retries=max_retries,
		)
		if response is None or response.status_code not in {401, 403} or force_refresh:
			return response
		invalidate_performance_access_token(store)
	return response


@frappe.whitelist()
def test_ozon_store_api(name):
	"""Test configured APIs only when the user explicitly clicks the test button."""
	store = get_store(name, require_enabled=False)
	results = []
	errors = []

	try:
		response = ozon_seller_request(
			store,
			"POST",
			"/v3/product/list",
			json_data={"filter": {"visibility": "ALL"}, "limit": 1},
			timeout=30,
			max_retries=1,
		)
		if response is not None and response.ok:
			results.append({"name": "Seller API", "status": "Available", "message": "Connection succeeded"})
		else:
			status = response.status_code if response is not None else "no response"
			message = f"HTTP {status}: {response_error_summary(response, 500)}"
			results.append({"name": "Seller API", "status": "Unavailable", "message": message})
			errors.append(f"Seller API: {message}")
	except Exception as exc:
		message = str(exc)[:500]
		results.append({"name": "Seller API", "status": "Unavailable", "message": message})
		errors.append(f"Seller API: {message}")

	performance_status = "Not Tested"
	if store.performance_client_id and _get_password(store, "performance_api_key", raise_exception=False):
		try:
			get_performance_access_token(store, force_refresh=True)
			performance_status = "Available"
			results.append(
				{"name": "Performance API", "status": "Available", "message": "Authentication succeeded"}
			)
		except Exception as exc:
			performance_status = "Unavailable"
			message = str(exc)[:500]
			results.append({"name": "Performance API", "status": "Unavailable", "message": message})
			errors.append(f"Performance API: {message}")
	else:
		results.append(
			{"name": "Performance API", "status": "Not Tested", "message": "Credentials are not configured"}
		)

	seller_status = results[0]["status"]
	frappe.db.set_value(
		DOCTYPE,
		store.name,
		{
			"seller_api_status": seller_status,
			"performance_api_status": performance_status,
			"last_tested_at": now_datetime(),
			"last_error": "\n".join(errors)[:2000],
		},
		update_modified=False,
	)
	return {
		"status": "success" if not errors else "partial" if seller_status == "Available" else "error",
		"results": results,
		"message": f"Ozon API test completed: {sum(row['status'] == 'Available' for row in results)} available",
	}


# Chinese aliases make future configuration-page integrations easier to read.
获取Ozon店铺 = get_store
发送OzonSeller请求 = ozon_seller_request
发送OzonPerformance请求 = ozon_performance_request

