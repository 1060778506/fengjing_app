# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


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
    def onload(self):
        # 2. Python 内部调用
        if not self.丰境_ai生成物料提示词:
            self.丰境_ai生成物料提示词 = self.STANDARD_PROMPT
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
