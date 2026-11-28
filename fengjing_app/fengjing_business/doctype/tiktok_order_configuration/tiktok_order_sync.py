from .tiktok_api import local_datetime
from .tiktok_storage import amount, save_record, source_id


def sync_orders(client, window, counts):
	time_field = "create_time" if window.historical else "update_time"
	for rows in client.pages(
		"POST", "/order/202309/orders/search", "orders",
		query={"sort_field": time_field, "sort_order": "ASC"},
		body={f"{time_field}_ge": window.start_timestamp, f"{time_field}_lt": window.end_timestamp},
	):
		for row in rows:
			payment = row.get("payment") or {}
			total, currency = amount(payment.get("total_amount"), payment.get("currency"))
			counts[save_record("TikTok Order Record", client.store.name, source_id(row, "id"), row, {
				"source_order_id": source_id(row, "id"),
				"created_at": local_datetime(row.get("create_time")),
				"source_updated_at": local_datetime(row.get("update_time")),
				"status": row.get("status"),
				"amount": total,
				"currency": currency,
			})] += 1
		counts["pages"] += 1
