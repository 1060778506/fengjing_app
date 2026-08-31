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
    1. 键值逻辑：fj-前缀 + 核心拼音首字母 + 六位序列号。
    2. 名称逻辑：[颜色]-[营销名称(尺寸)-名称]-[材质]-[备注]。缺失写“未提及”。
    3. 差异化：序列号保持一致，微调键的字母缩写和名称备注的侧重点，生成 10 组。
    4. 禁令：严禁输出“物料号：”、“物料名：”、“第n组”等字样。严禁任何开场白。
    5. 禁止出现";"、"、"、"等符号。禁止出现“/”、“\”、“|”等符号。禁止出现“()”、“[]”等符号。

    # 正确输出示例（严格按照此空行格式）:
    fj-6.0inxfcx-000001
    不锈钢色-6.0in小方插销-不锈钢-带螺丝1个装

    fj-6.0inxfcx-000001
    不锈钢色-6.0in小方插销-不锈钢-含配套螺丝1个

    接下来请你处理以下自然语言：
    """
    亚马逊站点对应表 = """
    美国	Amazon.com	ATVPDKIKX0DER
    加拿大	Amazon.ca	A2EUQ1WTGCTBG2
    墨西哥	Amazon.com.mx	A1AM78C64UM0Y8
    巴西	Amazon.com.br	A2Q3Y263D00KWC
    爱尔兰	Amazon.ie	A28R8C7NBKEWEA
    西班牙	Amazon.es	A1RKKUPIHCS9HS
    英国	Amazon.co.uk	A1F83G8C2ARO7P
    法国	Amazon.fr	A13V1IB3VIYZZH
    比利时	Amazon.com.be	AMEN7PMS3EDWL
    荷兰	Amazon.nl	A1805IZSGTT6HS
    德国	Amazon.de	A1PA6795UKMFR9
    意大利	Amazon.it	APJ6JRA9NG5V4
    瑞典	Amazon.se	A2NODRKZP88ZB9
    南非	Amazon.co.za	AE08WJ6YKNBMC
    波兰	Amazon.pl	A1C3SOZRARQ6R3
    埃及	Amazon.eg	ARBP9OOSHTCHU
    土耳其	Amazon.com.tr	A33AVAJ2PDY3EV
    沙特阿拉伯	Amazon.sa	A17E79C6D8DWNP
    阿联酋	Amazon.ae	A2VIGQ35RCS4UG
    印度	Amazon.in	A21TJRUUN4KGV
    新加坡	Amazon.sg	A19VAU5U5O7RUS
    澳大利亚	Amazon.com.au	A39IBJ37TRP1C6
    日本	Amazon.co.jp	A1VC38T7YXB528
    """

    def validate(self):
        """阻止排名ASIN配置和Amazon API配置出现重复组合。"""
        已有组合 = set()
        for row in self.get("抓取asin配置的子表") or []:
            asin = str(row.get("需要抓取数据的asin") or "").strip().upper()
            店铺 = str(row.get("属于哪个店铺") or "").strip()
            if not asin or not 店铺:
                continue
            组合 = (asin, 店铺)
            if 组合 in 已有组合:
                frappe.throw(
                    _("ASIN {0} 在店铺 {1} 中重复，请只保留一行。").format(
                        frappe.bold(asin), frappe.bold(店铺)
                    )
                )
            已有组合.add(组合)

        已有API组合 = set()
        for row in self.get("亚马逊api") or []:
            店铺 = str(row.get("店铺选项") or "").strip()
            站点id = str(row.get("站点id") or "").strip().upper()
            if not 店铺 or not 站点id:
                continue
            组合 = (店铺, 站点id)
            if 组合 in 已有API组合:
                frappe.throw(
                    _("店铺 {0} 与站点ID {1} 的Amazon API配置重复，请只保留一行。").format(
                        frappe.bold(店铺), frappe.bold(站点id)
                    )
                )
            已有API组合.add(组合)

    def onload(self):
        #不管是不是空的都去写入
        self.站点id对应表 = self.亚马逊站点对应表
        # 2. Python 内部调用
        if not self.丰境_ai生成物料提示词:
            self.丰境_ai生成物料提示词 = self.STANDARD_PROMPT
        #if not self.站点id对应表:
        #    self.站点id对应表 = self.亚马逊站点对应表
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




