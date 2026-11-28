from datetime import date, timedelta

from .tiktok_api import TikTokAPIError
from .tiktok_storage import amount, save_record, source_id


INVALID_DATE_RANGE = 28001022


def _request_performance(client, start, end):
	return client.request("GET", "/analytics/202609/shop/performance", query={
		"start_date_ge": start.isoformat(),
		"end_date_lt": end.isoformat(),
		"granularity": "1D",
		"currency": "LOCAL",
	})


def find_earliest_performance_date(client, rejected_start, today):
	def accepts(day):
		try:
			_request_performance(client, day, day + timedelta(days=1))
		except TikTokAPIError as error:
			if error.code != INVALID_DATE_RANGE:
				raise
			return False
		return True

	if accepts(rejected_start):
		return None
	# Search between a rejected day and a recent accepted day. Use completed shop dates only.
	for days_ago in (1, 2, 3, 7):
		recent = today - timedelta(days=days_ago)
		if recent > rejected_start and accepts(recent):
			break
	else:
		return None

	earliest = recent
	while (earliest - rejected_start).days > 1:
		middle = rejected_start + (earliest - rejected_start) // 2
		if accepts(middle):
			earliest = middle
		else:
			rejected_start = middle
	return earliest


def sync_shop_performance(client, window, counts):
	data = _request_performance(client, window.start_date, window.end_date)
	performance = data.get("performance")
	intervals = performance.get("intervals") if isinstance(performance, dict) else None
	if not isinstance(intervals, list):
		raise TikTokAPIError("TikTok 店铺表现缺少每日明细。")
	for row in intervals:
		report_date = date.fromisoformat(source_id(row, "start_date"))
		sales = row.get("sales") or {}
		total, currency = amount(sales.get("gmv"))
		counts[save_record("TikTok Shop Performance Record", client.store.name, report_date.isoformat(), row, {
			"report_date": report_date,
			"amount": total,
			"currency": currency,
			"order_count": sales.get("orders_count", sales.get("orders", sales.get("order_count"))),
		})] += 1
	counts["pages"] += 1
