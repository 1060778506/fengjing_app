import frappe
from frappe import _


@frappe.whitelist()
def get_translation_configuration():
    """Return enabled CSV definitions and rules without receiving any uploaded file."""
    if frappe.session.user == "Guest":
        frappe.throw(_("请先登录后再使用翻译工具"), frappe.PermissionError)

    report_types = frappe.get_all(
        "Amazon CSV Report Type",
        filters={"enabled": 1},
        fields=[
            "name",
            "report_type_name",
            "report_type_code",
            "recognition_priority",
            "recognition_fields",
            "description",
        ],
        order_by="recognition_priority desc, report_type_name asc",
        limit_page_length=0,
    )

    report_names = [row.name for row in report_types]
    rules = []
    if report_names:
        rules = frappe.get_all(
            "Amazon CSV Translation Rule",
            filters={"enabled": 1, "report_type": ["in", report_names]},
            fields=[
                "report_type",
                "translation_type",
                "applicable_country",
                "file_field_name",
                "source_text",
                "target_text",
            ],
            order_by="report_type asc, translation_type asc, file_field_name asc, source_text asc",
            limit_page_length=0,
        )

    sku_item_mappings = frappe.get_all(
        "Fengjing - Product Corresponding Platform - Main Table",
        filters={"启用": 1, "平台sku": ["!=", ""], "站点id": ["!=", ""]},
        fields=["站点id", "平台sku", "物料id", "物料名称"],
        order_by="站点id asc, 平台sku asc",
        limit_page_length=0,
    )

    return {
        "report_types": report_types,
        "rules": rules,
        "sku_item_mappings": sku_item_mappings,
        "privacy": {
            "uploads_are_saved": False,
            "translation_history_is_saved": False,
        },
    }
