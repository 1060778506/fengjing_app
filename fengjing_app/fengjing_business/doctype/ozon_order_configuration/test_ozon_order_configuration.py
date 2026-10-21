"""Regression checks for Ozon's cursor-based posting list responses."""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from . import ozon_order_configuration as orders


class TestOzonPostingLists(unittest.TestCase):
	def test_current_money_objects_and_legacy_prices(self):
		self.assertEqual(orders._money_amount({"amount": "12.50", "currency": "RUB"}), 12.5)
		self.assertEqual(orders._money_amount("12.50"), 12.5)
		with self.assertRaises(ValueError):
			orders._money_amount({"currency": "RUB"})

	def test_both_list_endpoints_follow_every_cursor(self):
		start = datetime(2026, 10, 1, tzinfo=timezone.utc)
		end = datetime(2026, 10, 2, tzinfo=timezone.utc)
		for query, endpoint in (
			(orders._query_fbs_window, "/v4/posting/fbs/list"),
			(orders._query_fbo_window, "/v3/posting/fbo/list"),
		):
			with self.subTest(endpoint=endpoint):
				pages = [
					{"postings": [{"posting_number": "first"}], "has_next": True, "cursor": "next"},
					{"postings": [{"posting_number": "second"}], "has_next": False, "cursor": ""},
				]
				responses = [SimpleNamespace(status_code=200, json=lambda page=page: page) for page in pages]
				with (
					patch.object(orders, "ozon_seller_request", side_effect=responses) as request,
					patch.object(orders, "ensure_database_connection"),
					patch.object(orders, "_save_order", return_value={"created": 1, "updated": 0, "archived": 0}),
					patch.object(frappe.db, "commit"),
				):
					summary = query(SimpleNamespace(cost_center="store", ozon_id="123"), "test", start, end)
				self.assertEqual(summary["pages"], 2)
				self.assertEqual(summary["postings"], 2)
				self.assertEqual(summary["created"], 2)
				self.assertEqual([call.args[2] for call in request.call_args_list], [endpoint, endpoint])
				self.assertEqual(
					[call.kwargs["json_data"]["cursor"] for call in request.call_args_list],
					["", "next"],
				)
				self.assertEqual(
					[call.kwargs["json_data"]["limit"] for call in request.call_args_list],
					[100, 100],
				)

	def test_repeated_cursor_fails_instead_of_silently_losing_orders(self):
		start = datetime(2026, 10, 1, tzinfo=timezone.utc)
		end = datetime(2026, 10, 2, tzinfo=timezone.utc)
		response = SimpleNamespace(
			status_code=200,
			json=lambda: {"postings": [{"posting_number": "first"}], "has_next": True, "cursor": ""},
		)
		with (
			patch.object(orders, "ozon_seller_request", return_value=response),
			patch.object(orders, "ensure_database_connection"),
			patch.object(orders, "_save_order", return_value={"created": 1, "updated": 0, "archived": 0}),
			patch.object(frappe.db, "commit"),
		):
			with self.assertRaisesRegex(RuntimeError, "new cursor"):
				orders._query_fbs_window(SimpleNamespace(cost_center="store", ozon_id="123"), "test", start, end)
