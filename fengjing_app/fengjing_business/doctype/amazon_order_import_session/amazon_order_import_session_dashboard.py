from frappe import _

def get_data():
    return {
        'fieldname': '导入数据页关联', 
        # 左侧关联按钮
        'transactions': [
            {
                'label': _('关联订单'),
                'items': ['Amazon Order Summary']
            }
        ]
    }