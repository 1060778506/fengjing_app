# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

"""只封装Amazon Fulfillment Inbound v2024-03-20的读取接口。"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from frappe.utils import get_datetime, get_system_timezone

from fengjing_app.fengjing_business.doctype.amazon_store_configuration.amazon_store_configuration import (
	amazon_api_json,
)


SERVICE = "fba-inbound"
BASE_PATH = "/inbound/fba/2024-03-20"


def amazon_datetime(value):
	"""把Amazon ISO 8601时间转换成可比较的UTC时间。"""
	if not value:
		return None
	if isinstance(value, datetime):
		result = value
	else:
		text = str(value).strip()
		try:
			result = datetime.fromisoformat(text.replace("Z", "+00:00"))
		except ValueError:
			result = get_datetime(text)
	if result.tzinfo is None:
		result = result.replace(tzinfo=timezone.utc)
	return result.astimezone(timezone.utc)


def frappe_datetime(value):
	value = amazon_datetime(value)
	return value.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None) if value else None


def iter_inbound_plan_pages(store, *, sort_by="LAST_UPDATED_TIME", sort_order="DESC", status=None):
	"""逐页返回计划摘要，检测Amazon异常重复的分页标记。"""
	token = None
	seen_tokens = set()
	while True:
		params = {"pageSize": 30, "sortBy": sort_by, "sortOrder": sort_order}
		if status:
			params["status"] = status
		if token:
			params["paginationToken"] = token
		payload = amazon_api_json(store, SERVICE, "GET", f"{BASE_PATH}/inboundPlans", params=params)
		plans = payload.get("inboundPlans") or []
		next_token = (payload.get("pagination") or {}).get("nextToken")
		yield plans, next_token
		if not next_token:
			break
		if next_token in seen_tokens:
			raise RuntimeError("Amazon入库计划接口返回了重复的分页标记。")
		seen_tokens.add(next_token)
		token = next_token


def get_inbound_plan(store, inbound_plan_id):
	return amazon_api_json(
		store,
		SERVICE,
		"GET",
		f"{BASE_PATH}/inboundPlans/{inbound_plan_id}",
	)


def get_shipment(store, inbound_plan_id, shipment_id):
	return amazon_api_json(
		store,
		SERVICE,
		"GET",
		f"{BASE_PATH}/inboundPlans/{inbound_plan_id}/shipments/{shipment_id}",
	)


def iter_shipment_item_pages(store, inbound_plan_id, shipment_id, *, resume_token=None):
	"""逐页返回一个货件的商品明细和下一页标记。"""
	token = resume_token or None
	seen_tokens = set()
	while True:
		params = {"pageSize": 1000}
		if token:
			params["paginationToken"] = token
		payload = amazon_api_json(
			store,
			SERVICE,
			"GET",
			f"{BASE_PATH}/inboundPlans/{inbound_plan_id}/shipments/{shipment_id}/items",
			params=params,
		)
		next_token = (payload.get("pagination") or {}).get("nextToken")
		yield payload.get("items") or [], next_token
		if not next_token:
			break
		if next_token in seen_tokens:
			raise RuntimeError("Amazon货件商品接口返回了重复的分页标记。")
		seen_tokens.add(next_token)
		token = next_token


def iter_received_item_pages(store, shipment_confirmation_id):
	"""通过Amazon仍公开的v0只读接口补充已发货和已接收数量。"""
	token = None
	seen_tokens = set()
	while True:
		if token:
			path = "/fba/inbound/v0/items"
			params = {"NextToken": token}
		else:
			path = f"/fba/inbound/v0/shipments/{shipment_confirmation_id}/items"
			params = None
		payload = amazon_api_json(store, SERVICE, "GET", path, params=params)
		result = payload.get("payload") or payload
		next_token = result.get("NextToken")
		yield result.get("ItemData") or [], next_token
		if not next_token:
			break
		if next_token in seen_tokens:
			raise RuntimeError("Amazon货件收货数量接口返回了重复的分页标记。")
		seen_tokens.add(next_token)
		token = next_token
