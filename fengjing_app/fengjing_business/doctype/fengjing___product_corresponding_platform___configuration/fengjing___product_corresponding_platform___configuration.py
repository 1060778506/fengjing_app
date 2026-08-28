# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import frappe
import requests
from frappe.model.document import Document
from frappe.utils.scheduler import enable_scheduler, is_scheduler_disabled
from frappe import _

# 如果是新系统就自动填充提示词
class FengjingProductCorrespondingPlatformConfiguration(Document):
    # 1. 定义一个类常量，作为唯一的事实来源
    默认物料命名模版 = """[颜色]-[营销名称(尺寸)-名称]-[材质]-[备注]"""
    STANDARD_PROMPT = """
    # Role
    你是一个专业的 ERP 物料数据清洗专家。

    # Task
    根据输入的【物料描述】和【物料位数】，严格按照规范生成 10 组备选输出。

    # Output Format (严格执行，每组两行，组与组之间必须空一行，严禁解释):
    fj-[关键信息拼音首字母(含尺寸)]-[六位序列号]
    [颜色]-[营销名称(尺寸)-名称]-[材质]-[备注]

    (此处必须有一个空行)

    # Rules
    1. 键值逻辑：fj-前缀 + 核心拼音首字母 + 6位序列号。
    2. 名称逻辑：[颜色]-[营销名称(尺寸)-名称]-[材质]-[备注]。缺失写“未提及”。
    3. 差异化：序列号保持一致，微调键的字母缩写和名称备注的侧重点，生成 10 组。
    4. 禁令：严禁输出“物料号：”、“物料名：”、“第n组”等字样。严禁任何开场白。

    # 正确输出示例（严格按照此空行格式）:
    fj-6.0inxfcx-000001
    不锈钢色-6.0in小方插销-不锈钢-带螺丝1个装

    fj-6.0inxfcx-000001
    不锈钢色-6.0in小方插销-不锈钢-含配套螺丝1个

    接下来请你处理以下自然语言：
    """
    亚马逊站点对应表 = """美国		Amazon.com		ATVPDKIKX0DER
加拿大	Amazon.ca		A2EUQ1WTGCTBG2
墨西哥	Amazon.com.mx	A1AM78C64UM0Y8
英国		Amazon.co.uk	A1F83G8C2ARO7P
德国		Amazon.de		A1PA6795UKMFR9
日本		Amazon.co.jp		A1VC38T7YXB528"""
    def onload(self):
        # 2. Python 内部调用
        if not self.丰境_ai生成物料提示词:
            self.丰境_ai生成物料提示词 = self.STANDARD_PROMPT
        if not self.站点id对应表:
            self.站点id对应表 = self.亚马逊站点对应表
        if not self.物料命名模版:
            self.物料命名模版 = self.默认物料命名模版
    # 3. 暴露给前端 JS 调用的接口
    # 恢复默认的ai提示词
    @frappe.whitelist()
    def get_standard_prompt(self):
        return {
            "STANDARD_PROMPT": self.STANDARD_PROMPT,
            "物料命名模版": self.默认物料命名模版
        }

    #返回给物料命名模版
    @frappe.whitelist()
    def get_item_naming_template():
        return frappe.db.get_single_value(
            "Fengjing - Product Corresponding Platform - Configuration",
            "物料命名模版"
        )



    @frappe.whitelist()
    def 测试亚马逊api(self, account_name=None):
        # 1. 找到被点击的那一行子表数据
        子表行 = None
        for row in self.亚马逊api:  # 假设子表字段名是“亚马逊api”
            if row.name == account_name:
                子表行 = row
                break
        
        if not 子表行:
            return {"status": "error", "message": "找不到对应的行数据"}

        # 2. 正确获取中文命名的字段值
        # 普通 Data 类型的字段直接点出来
        客户端编码 = 子表行.get_password("客户端编码")
        
        # Password 类型的字段必须用 get_password 方法解密
        客户端密钥 = 子表行.get_password("客户端密钥")
        刷新令牌 = 子表行.get_password("刷新令牌")
        
        # 3. 换取 Access Token
        api链接 = "https://api.amazon.com/auth/o2/token"
        数据 = {
            "grant_type": "refresh_token",
            "refresh_token": 刷新令牌,
            "client_id": 客户端编码,
            "client_secret": 客户端密钥
        }
        
        try:
            # 1. 发送请求
            响应对象 = requests.post(api链接, data=数据, timeout=15)
            
            # 2. 获取 JSON 内容
            返回结果 = 响应对象.json()

            # 3. 判断响应状态码（要用响应对象的 status_code）
            if 响应对象.status_code == 200:
                # 提取临时令牌
                临时令牌 = 返回结果.get("access_token")
                
                # 获取当前时间 (格式如: 2026-03-11 21:15:30)
                当前时间 = frappe.utils.now()
                显示内容 = f"可用：{当前时间}"
                
                # 更新子表当前行的字段
                子表行.db_set("是否可用", 显示内容) 

                return {
                    "status": "success", 
                    "message": f"✅ 授权成功！{显示内容}"
                }
            else:
                # 授权失败的情况
                失败原因 = 返回结果.get("error_description") or 响应对象.text
                子表行.db_set("是否可用", f"不可用：{frappe.utils.now()}")
                
                return {
                    "status": "error", 
                    "message": f"授权失败: {失败原因}"
                }

        except Exception as e:
            return {"status": "error", "message": f"连接错误: {str(e)}"}


@frappe.whitelist()
def 查看排名仪表盘():
    # 1. 尝试从当前站点的配置中获取 Insights 的配置 (如果存在)
    # 很多 Frappe App 会把端口写在 site_config 里
    insights_config = frappe.conf.get("insights_config") or {}
    port = insights_config.get("port")
    
    # 2. 如果配置里没写，我们返回一个标记，让前端去处理
    return {
        "port": port,
        "path": "/insights/dashboards"
    }

# 不要缩进
@frappe.whitelist()
def 开启任务调度器():
    if is_scheduler_disabled():
        try:
            enable_scheduler() 
            frappe.db.commit() 
            # 记录日志方便追溯
            frappe.log_error("用户通过前端按钮开启了调度器", "系统维护")
            return "调度器已成功开启！定时任务现在开始排队。"
        except Exception as e:
            return f"开启失败: {str(e)}"
    else:
        return "调度器已经是开启状态。"




