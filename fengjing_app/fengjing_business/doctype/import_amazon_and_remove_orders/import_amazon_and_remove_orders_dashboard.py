from frappe import _

def get_data():
    return {
        'fieldname': '链接主表',  # 必须是 Amazon Settlement Detail 里的字段名
        'transactions': [
            {
                'label': _('相关明细'),
                'items': ['Amazon removes order details']
            }
        ]
    }