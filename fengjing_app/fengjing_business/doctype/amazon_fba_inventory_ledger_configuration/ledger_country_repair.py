"""One-time, manually invoked repair of legacy US ledger rows saved under other stores.

Not registered in patches.txt: run backup() and then cleanup() deliberately.
"""

import gzip
import hashlib
import json
import os
from pathlib import Path

import frappe
from frappe.utils import now_datetime

from .amazon_fba_inventory_ledger_configuration import (
    _country_code,
    _source_country_value,
)


TABLES = {
    "summary": "`tabAmazon FBA Inventory Ledger Summary`",
    "detail": "`tabAmazon FBA Inventory Ledger Detail`",
}
COUNTRIES = ("Brazil", "Canada", "Mexico")
COUNTRY_SQL = "'Brazil', 'Canada', 'Mexico'"


def _matching_us_count(kind):
    if kind == "summary":
        matching = " AND ".join(
            f"usa.{field} = other.{field}" for field in (
                "period_date", "seller_sku", "fnsku", "asin", "disposition",
                "location_id", "time_aggregation", "location_type", "customer_shipments",
            )
        )
    else:
        matching = " AND ".join(
            f"usa.{field} = other.{field}" for field in (
                "event_at", "event_type", "reference_id", "seller_sku", "fnsku",
                "asin", "fulfillment_center", "disposition", "reason", "quantity",
            )
        )
    return frappe.db.sql(
        f"SELECT COUNT(*) AS total, COALESCE(SUM(EXISTS(SELECT 1 FROM {TABLES[kind]} usa "
        f"WHERE usa.country = 'United States' AND {matching})), 0) AS matched "
        f"FROM {TABLES[kind]} other WHERE other.country IN ({COUNTRY_SQL})",
        as_dict=True,
    )[0]


def _audited_rows():
    rows_by_kind = {}
    for kind, table in TABLES.items():
        rows = frappe.db.sql(
            f"SELECT * FROM {table} WHERE country IN ({COUNTRY_SQL}) ORDER BY name",
            as_dict=True,
        )
        for row in rows:
            try:
                raw = json.loads(row.raw_json or "{}")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{kind} {row.name}: 原始报告不是有效 JSON") from exc
            source = _country_code(_source_country_value(raw, kind))
            if source != "US":
                raise ValueError(f"{kind} {row.name}: 原始国家为 {source}，停止清理")
        match = _matching_us_count(kind)
        if len(rows) != int(match.total) or len(rows) != int(match.matched):
            raise ValueError(
                f"{kind}: {len(rows)} 条候选中仅 {match.matched} 条有美国对应记录，停止清理"
            )
        rows_by_kind[kind] = rows
    return rows_by_kind


def _backup_dir():
    return Path(frappe.get_site_path("private", "backups")).resolve()


def backup():
    """Archive every affected database field before any row is removed."""
    rows_by_kind = _audited_rows()
    directory = _backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = now_datetime().strftime("%Y%m%d-%H%M%S-%f")
    path = directory / f"fba-ledger-wrong-country-{timestamp}.json.gz"
    archive = {
        "site": frappe.local.site,
        "created_at": str(now_datetime()),
        "reason": "US FBA inventory ledger rows saved under Brazil, Canada or Mexico",
        "rows": rows_by_kind,
    }
    temporary = path.with_suffix(".tmp")
    try:
        with gzip.open(temporary, "wt", encoding="utf-8") as output:
            json.dump(archive, output, ensure_ascii=False, default=str)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "path": str(path), "sha256": digest,
        "summary": len(rows_by_kind["summary"]),
        "detail": len(rows_by_kind["detail"]),
        "bytes": path.stat().st_size,
    }


def cleanup(path, sha256):
    """Remove only archived rows, after rerunning all source and counterpart checks."""
    archive_path = Path(path).resolve()
    if not archive_path.is_relative_to(_backup_dir()) or not archive_path.is_file():
        raise ValueError("备份文件不在本站点的 private/backups 中")
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != sha256:
        raise ValueError("备份文件校验值不一致")
    with gzip.open(archive_path, "rt", encoding="utf-8") as source:
        archive = json.load(source)
    if archive.get("site") != frappe.local.site:
        raise ValueError("备份文件属于另一个站点")

    current = _audited_rows()
    for kind in TABLES:
        archived_names = {row["name"] for row in archive["rows"][kind]}
        current_names = {row.name for row in current[kind]}
        if archived_names != current_names:
            raise ValueError(f"{kind} 候选记录在备份后发生变化，停止清理")

    usa_before = {
        kind: frappe.db.sql(
            f"SELECT COUNT(*) FROM {table} WHERE country = 'United States'"
        )[0][0] for kind, table in TABLES.items()
    }
    try:
        for kind, table in TABLES.items():
            names = [row.name for row in current[kind]]
            for offset in range(0, len(names), 500):
                batch = names[offset:offset + 500]
                placeholders = ", ".join(["%s"] * len(batch))
                frappe.db.sql(f"DELETE FROM {table} WHERE name IN ({placeholders})", tuple(batch))
        for kind, table in TABLES.items():
            remaining = frappe.db.sql(
                f"SELECT COUNT(*) FROM {table} WHERE country IN ({COUNTRY_SQL})"
            )[0][0]
            usa_after = frappe.db.sql(
                f"SELECT COUNT(*) FROM {table} WHERE country = 'United States'"
            )[0][0]
            if remaining or usa_after != usa_before[kind]:
                raise ValueError(f"{kind} 清理后核对失败，回滚事务")
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        raise
    return {"backup": str(archive_path), **{
        kind: len(current[kind]) for kind in TABLES
    }}
