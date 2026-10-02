# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""Amazon 店铺配置及订单、财务、排名可共用的 SP-API 请求能力。"""

import hashlib
import time

import frappe
import requests
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime


TOKEN_URL = "https://api.amazon.com/auth/o2/token"

REGION_ENDPOINTS = {
	"North America": "https://sellingpartnerapi-na.amazon.com",
	"Europe": "https://sellingpartnerapi-eu.amazon.com",
	"Far East": "https://sellingpartnerapi-fe.amazon.com",
}

REGION_STORAGE_LABELS = {
	"North America": "北美",
	"Europe": "欧洲",
	"Far East": "远东",
}

MARKETPLACE_REGIONS = {
	"ATVPDKIKX0DER": "North America",
	"A2EUQ1WTGCTBG2": "North America",
	"A1AM78C64UM0Y8": "North America",
	"A2Q3Y263D00KWC": "North America",
	"A2ZV50J4W1RKNI": "North America",
	"A28R8C7NBKEWEA": "Europe",
	"A1RKKUPIHCS9HS": "Europe",
	"A1F83G8C2ARO7P": "Europe",
	"A13V1IB3VIYZZH": "Europe",
	"AMEN7PMS3EDWL": "Europe",
	"A1805IZSGTT6HS": "Europe",
	"A1PA6795UKMFR9": "Europe",
	"APJ6JRA9NG5V4": "Europe",
	"A2NODRKZP88ZB9": "Europe",
	"AE08WJ6YKNBMC": "Europe",
	"A1C3SOZRARQ6R3": "Europe",
	"ARBP9OOSHTCHU": "Europe",
	"A33AVAJ2PDY3EV": "Europe",
	"A17E79C6D8DWNP": "Europe",
	"A2VIGQ35RCS4UG": "Europe",
	"A21TJRUUN4KGV": "Europe",
	"A19VAU5U5O7RUS": "Far East",
	"A39IBJ37TRP1C6": "Far East",
	"A1VC38T7YXB528": "Far East",
}

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
RETRY_DELAYS = (5, 15, 30, 60, 120, 180)


class AmazonStoreConfiguration(Document):
	def validate(self):
		self.store_name = str(self.store_name or "").strip()
		self.marketplace_id = str(self.marketplace_id or "").strip().upper()
		self.seller_id = str(self.seller_id or "").strip().upper()
		region = MARKETPLACE_REGIONS.get(self.marketplace_id)
		if region:
			self.api_region = region
		if cint(self.enabled):
			missing = []
			for fieldname, label in (
				("store_name", _("Store Name")),
				("cost_center", _("Cost Center")),
				("marketplace_id", _("Marketplace ID")),
				("api_region", _("API Region")),
				("client_id", _("Client ID")),
			):
				if not self.get(fieldname):
					missing.append(label)
			for fieldname, label in (
				("client_secret", _("Client Secret")),
				("refresh_token", _("Refresh Token")),
			):
				if not _get_password(self, fieldname, raise_exception=False):
					missing.append(label)
			if missing:
				frappe.throw(_("Enabled Amazon store is missing: {0}").format(", ".join(missing)))


def ensure_database_connection():
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
		raise ValueError("Amazon configuration is not linked to a store")
	store = frappe.get_doc("Amazon Store Configuration", store_name)
	if require_enabled and not cint(store.enabled):
		raise ValueError(f"Amazon store {store.store_name or store.name} is disabled")
	if not store.marketplace_id:
		raise ValueError(f"Amazon store {store.store_name or store.name} has no Marketplace ID")
	return store


def get_region(store):
	marketplace_id = str(store.marketplace_id or "").strip().upper()
	region = MARKETPLACE_REGIONS.get(marketplace_id) or store.api_region
	if region not in REGION_ENDPOINTS:
		raise ValueError(f"Cannot determine SP-API region for Marketplace ID {marketplace_id}")
	return region


def get_endpoint(store):
	return REGION_ENDPOINTS[get_region(store)]


def get_storage_region(store):
	return REGION_STORAGE_LABELS[get_region(store)]


def _credential_fingerprint(store):
	value = "|".join((str(store.client_id or "").strip(), _get_password(store, "refresh_token")))
	return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _token_cache_key(store):
	return f"fengjing:amazon-access-token:{store.name}:{_credential_fingerprint(store)}"


def invalidate_access_token(store):
	frappe.cache().delete_value(_token_cache_key(store))


