import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import frappe
import requests
from frappe.utils import cint, get_datetime, get_system_timezone, now_datetime
from frappe.utils.password import set_encrypted_password
from redis.exceptions import LockError


STORE_DOCTYPE = "TikTok Order Configuration"
API_BASE = "https://open-api.tiktokglobalshop.com"
REFRESH_URL = "https://auth.tiktok-shops.com/api/v2/token/refresh"


class TikTokAPIError(RuntimeError):
	def __init__(self, message, code=None):
		super().__init__(message)
		self.code = code


def sign_parameters(path, params, secret, body=b""):
	query = "".join(f"{key}{params[key]}" for key in sorted(params) if key not in {"sign", "access_token"})
	key = secret.encode()
	return hmac.new(key, key + (path + query).encode() + body + key, hashlib.sha256).hexdigest()


def local_datetime(timestamp):
	if not timestamp:
		return None
	return datetime.fromtimestamp(int(timestamp), timezone.utc).astimezone(
		ZoneInfo(get_system_timezone())
	).replace(tzinfo=None)


def token_expiry_fields(payload):
	values = {}
	for token in ("access", "refresh"):
		expires = cint(payload.get(f"{token}_token_expire_in"))
		if expires > 0:
			timestamp = expires if expires >= 1_000_000_000 else int(time.time()) + expires
			values[f"{token}_expires_at"] = local_datetime(timestamp)
	return values


def _response_data(response):
	try:
		result = response.json()
	except ValueError:
		result = {}
	if not isinstance(result, dict):
		result = {}
	code = result.get("code")
	if response.status_code != 200 or code not in (0, "0"):
		details = f"，错误码 {cint(code)}" if code is not None else ""
		raise TikTokAPIError(f"TikTok 请求失败（HTTP {response.status_code}{details}）。", cint(code))
	data = result.get("data")
	if not isinstance(data, dict):
		raise TikTokAPIError("TikTok 返回的数据不完整。")
	return data


def _refresh_access_token(store_name, used_token):
	lock = frappe.cache.lock(frappe.cache.make_key(f"tiktok-token-refresh:{store_name}"), timeout=90, blocking_timeout=25)
	try:
		with lock:
			store = frappe.get_doc(STORE_DOCTYPE, store_name, for_update=True)
			token = store.get_password("access_token", raise_exception=False)
			if token and token != used_token:
				frappe.db.commit()
				return token
			refresh_token = store.get_password("refresh_token", raise_exception=False)
			if not refresh_token:
				raise TikTokAPIError("TikTok 刷新令牌缺失，请重新授权店铺。")
			try:
				response = requests.get(
					REFRESH_URL,
					params={
						"app_key": store.app_key.strip(),
						"app_secret": store.app_secret.strip(),
						"refresh_token": refresh_token,
						"grant_type": "refresh_token",
					},
					timeout=(5, 15),
					allow_redirects=False,
				)
			except requests.RequestException:
				raise TikTokAPIError("刷新 TikTok 令牌时网络异常，请重试。") from None
			try:
				tokens = _response_data(response)
			except TikTokAPIError as error:
				raise TikTokAPIError(f"刷新令牌失败。{error} 如授权已失效，请重新授权店铺。", error.code) from None
			new_token, new_refresh = tokens.get("access_token"), tokens.get("refresh_token")
			if not all(isinstance(token, str) and token for token in (new_token, new_refresh)):
				raise TikTokAPIError("TikTok 未返回完整的刷新令牌，请重新授权店铺。")
			set_encrypted_password(STORE_DOCTYPE, store_name, new_token, "access_token")
			set_encrypted_password(STORE_DOCTYPE, store_name, new_refresh, "refresh_token")
			frappe.db.set_value(
				STORE_DOCTYPE, store_name, {"access_expires_at": None, "refresh_expires_at": None, **token_expiry_fields(tokens)},
				update_modified=False,
			)
			# Commit the rotated credentials even if a subsequent business request fails.
			frappe.db.commit()
			return new_token
	except LockError:
		raise TikTokAPIError("店铺令牌正在刷新，请稍后重试。") from None


class TikTokClient:
	def __init__(self, store):
		self.store = store
		self.token = store.get_password("access_token", raise_exception=False)
		if not self.token or not store.shop_cipher or not store.app_key or not store.app_secret:
			raise TikTokAPIError("店铺授权资料不完整，请先在店铺配置完成授权。")
		expires = store.get("access_expires_at")
		if expires and get_datetime(expires) <= now_datetime() + timedelta(minutes=2):
			self.token = _refresh_access_token(store.name, self.token)

	def request(self, method, path, *, query=None, body=None, shop_scoped=True):
		payload = b"" if body is None else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
		refreshed = False
		for attempt in range(4):
			params = {
				**(query or {}),
				"app_key": self.store.app_key.strip(),
				"timestamp": str(int(time.time())),
			}
			if shop_scoped:
				params["shop_cipher"] = self.store.shop_cipher
			params["sign"] = sign_parameters(path, params, self.store.app_secret.strip(), payload)
			try:
				response = requests.request(
					method, API_BASE + path, params=params, data=payload or None,
					headers={"content-type": "application/json", "x-tts-access-token": self.token},
					timeout=(5, 30), allow_redirects=False,
				)
			except requests.RequestException:
				if attempt == 3:
					raise TikTokAPIError("TikTok 请求网络异常，请重试。") from None
				time.sleep(2 ** attempt)
				continue
			if (response.status_code == 429 or response.status_code >= 500) and attempt < 3:
				time.sleep(max(2 ** attempt, min(cint(response.headers.get("Retry-After")), 30)))
				continue
			try:
				return _response_data(response)
			except TikTokAPIError as error:
				if error.code != 105002 or refreshed or attempt == 3:
					raise
				self.token = _refresh_access_token(self.store.name, self.token)
				refreshed = True

	def pages(self, method, path, rows_key, *, query=None, body=None):
		params = {"page_size": 100, **(query or {})}
		seen_tokens = set()
		while True:
			data = self.request(method, path, query=params, body=body)
			rows = data.get(rows_key)
			if rows is None and (not data or data.get("total_count") in (0, "0")) and not data.get("next_page_token"):
				# Some empty TikTok responses omit the array.
				rows = []
			if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
				raise TikTokAPIError(f"TikTok 分页返回缺少有效的 {rows_key} 列表。")
			yield rows
			next_token = data.get("next_page_token")
			if not next_token:
				break
			if next_token in seen_tokens:
				raise TikTokAPIError("TikTok 返回了重复的分页令牌，已停止以免遗漏。")
			seen_tokens.add(next_token)
			params["page_token"] = next_token
