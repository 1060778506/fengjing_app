from __future__ import annotations

from datetime import timedelta

import frappe
from frappe.utils import cint, flt, getdate


DOCTYPE = "Amazon Balance Snapshot"
AMOUNT_FIELDS = (
	"total_balance",
	"available_balance",
	"reserved_balance",
	"deferred_balance",
	"account_level_reserve",
)
FILTER_FIELDS = (
	"master_configuration",
	"country_configuration",
	"amazon_store",
	"country",
	"marketplace_id",
	"currency_code",
	"account_type",
)


def _filters(raw=None):
	if isinstance(raw, str):
		raw = frappe.parse_json(raw)
	return frappe._dict(raw or {})


def _conditions(filters, include_dates=True):
	clauses = ["1=1"]
	values = {}
	for fieldname in FILTER_FIELDS:
		if filters.get(fieldname):
			clauses.append(f"`{fieldname}` = %({fieldname})s")
			values[fieldname] = filters.get(fieldname)
	if include_dates and filters.date_from:
		clauses.append("`as_of_date` >= %(date_from)s")
		values["date_from"] = getdate(filters.date_from)
	if include_dates and filters.date_to:
		clauses.append("`as_of_date` <= %(date_to)s")
		values["date_to"] = getdate(filters.date_to)
	return " AND ".join(clauses), values


def _date_limits():
	return frappe.db.sql(
		f"SELECT MIN(as_of_date) AS earliest_date, MAX(as_of_date) AS latest_date FROM `tab{DOCTYPE}`",
		as_dict=True,
	)[0]


def _normalise_dates(filters):
	limits = _date_limits()
	latest = getdate(limits.latest_date) if limits.latest_date else getdate()
	if not filters.date_to:
		filters.date_to = latest
	if not filters.date_from:
		filters.date_from = max(latest - timedelta(days=29), getdate(limits.earliest_date or latest))
	if getdate(filters.date_from) > getdate(filters.date_to):
		frappe.throw("开始日期不能晚于结束日期。")
	return limits


def _currency_summary(where, values, snapshot_date):
	if not snapshot_date:
		return []
	params = dict(values)
	params["snapshot_date"] = snapshot_date
	return frappe.db.sql(
		f"""SELECT currency_code,
			ROUND(SUM(total_balance), 2) AS total_balance,
			ROUND(SUM(available_balance), 2) AS available_balance,
			ROUND(SUM(reserved_balance), 2) AS reserved_balance,
			ROUND(SUM(deferred_balance), 2) AS deferred_balance,
			ROUND(SUM(account_level_reserve), 2) AS account_level_reserve,
			COUNT(name) AS account_count
		FROM `tab{DOCTYPE}`
		WHERE {where} AND as_of_date = %(snapshot_date)s
		GROUP BY currency_code ORDER BY currency_code""",
		params,
		as_dict=True,
	)


def _options():
	result = {}
	for fieldname in FILTER_FIELDS:
		result[fieldname] = [
			row[0]
			for row in frappe.db.sql(
				f"SELECT DISTINCT `{fieldname}` FROM `tab{DOCTYPE}` "
				f"WHERE IFNULL(`{fieldname}`, '') != '' ORDER BY `{fieldname}`"
			)
		]
	return result