def get_access_token(store, force_refresh=False):
	cache_key = _token_cache_key(store)
	if not force_refresh:
		cached = frappe.cache().get_value(cache_key)
		if cached:
			return str(cached)
	client_id = str(store.client_id or "").strip()
	client_secret = _get_password(store, "client_secret")
	refresh_token = _get_password(store, "refresh_token")
	if not client_id or not client_secret or not refresh_token:
		raise ValueError(f"Amazon store {store.store_name or store.name} has incomplete API credentials")
	response = None
	for attempt, delay in enumerate(RETRY_DELAYS):
		try:
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
		except requests.RequestException:
			if attempt == len(RETRY_DELAYS) - 1:
				raise
			time.sleep(delay)
			continue
		if response.status_code not in RETRYABLE_STATUS_CODES or attempt == len(RETRY_DELAYS) - 1:
			break
		time.sleep(delay)
	if response is None:
		raise RuntimeError("Amazon authorization returned no response")
	if response.status_code != 200:
		raise RuntimeError(
			f"Amazon authorization failed (HTTP {response.status_code}): {response.text[:500]}"
		)
	payload = response.json() or {}
	token = payload.get("access_token")
	if not token:
		raise RuntimeError("Amazon authorization succeeded but returned no Access Token")
	expires_in = max(cint(payload.get("expires_in")) - 300, 300)
	frappe.cache().set_value(cache_key, token, expires_in_sec=expires_in)
	return token


def _rate_key(store, service):
	return f"fengjing:amazon-rate:{service}:{_credential_fingerprint(store)}"


def _wait_for_rate_limit(store, service):
	cache = frappe.cache()
	key = _rate_key(store, service)
	if service == "orders":
		with cache.lock(cache.make_key(f"{key}:lock"), timeout=30, blocking_timeout=30):
			now = time.time()
			state = cache.get_value(key) or {}
			try:
				tokens = float(state.get("tokens", 15))
				updated_at = float(state.get("updated_at", now))
			except (AttributeError, TypeError, ValueError):
				tokens, updated_at = 15.0, now
			tokens = min(15.0, tokens + max(now - updated_at, 0) / 180.0)
			if tokens >= 1:
				cache.set_value(key, {"tokens": tokens - 1, "updated_at": now}, expires_in_sec=86400)
				return
			wait_seconds = max((1 - tokens) * 180.0, 1)
			cache.set_value(key, {"tokens": tokens, "updated_at": now}, expires_in_sec=86400)
		time.sleep(wait_seconds)
		return
	minimum_interval = 2.1 if service == "finances" else 1.0
	with cache.lock(cache.make_key(f"{key}:lock"), timeout=30, blocking_timeout=30):
		now = time.time()
		try:
			last_reserved = float(cache.get_value(key) or 0)
		except (TypeError, ValueError):
			last_reserved = 0
		reserved = max(now, last_reserved + minimum_interval)
		cache.set_value(key, reserved, expires_in_sec=86400)
	wait_seconds = reserved - time.time()
	if wait_seconds > 0:
		time.sleep(wait_seconds)


def amazon_api_request(store, service, method, path, *, params=None, timeout=60, max_retries=None):
	url = path if str(path).startswith("http") else f"{get_endpoint(store)}{path}"
	last_response = None
	refreshed_token = False
	force_token_refresh = False
	retry_count = max(cint(max_retries), 0) if max_retries is not None else len(RETRY_DELAYS) - 1
	attempt_count = min(retry_count + 1, len(RETRY_DELAYS))
	delays = RETRY_DELAYS[:attempt_count]
	for attempt, delay in enumerate(delays):
		_wait_for_rate_limit(store, service)
		token = get_access_token(store, force_refresh=force_token_refresh)
		force_token_refresh = False
		try:
			response = requests.request(
				method,
				url,
				headers={
					"X-Amz-Access-Token": token,
					"Accept": "application/json",
					"User-Agent": "FengjingAmazonIntegration/2.0",
				},
				params=params,
				timeout=timeout,
			)
		except requests.RequestException:
			if attempt == len(delays) - 1:
				raise
			time.sleep(delay)
			continue
		last_response = response
		if response.status_code == 401 and not refreshed_token:
			invalidate_access_token(store)
			refreshed_token = True
			force_token_refresh = True
			continue
		if response.status_code in RETRYABLE_STATUS_CODES and attempt < len(delays) - 1:
			retry_after = response.headers.get("Retry-After")
			try:
				wait_seconds = max(float(retry_after), delay) if retry_after else delay
			except (TypeError, ValueError):
				wait_seconds = delay
			time.sleep(min(wait_seconds, 300))
			continue
		return response
	return last_response


@frappe.whitelist()
def test_amazon_store_api(name):
	store = get_store(name)
	try:
		response = amazon_api_request(store, "sellers", "GET", "/sellers/v1/marketplaceParticipations")
		if response is None or response.status_code != 200:
			status = response.status_code if response is not None else "no response"
			message = response.text[:1000] if response is not None else "Amazon returned no response"
			raise RuntimeError(f"Amazon API test failed (HTTP {status}): {message}")
		frappe.db.set_value(
			store.doctype,
			store.name,
			{"api_status": "Available", "last_tested_at": now_datetime(), "last_error": ""},
			update_modified=False,
		)
		frappe.db.commit()
		return {"status": "success", "message": "Amazon API connection is available"}
	except Exception as exc:
		frappe.db.set_value(
			store.doctype,
			store.name,
			{"api_status": "Unavailable", "last_tested_at": now_datetime(), "last_error": str(exc)[:2000]},
			update_modified=False,
		)
		frappe.db.commit()
		raise
