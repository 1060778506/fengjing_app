from collections import Counter
from urllib.parse import quote

import frappe
from frappe.utils import getdate

from .tiktok_api import TikTokAPIError, local_datetime
from .tiktok_storage import amount, save_record, source_id


STATEMENT_DOCTYPE = "TikTok Statement Record"


def sync_statements(client, window, counts):
	for rows in client.pages("GET", "/finance/202309/statements", "statements", query={
		"statement_time_ge": window.start_timestamp,
		"statement_time_lt": window.end_timestamp,
		"sort_field": "statement_time",
	}):
		for row in rows:
			identifier = source_id(row, "id")
			total, currency = amount(row.get("settlement_amount", row.get("amount")), row.get("currency"))
			counts[save_record(STATEMENT_DOCTYPE, client.store.name, identifier, row, {
				"statement_id": identifier,
				"created_at": local_datetime(row.get("statement_time") or row.get("create_time")),
				"status": row.get("payment_status") or row.get("status"),
				"payment_id": row.get("payment_id"),
				"amount": total,
				"currency": currency,
			})] += 1
		counts["pages"] += 1


def sync_statement_transactions(client, window, counts):
	store = client.store.name
	if window.recheck:
		header_counts = Counter()
		sync_statements(client, window, header_counts)
		counts["statement_headers"] += sum(header_counts[key] for key in ("created", "updated", "unchanged", "older"))
	else:
		header = frappe.db.get_value(
			"TikTok Statement Sync Configuration", {"tiktok_store": store},
			["last_success_at", "history_origin", "history_cursor"], as_dict=True,
		)
		if not header or not header.last_success_at:
			raise TikTokAPIError("请先同步结算账单列表，再抓取该期间的账单交易明细。")
		if window.historical and (
			not header.history_origin or not header.history_cursor
			or getdate(header.history_origin) > window.start_date
			or getdate(header.history_cursor) < window.end_date
		):
			raise TikTokAPIError("请先完成该日期段的结算账单列表补抓，再重试账单交易明细。")
	statements = frappe.qb.DocType(STATEMENT_DOCTYPE)
	selected = (
		frappe.qb.from_(statements)
		.select(statements.name, statements.statement_id, statements.currency)
		.where(statements.tiktok_store == store)
		.where(statements.created_at >= local_datetime(window.start_timestamp))
		.where(statements.created_at < local_datetime(window.end_timestamp))
		.orderby(statements.created_at, statements.name)
		.run(as_dict=True)
	)
	counts["statements"] += len(selected)
	for statement in selected:
		path = f"/finance/202501/statements/{quote(statement.statement_id, safe='')}/statement_transactions"
		for rows in client.pages("GET", path, "transactions", query={"sort_field": "order_create_time"}):
			for row in rows:
				identifier = source_id(row, "id")
				total, currency = amount(row.get("settlement_amount"), row.get("currency") or statement.currency)
				counts[save_record("TikTok Statement Transaction Record", store, f"{statement.statement_id}/{identifier}", row, {
					"transaction_id": identifier,
					"statement": statement.name,
					"statement_id": statement.statement_id,
					"source_order_id": row.get("order_id"),
					"adjustment_id": row.get("adjustment_id"),
					"transaction_type": row.get("type"),
					"created_at": local_datetime(row.get("order_create_time")),
					"amount": total,
					"currency": currency,
				})] += 1
			counts["pages"] += 1


def sync_payouts(client, window, counts):
	for rows in client.pages("GET", "/finance/202605/payments", "payments", query={
		"create_time_ge": window.start_timestamp,
		"create_time_lt": window.end_timestamp,
		"sort_field": "create_time",
	}):
		for row in rows:
			identifier = source_id(row, "id")
			total, currency = amount(row.get("amount"), row.get("currency"))
			counts[save_record("TikTok Payout Record", client.store.name, identifier, row, {
				"payment_id": identifier,
				"created_at": local_datetime(row.get("create_time")),
				"status": row.get("status"),
				"amount": total,
				"currency": currency,
			})] += 1
		counts["pages"] += 1
