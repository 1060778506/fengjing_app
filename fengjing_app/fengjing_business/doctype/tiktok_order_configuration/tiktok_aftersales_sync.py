from .tiktok_api import local_datetime
from .tiktok_storage import amount, save_record, source_id


def sync_aftersales(client, window, counts):
	time_field = "create_time" if window.historical else "update_time"
	for rows in client.pages(
		"POST", "/return_refund/202602/returns/search", "return_orders",
		query={"page_size": 50, "sort_field": time_field, "sort_order": "ASC"},
		body={f"{time_field}_ge": window.start_timestamp, f"{time_field}_lt": window.end_timestamp},
	):
		for row in rows:
			refund = row.get("refund_amount") or {}
			value = refund.get("refund_total", refund.get("refund_total_amount", refund)) if isinstance(refund, dict) else refund
			total, currency = amount(value, refund.get("currency") if isinstance(refund, dict) else row.get("currency"))
			identifier = source_id(row, "return_id")
			counts[save_record("TikTok Aftersales Record", client.store.name, identifier, row, {
				"aftersales_id": identifier,
				"source_order_id": row.get("order_id"),
				"created_at": local_datetime(row.get("create_time")),
				"source_updated_at": local_datetime(row.get("update_time")),
				"status": row.get("return_status"),
				"refund_amount": total,
				"currency": currency,
			})] += 1
		counts["pages"] += 1
