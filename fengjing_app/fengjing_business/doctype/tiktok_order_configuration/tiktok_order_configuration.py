import hashlib
import json
import re
import secrets
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

import frappe
import requests
from frappe.model.document import Document
from frappe.utils import now_datetime
from frappe.utils.password import set_encrypted_password
from werkzeug.wrappers import Response
from .tiktok_api import TikTokAPIError, TikTokClient, sign_parameters, token_expiry_fields


DOCTYPE = "TikTok Order Configuration"
TOKEN_URL = "https://auth.tiktok-shops.com/api/v2/token/get"
SHOP_API = "https://open-api.tiktokglobalshop.com"
AUTHORIZED_SHOPS_PATH = "/authorization/202309/shops"
CALLBACK_PATH = f"/api/method/{__name__}.authorization_callback"
AUTHORIZATION_TIMEOUT = 20 * 60
RESPONSE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


class TikTokOrderConfiguration(Document):
	pass


class AuthorizationError(Exception):
	pass


def _seller_authorization_url(url, state):
	parts = urlsplit(url)
	query = [(key, value) for key, value in parse_qsl(parts.query) if key != "state"]
	if (
		parts.scheme != "https"
		or not re.fullmatch(r"services(?:\.[a-z]{2})?\.tiktokshop\.com", parts.netloc.lower())
		or parts.path.rstrip("/") != "/open/authorize"
		or sum(key == "service_id" for key, _ in query) != 1
	):
		raise ValueError("Invalid seller authorization URL")
	query.append(("state", state))
	return parts._replace(query=urlencode(query), fragment="").geturl()


def _config_binding(doc):
	return {
		"app_key": (doc.app_key or "").strip(),
		"shop_id": (doc.shop_id or "").strip(),
		"secret_hash": hashlib.sha256((doc.app_secret or "").strip().encode()).hexdigest(),
	}


@frappe.whitelist(methods=["POST"])
def start_authorization(configuration):
	doc = frappe.get_doc(DOCTYPE, configuration)
	doc.check_permission("write")
	binding = _config_binding(doc)
	if not binding["app_key"] or not (doc.app_secret or "").strip() or not binding["shop_id"]:
		frappe.throw("请先填写应用密钥、应用密码和店铺 ID。")

	state = secrets.token_hex(32)
	try:
		url = _seller_authorization_url((doc.seller_authorization_url or "").strip(), state)
	except ValueError:
		frappe.throw("请填写 Partner Center 提供的卖家授权链接（包含 service_id）。")

	browser_token = secrets.token_urlsafe(32)
	cache_key = frappe.cache.make_key(f"tiktok_order_auth:{state}")
	frappe.cache.set(cache_key, json.dumps({
		"configuration": doc.name,
		"user": frappe.session.user,
		"browser_token": browser_token,
		"binding": binding,
	}), ex=AUTHORIZATION_TIMEOUT)

	response = Response(
		json.dumps({"message": {"authorization_url": url}}),
		mimetype="application/json",
		headers=RESPONSE_HEADERS,
	)
	response.set_cookie(
		f"fj_tiktok_auth_{state}", browser_token, max_age=AUTHORIZATION_TIMEOUT,
		path=CALLBACK_PATH, secure=True, httponly=True, samesite="Lax",
	)
	return response


@frappe.whitelist(methods=["POST"])
def test_authorization(configuration):
	doc = frappe.get_doc(DOCTYPE, configuration)
	doc.check_permission("write")
	configured_id = _config_binding(doc)["shop_id"]
	if not configured_id:
		frappe.throw("请先填写店铺 ID 并完成授权。")
	try:
		result = TikTokClient(doc).request("GET", AUTHORIZED_SHOPS_PATH, shop_scoped=False)
	except TikTokAPIError as error:
		frappe.throw(str(error))
	shops = result.get("shops")
	if not isinstance(shops, list) or not any(
		isinstance(shop, dict) and configured_id in (str(shop.get("id")), shop.get("code"))
		and shop.get("cipher") == doc.shop_cipher
		for shop in shops
	):
		frappe.throw("当前令牌未找到匹配的店铺授权，请核对店铺 ID 并重新授权。")
	return {"message": "连接成功，TikTok 已确认当前店铺的授权。"}


