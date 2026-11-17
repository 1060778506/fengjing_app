"""Manually back up and re-fetch inventory ledger detail with occurrence-safe keys.

Run backup() first, then resync() for complete calendar-day ranges. This utility
does not change ledger summaries or configured history checkpoints.
"""

import gzip
import hashlib
import json
import os
from pathlib import Path

import frappe
from frappe.utils import getdate, now_datetime

from .amazon_fba_inventory_ledger_configuration import (
    DETAIL_DOCTYPE,
    _enabled_group,
    _group_lock,
    _request_group_report,
    _update_configuration,
)


def _backup_dir():
    return Path(frappe.get_site_path("private", "backups")).resolve()


def backup():
    """Archive every existing detail field before replacing historical results."""
    rows = frappe.db.sql("SELECT * FROM `tabAmazon FBA Inventory Ledger Detail`", as_dict=True)
    directory = _backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = now_datetime().strftime("%Y%m%d-%H%M%S-%f")
    path = directory / f"fba-ledger-detail-before-duplicate-repair-{timestamp}.json.gz"
    temporary = path.with_suffix(".tmp")
    try:
        with gzip.open(temporary, "wt", encoding="utf-8") as output:
            json.dump({
                "site": frappe.local.site,
                "created_at": str(now_datetime()),
                "reason": "Back up ledger detail before duplicate occurrence repair",
                "rows": rows,
            }, output, ensure_ascii=False, default=str)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "rows": len(rows),
        "bytes": path.stat().st_size,
    }


def resync(master_name, start_date, end_date, backup_path, backup_sha256):
    """Re-fetch a complete date range without resetting normal sync progress."""
    path = Path(backup_path).resolve()
    if not path.is_relative_to(_backup_dir()) or not path.is_file():
        raise ValueError("库存明细备份文件不在本站点的 private/backups 中")
    if hashlib.sha256(path.read_bytes()).hexdigest() != backup_sha256:
        raise ValueError("库存明细备份校验值不一致")
    with gzip.open(path, "rt", encoding="utf-8") as source:
        if json.load(source).get("site") != frappe.local.site:
            raise ValueError("库存明细备份属于另一个站点")

    start, end = getdate(start_date), getdate(end_date)
    if end < start or (end - start).days > 365:
        raise ValueError("每次修复必须是完整日期，且范围不超过 366 天")
    master, _configurations, _stores, regions = _enabled_group(master_name)
    lock = _group_lock(master.name)
    if not lock.acquire(blocking=False):
        return {"status": "busy", "message": "库存分类账同步任务正在运行"}
    try:
        result = {}
        for region_name, targets in sorted(regions.items()):
            report_id, stats = _request_group_report(
                master, targets, "detail", start, end,
                f"ledger-detail-repair-{frappe.generate_hash(length=12)}",
            )
            for config, _store in targets:
                _update_configuration(config.name, detail_status="Completed", detail_last_error="")
            result[region_name] = {"report_id": report_id, "stores": stats}
        return {"status": "completed", "start": str(start), "end": str(end), "regions": result}
    finally:
        lock.release()
