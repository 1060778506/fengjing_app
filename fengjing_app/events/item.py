import frappe


# 物料增加数量
@frappe.whitelist()
def 检测这是第几个物料():
    # 获取当前系统中 Item 的总数
    # 如果你需要按公司过滤，可以增加 filters={'company': company}
    count = frappe.db.count('Item')
    return count + 1