@frappe.whitelist(allow_guest=True, methods=["GET"])
def authorization_callback(state=None, code=None, app_key=None):
	if not isinstance(state, str) or not re.fullmatch(r"[a-f0-9]{64}", state):
		return _callback_response("授权链接无效。请从 ERPNext 店铺配置重新发起授权。", 400)

	cookie_name = f"fj_tiktok_auth_{state}"
	browser_token = frappe.request.cookies.get(cookie_name)
	cache_key = frappe.cache.make_key(f"tiktok_order_auth:{state}")
	pending = frappe.cache.get(cache_key) if browser_token else None
	if not pending or not secrets.compare_digest(
		json.loads(pending)["browser_token"].encode(), browser_token.encode()
	):
		return _callback_response("授权已过期，或浏览器与发起授权时不同。请从 ERPNext 重新发起授权。", 400)
	pending = frappe.cache.getdel(cache_key)
	if not pending:
		return _callback_response("这次授权已处理。请回到 ERPNext 查看授权时间。", 400)

	try:
		if not isinstance(code, str) or not code or len(code) > 4096:
			raise AuthorizationError("TikTok 未返回有效授权码，请重新授权。")
		_save_authorization(json.loads(pending), code, app_key)
		frappe.db.commit()
		response = _callback_response("TikTok 店铺授权成功。可以返回 ERPNext 的 TikTok 订单配置。")
	except AuthorizationError as error:
		frappe.db.rollback()
		response = _callback_response(str(error), 400)
	except Exception as error:
		frappe.db.rollback()
		frappe.logger("tiktok_shop", allow_site=True).error("Authorization callback failed: %s", type(error).__name__)
		response = _callback_response("授权未完成，请联系 ERPNext 管理员或重新发起授权。", 500)

	response.delete_cookie(cookie_name, path=CALLBACK_PATH, secure=True, httponly=True, samesite="Lax")
	return response


def _callback_response(message, status=200):
	return Response(message, status=status, mimetype="text/plain", headers=RESPONSE_HEADERS)


def _get_bound_configuration(pending, *, for_update=False):
	doc = frappe.get_doc(DOCTYPE, pending["configuration"], for_update=for_update)
	if not doc.has_permission("write", user=pending["user"]) or _config_binding(doc) != pending["binding"]:
		raise AuthorizationError("店铺配置或权限已变更，请重新发起授权。")
	return doc


def _save_authorization(pending, code, callback_app_key):
	doc = _get_bound_configuration(pending)
	app_key = pending["binding"]["app_key"]
	app_secret = doc.app_secret.strip()
	if callback_app_key is not None and callback_app_key != app_key:
		raise AuthorizationError("TikTok 回传的应用密钥与已选配置不匹配。")

	tokens = _get_tiktok_data(TOKEN_URL, {
		"app_key": app_key,
		"app_secret": app_secret,
		"auth_code": code,
		"grant_type": "authorized_code",
	}, "兑换授权令牌")
	access_token, refresh_token = tokens.get("access_token"), tokens.get("refresh_token")
	if tokens.get("user_type") not in (0, "0") or not all(
		isinstance(token, str) and token for token in (access_token, refresh_token)
	):
		raise AuthorizationError("TikTok 未返回有效的卖家访问令牌和刷新令牌。")

	params = {"app_key": app_key, "timestamp": str(int(time.time()))}
	params["sign"] = sign_parameters(AUTHORIZED_SHOPS_PATH, params, app_secret)
	shops = _get_tiktok_data(
		SHOP_API + AUTHORIZED_SHOPS_PATH, params, "查询已授权店铺",
		headers={"x-tts-access-token": access_token, "content-type": "application/json"},
	).get("shops", [])
	shop_id = pending["binding"]["shop_id"]
	matches = [shop for shop in shops if shop_id in (str(shop.get("id")), shop.get("code"))]
	if len(matches) != 1 or not matches[0].get("cipher"):
		raise AuthorizationError("授权范围内没有唯一匹配的店铺，请核对店铺 ID 和 TikTok 授权账号。")

	doc = _get_bound_configuration(pending, for_update=True)
	set_encrypted_password(DOCTYPE, doc.name, access_token, "access_token")
	set_encrypted_password(DOCTYPE, doc.name, refresh_token, "refresh_token")
	frappe.db.set_value(DOCTYPE, doc.name, {
		"shop_cipher": matches[0]["cipher"],
		"authorized_at": now_datetime(),
		"access_expires_at": None,
		"refresh_expires_at": None,
		**token_expiry_fields(tokens),
	}, modified_by=pending["user"])


def _get_tiktok_data(url, params, step, *, headers=None):
	try:
		response = requests.get(url, params=params, headers=headers, timeout=(5, 15), allow_redirects=False)
		payload = response.json()
	except (requests.RequestException, ValueError):
		raise AuthorizationError(f"TikTok {step}请求失败，请重新发起授权。") from None
	api_code = payload.get("code") if isinstance(payload, dict) else None
	if response.status_code != 200 or api_code not in (0, "0"):
		detail = f"（错误码 {api_code}）" if isinstance(api_code, int) else ""
		raise AuthorizationError(f"TikTok {step}失败{detail}，请核对应用权限或重新授权。")
	data = payload.get("data")
	if not isinstance(data, dict):
		raise AuthorizationError(f"TikTok {step}返回的数据不完整。")
	return data
