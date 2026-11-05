# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

from urllib.parse import urlsplit
from textwrap import dedent

import frappe
import requests
from frappe.model.document import Document


CONFIG_DOCTYPE = "Fengjing - Product Corresponding Platform - Configuration"


class TeamorouterRequestError(Exception):
    pass


def _get_teamorouter_config(row_name):
    frappe.has_permission(CONFIG_DOCTYPE, "read", throw=True)
    doc = frappe.get_doc(CONFIG_DOCTYPE)
    row = next(
        (item for item in doc.get("丰境_ai配置页面") or [] if item.name == row_name),
        None,
    )
    if not row or row.get("丰境_ai来源") != "Teamorouter":
        frappe.throw("找不到已保存的 Teamorouter 配置，请先保存配置。")

    key = (row.get("丰境_ai秘钥") or "").strip()
    if not key:
        frappe.throw("请先填写 Teamorouter API 秘钥。")

    configured_url = (row.get("丰境_api链接") or "https://api.teamorouter.ai/v1").strip()
    parsed = urlsplit(configured_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"api.teamorouter.cn", "api.teamorouter.ai"}
        or parsed.port is not None
        or parsed.path.rstrip("/") not in {"", "/v1", "/v1/chat/completions"}
        or parsed.query
        or parsed.fragment
    ):
        frappe.throw("Teamorouter API 链接必须使用官方接口地址。")

    return key, "https://api.teamorouter.ai/v1", row


def _teamorouter_json(method, url, key, **kwargs):
    try:
        response = requests.request(
            method,
            url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=(10, 180) if method == "POST" else (10, 20),
            **kwargs,
        )
    except requests.Timeout as exc:
        wait = "3 分钟" if method == "POST" else "20 秒"
        raise TeamorouterRequestError(
            f"Teamorouter 在{wait}内没有返回结果，请稍后重试或选择响应更快的模型。"
        ) from exc
    except requests.RequestException as exc:
        raise TeamorouterRequestError(
            f"Teamorouter 网络请求失败：{exc.__class__.__name__}。请稍后重试。"
        ) from exc

    try:
        data = response.json()
    except ValueError:
        raise TeamorouterRequestError(
            f"Teamorouter 返回了无法解析的响应（HTTP {response.status_code}）。"
        )

    if not response.ok:
        error = data.get("error") if isinstance(data, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        message = message or (data.get("message") if isinstance(data, dict) else None)
        raise TeamorouterRequestError(
            f"Teamorouter 请求失败（HTTP {response.status_code}）：{str(message or '未知错误')[:300]}"
        )
    return data


@frappe.whitelist(methods=["POST"])
def get_teamorouter_models(row_name):
    key, base_url, _ = _get_teamorouter_config(row_name)
    try:
        data = _teamorouter_json("GET", f"{base_url}/models", key)
    except TeamorouterRequestError as exc:
        return {"error": str(exc)}
    return {"models": list(dict.fromkeys(
        str(item.get("id", "")).strip()
        for item in data.get("data", [])
        if isinstance(item, dict) and item.get("id")
    ))}


@frappe.whitelist(methods=["POST"])
def complete_teamorouter_prompt(row_name, prompt, model=None):
    key, base_url, row = _get_teamorouter_config(row_name)
    prompt = str(prompt or "").strip()
    model = str(model or row.get("中转站模型") or "").strip()
    if not prompt or len(prompt) > 50000:
        frappe.throw("AI 提示词为空或过长。")
    if not model or len(model) > 128:
        frappe.throw("请先选择 Teamorouter 模型。")

    try:
        data = _teamorouter_json(
            "POST",
            f"{base_url}/chat/completions",
            key,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1024,
                "stream": False,
            },
        )
    except TeamorouterRequestError as exc:
        return {"error": str(exc)}
    choices = data.get("choices") or []
    answer = (choices[0].get("message") or {}).get("content") if choices else None
    if not answer:
        return {"error": "接口返回成功，但没有找到模型回答。"}
    return {"answer": str(answer).strip()}


# 如果是新系统就自动填充提示词
class FengjingProductCorrespondingPlatformConfiguration(Document):
    # 1. 定义一个类常量，作为唯一的事实来源
    默认物料命名模版 = """[颜色]-[营销名称(尺寸)-名称]-[材质]-[备注]"""
    STANDARD_PROMPT = dedent("""
        # Role
        你是一个专业的 ERP 物料数据清洗专家。

        # Task
        根据【物料自然语言输入】和【本次固定序列号】，严格按照规范生成 10 组备选输出。

        # BOM 判断
        只检查【物料自然语言输入】是否包含 BOM（不区分大小写）。本提示词、输出示例和已有物料参考中的 BOM 字样都不能作为判断依据。
        如果输入末尾提供了【本次物料类型】，以该类型为准；普通物料即使参考里有 BOM，也绝不能使用 BOM 前缀。
        如果包含 BOM，物料 ID 必须以 fj-bom- 开头，物料名称必须以 BOM- 开头。
        如果不包含 BOM，物料 ID 以 fj- 开头，物料名称不添加 BOM-。
        同一次生成的 10 组结果必须采用同一种格式，不能混用。

        # Output Format
        每组严格输出两行，组与组之间空一行，严禁解释。
        普通物料：
        fj-[关键信息拼音首字母(含尺寸)]-[六位序列号]
        [颜色]-[营销名称(尺寸)-名称]-[材质]-[备注]

        BOM 物料：
        fj-bom-[关键信息拼音首字母(含尺寸)]-[六位序列号]
        BOM-[颜色]-[营销名称(尺寸)-名称]-[材质]-[备注]

        # Rules
        1. 物料 ID 使用对应前缀、关键信息缩写和输入提供的六位固定序列号。10 组序列号完全一致，禁止自行计算或修改。
        2. 名称按照对应格式填写；缺失的信息写“未提及”。
        3. 10 组可以微调缩写和备注侧重点，但不得改变 BOM 判断结果。
        4. 实际输出只写 10 组结果；严禁输出“物料号：”“物料名：”“第n组”、格式标题或开场白。
        5. 实际生成的物料 ID 和名称中，禁止出现分号、顿号、斜杠、反斜杠、竖线或各类括号。

        # 普通物料输出示例
        fj-6.0inxfcx-000001
        不锈钢色-6.0in小方插销-不锈钢-带螺丝1个装

        fj-6.0inxfcx-000001
        不锈钢色-6.0in小方插销-不锈钢-含配套螺丝1个

        # BOM 物料输出示例
        fj-bom-6.0inxfcx-000001
        BOM-不锈钢色-6.0in小方插销-不锈钢-带螺丝1个装

        接下来请处理【物料自然语言输入】。
    """).strip()

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