@frappe.whitelist()
def get_balance_dashboard_data(filters=None, page=1, page_size=50):
	frappe.has_permission(DOCTYPE, "read", throw=True)
	filters = _filters(filters)
	limits = _normalise_dates(filters)
	where, values = _conditions(filters)
	non_date_where, non_date_values = _conditions(filters, include_dates=False)

	latest_date = frappe.db.sql(
		f"SELECT MAX(as_of_date) FROM `tab{DOCTYPE}` WHERE {where}", values
	)[0][0]
	previous_date = None
	if latest_date:
		previous_values = dict(non_date_values)
		previous_values["latest_date"] = latest_date
		previous_date = frappe.db.sql(
			f"SELECT MAX(as_of_date) FROM `tab{DOCTYPE}` "
			f"WHERE {non_date_where} AND as_of_date < %(latest_date)s",
			previous_values,
		)[0][0]
	current = _currency_summary(non_date_where, non_date_values, latest_date)
	previous = _currency_summary(non_date_where, non_date_values, previous_date)
	previous_by_currency = {row.currency_code: row for row in previous}
	for row in current:
		prior = previous_by_currency.get(row.currency_code)
		row.previous_total = flt(prior.total_balance, 2) if prior else None
		row.change_amount = flt(flt(row.total_balance, 2) - flt(prior.total_balance, 2), 2) if prior else None

	trend = frappe.db.sql(
		f"""SELECT as_of_date, currency_code,
			ROUND(SUM(total_balance), 2) AS total_balance,
			ROUND(SUM(available_balance), 2) AS available_balance,
			ROUND(SUM(reserved_balance), 2) AS reserved_balance,
			ROUND(SUM(deferred_balance), 2) AS deferred_balance,
			ROUND(SUM(account_level_reserve), 2) AS account_level_reserve
		FROM `tab{DOCTYPE}` WHERE {where}
		GROUP BY as_of_date, currency_code ORDER BY as_of_date, currency_code""",
		values,
		as_dict=True,
	)

	country_rows = []
	account_rows = []
	if latest_date:
		latest_values = dict(non_date_values)
		latest_values["latest_date"] = latest_date
		country_rows = frappe.db.sql(
			f"""SELECT country, amazon_store, marketplace_id, currency_code,
				ROUND(SUM(total_balance), 2) AS total_balance,
				ROUND(SUM(available_balance), 2) AS available_balance,
				ROUND(SUM(reserved_balance), 2) AS reserved_balance,
				ROUND(SUM(deferred_balance), 2) AS deferred_balance,
				ROUND(SUM(account_level_reserve), 2) AS account_level_reserve,
				COUNT(name) AS account_count
			FROM `tab{DOCTYPE}`
			WHERE {non_date_where} AND as_of_date = %(latest_date)s
			GROUP BY country, amazon_store, marketplace_id, currency_code
			ORDER BY FIELD(country, 'United States', 'Canada', 'Mexico', 'Brazil'), country, currency_code""",
			latest_values,
			as_dict=True,
		)
		account_rows = frappe.db.sql(
			f"""SELECT name, country, amazon_store, marketplace_id, account_type, currency_code,
				total_balance, available_balance, reserved_balance, deferred_balance,
				account_level_reserve, last_updated_time, fetched_at
			FROM `tab{DOCTYPE}`
			WHERE {non_date_where} AND as_of_date = %(latest_date)s
			ORDER BY FIELD(country, 'United States', 'Canada', 'Mexico', 'Brazil'),
				country, currency_code, account_type""",
			latest_values,
			as_dict=True,
		)

	total = cint(frappe.db.sql(f"SELECT COUNT(name) FROM `tab{DOCTYPE}` WHERE {where}", values)[0][0])
	page = max(cint(page), 1)
	page_size = min(max(cint(page_size), 10), 200)
	offset = (page - 1) * page_size
	detail_values = dict(values)
	detail_values.update({"page_size": page_size, "offset": offset})
	details = frappe.db.sql(
		f"""SELECT name, snapshot_key, as_of_date, country, amazon_store, marketplace_id,
			account_type, currency_code, total_balance, available_balance, reserved_balance,
			deferred_balance, account_level_reserve, last_updated_time, fetched_at, sync_type
		FROM `tab{DOCTYPE}` WHERE {where}
		ORDER BY as_of_date DESC,
			FIELD(country, 'United States', 'Canada', 'Mexico', 'Brazil'),
			country, currency_code, account_type
		LIMIT %(page_size)s OFFSET %(offset)s""",
		detail_values,
		as_dict=True,
	)

	return {
		"filters": {
			"date_from": str(getdate(filters.date_from)),
			"date_to": str(getdate(filters.date_to)),
		},
		"limits": {
			"earliest_date": str(limits.earliest_date or ""),
			"latest_date": str(limits.latest_date or ""),
		},
		"latest_date": str(latest_date or ""),
		"previous_date": str(previous_date or ""),
		"currencies": current,
		"trend": trend,
		"countries": country_rows,
		"accounts": account_rows,
		"details": details,
		"options": _options(),
		"pagination": {
			"page": page,
			"page_size": page_size,
			"total": total,
			"pages": max(1, (total + page_size - 1) // page_size),
		},
	}
