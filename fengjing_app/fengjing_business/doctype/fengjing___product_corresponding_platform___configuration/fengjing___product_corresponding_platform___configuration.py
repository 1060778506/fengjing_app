# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import hashlib
import json
import time

import frappe
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from frappe.model.document import Document
from frappe.utils import get_datetime, get_system_timezone
from frappe import _


def _系统时间转utc(时间值):
    """把ERPNext界面填写的系统本地时间转换成带时区的UTC时间。"""
    if not 时间值:
        return None
    时间 = get_datetime(时间值)
    if 时间.tzinfo is None:
        时间 = 时间.replace(tzinfo=ZoneInfo(get_system_timezone()))
    return 时间.astimezone(timezone.utc)


def _utc转系统时间字符串(时间值):
    if not 时间值:
        return None
    return 时间值.astimezone(ZoneInfo(get_system_timezone())).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _确保数据库连接():
    """长时间等待后恢复可能已失效的MariaDB连接，供现有Ozon任务使用。"""
    try:
        frappe.db.sql("select 1")
    except Exception:
        try:
            frappe.db.close()
        except Exception:
            pass
        frappe.connect()


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
    def validate(self):
        """对尚未拆分的 Ozon 店铺子表做唯一性校验。"""
        已有ozon组合 = set()
        for row in self.get("table_wckx") or []:
            店铺 = str(row.get("店铺选项") or "").strip()
            ozon_id = str(row.get("ozon_id") or "").strip()
            row.ozon_id = ozon_id
            if not 店铺 or not ozon_id:
                continue
            组合 = (店铺, ozon_id)
            if 组合 in 已有ozon组合:
                frappe.throw(
                    _("店铺 {0} 与Ozon ID {1} 的配置重复，请只保留一行。").format(
                        frappe.bold(店铺), frappe.bold(ozon_id)
                    )
                )
            已有ozon组合.add(组合)

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



    @frappe.whitelist()
    def 测试ozon_api(self, account_name=None):
        """测试当前 Ozon 子表行的 Seller API 与 Performance API 凭证。"""
        子表行 = next(
            (
                row
                for row in (self.get("table_wckx") or [])
                if row.name == account_name
            ),
            None,
        )
        if not 子表行:
            return {"status": "error", "message": "找不到对应的 Ozon API 配置行。"}

        ozon_id = str(子表行.get("ozon_id") or "").strip()
        ozon_秘钥 = str(子表行.get("ozon_秘钥") or "").strip()
        performance客户端id = str(
            子表行.get("performance_client_id") or ""
        ).strip()
        performance_秘钥 = str(
            子表行.get("performance_api_秘钥") or ""
        ).strip()

        缺少字段 = [
            字段名
            for 字段名, 字段值 in (
                ("Ozon ID", ozon_id),
                ("Ozon 秘钥", ozon_秘钥),
                ("Performance Client ID", performance客户端id),
                ("Performance API 秘钥", performance_秘钥),
            )
            if not 字段值
        ]
        if 缺少字段:
            return {
                "status": "error",
                "message": f"无法测试，缺少：{'、'.join(缺少字段)}",
            }

        def 响应错误摘要(响应):
            try:
                数据 = 响应.json()
                内容 = (
                    数据.get("message")
                    or 数据.get("error_description")
                    or 数据.get("error")
                    or 数据
                )
                if isinstance(内容, (dict, list)):
                    内容 = json.dumps(内容, ensure_ascii=False)
                return str(内容)[:500]
            except (ValueError, TypeError, AttributeError):
                return str(响应.text or "无响应内容")[:500]

        测试结果 = []

        # Seller API：验证 Client-Id 与 Api-Key，并读取最多一个商品。
        try:
            seller响应 = requests.post(
                "https://api-seller.ozon.ru/v3/product/list",
                headers={
                    "Client-Id": ozon_id,
                    "Api-Key": ozon_秘钥,
                    "Content-Type": "application/json",
                },
                json={"filter": {"visibility": "ALL"}, "limit": 1},
                timeout=30,
            )
            if seller响应.ok:
                测试结果.append(
                    {
                        "name": "Seller API",
                        "success": True,
                        "message": "连接成功，商品接口可用。",
                    }
                )
            else:
                测试结果.append(
                    {
                        "name": "Seller API",
                        "success": False,
                        "message": (
                            f"HTTP {seller响应.status_code}："
                            f"{响应错误摘要(seller响应)}"
                        ),
                    }
                )
        except requests.RequestException as exc:
            测试结果.append(
                {
                    "name": "Seller API",
                    "success": False,
                    "message": f"网络连接失败：{exc}",
                }
            )

        try:
            performance响应 = requests.post(
                "https://api-performance.ozon.ru/api/client/token",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json={
                    "client_id": performance客户端id,
                    "client_secret": performance_秘钥,
                    "grant_type": "client_credentials",
                },
                timeout=30,
            )
            performance数据 = (
                performance响应.json() if performance响应.content else {}
            )
            if performance响应.ok and performance数据.get("access_token"):
                测试结果.append(
                    {
                        "name": "Performance API",
                        "success": True,
                        "message": "认证成功，已取得临时访问令牌。",
                    }
                )
            else:
                测试结果.append(
                    {
                        "name": "Performance API",
                        "success": False,
                        "message": (
                            f"HTTP {performance响应.status_code}："
                            f"{响应错误摘要(performance响应)}"
                        ),
                    }
                )
        except (requests.RequestException, ValueError) as exc:
            测试结果.append(
                {
                    "name": "Performance API",
                    "success": False,
                    "message": f"连接或响应解析失败：{exc}",
                }
            )

        成功数量 = sum(1 for 结果 in 测试结果 if 结果["success"])
        状态 = (
            "success"
            if 成功数量 == len(测试结果)
            else "partial"
            if 成功数量
            else "error"
        )
        return {
            "status": 状态,
            "results": 测试结果,
            "message": f"已完成测试：{成功数量}/{len(测试结果)} 项成功。",
        }


OZON配置子表 = "Ozon Store API Sub-table"
OZON配置主表 = "Fengjing - Product Corresponding Platform - Configuration"
OZON历史单次窗口天数 = 30
OZON安全延迟分钟 = 5


def _ozon_utc_string(value):
    return value.astimezone(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _取得ozon配置行(行名称):
    _确保数据库连接()
    主表 = frappe.get_single(OZON配置主表)
    行 = next(
        (row for row in (主表.get("table_wckx") or []) if row.name == 行名称),
        None,
    )
    if not 行:
        raise ValueError(f"找不到Ozon订单配置行：{行名称}")
    return 主表, 行


def _更新ozon配置状态(行名称, **字段):
    _确保数据库连接()
    有效字段 = set(frappe.get_meta(OZON配置子表).get_valid_columns())
    有效值 = {key: value for key, value in 字段.items() if key in 有效字段}
    if 有效值:
        frappe.db.set_value(
            OZON配置子表, 行名称, 有效值, update_modified=False
        )
        frappe.db.commit()


def _ozon任务锁(行名称):
    cache = frappe.cache()
    return cache.lock(
        cache.make_key(f"fengjing:ozon-orders:{行名称}"),
        timeout=6 * 60 * 60,
        blocking_timeout=0,
    )


def _ozon错误摘要(响应):
    try:
        数据 = 响应.json()
        内容 = (
            数据.get("message")
            or 数据.get("error_description")
            or 数据.get("error")
            or 数据
        )
        if isinstance(内容, (dict, list)):
            内容 = json.dumps(内容, ensure_ascii=False)
        return str(内容)[:1000]
    except (ValueError, TypeError, AttributeError):
        return str(响应.text or "无响应内容")[:1000]


def _发送ozon订单请求(配置行, 地址, 数据):
    headers = {
        "Client-Id": str(配置行.get("ozon_id") or "").strip(),
        "Api-Key": str(配置行.get("ozon_秘钥") or "").strip(),
        "Content-Type": "application/json",
    }
    if not headers["Client-Id"] or not headers["Api-Key"]:
        raise ValueError("Ozon订单同步缺少Ozon ID或Ozon秘钥")

    最后响应 = None
    for 序号, 默认等待秒数 in enumerate((2, 5, 15, 30, 60, 120)):
        try:
            响应 = requests.post(
                地址, headers=headers, json=数据, timeout=60
            )
        except requests.RequestException:
            if 序号 == 5:
                raise
            time.sleep(默认等待秒数)
            continue
        最后响应 = 响应
        if 响应.status_code == 429:
            retry_after = 响应.headers.get("Retry-After")
            try:
                等待秒数 = max(float(retry_after), 默认等待秒数)
            except (TypeError, ValueError):
                等待秒数 = 默认等待秒数
            if 序号 < 5:
                time.sleep(min(等待秒数, 300))
                continue
        if 响应.status_code in {500, 502, 503, 504} and 序号 < 5:
            time.sleep(默认等待秒数)
            continue
        return 响应
    return 最后响应


def _识别ozon跨境履约方式(订单):
    线索 = " ".join(
        str(订单.get(key) or "")
        for key in (
            "integration_type_flow",
            "tpl_integration_type",
            "container_sort_type",
        )
    ).lower()
    if "realfbs" in 线索 or "real_fbs" in 线索:
        return "realFBS"
    if "fbp" in 线索:
        return "FBP"
    return "FBS"


def _累计ozon保存结果(汇总, 保存结果):
    汇总["新建"] += 保存结果.get("created", 0)
    汇总["更新"] += 保存结果.get("updated", 0)
    汇总["历史归档"] += 保存结果.get("archived", 0)


def _抓取ozon_fbs窗口(配置行, 同步类型, 开始utc, 结束utc):
    from fengjing_app.fengjing_business.doctype.ozon_order_storage.ozon_order_storage import (
        保存ozon订单,
    )

    汇总 = {"新建": 0, "更新": 0, "历史归档": 0, "分页": 0, "发货单": 0}
    offset = 0
    limit = 1000
    while True:
        数据 = {
            "dir": "ASC",
            "filter": {
                "since": _ozon_utc_string(开始utc),
                "to": _ozon_utc_string(结束utc),
            },
            "limit": limit,
            "offset": offset,
            "with": {
                "analytics_data": True,
                "barcodes": True,
                "financial_data": True,
                "translit": True,
            },
        }
        响应 = _发送ozon订单请求(
            配置行, "https://api-seller.ozon.ru/v3/posting/fbs/list", 数据
        )
        if 响应 is None or 响应.status_code != 200:
            状态码 = 响应.status_code if 响应 is not None else "无响应"
            raise RuntimeError(
                f"Ozon FBS订单接口失败（HTTP {状态码}）："
                f"{_ozon错误摘要(响应) if 响应 is not None else ''}"
            )
        payload = 响应.json() or {}
        result = payload.get("result") or {}
        postings = result.get("postings") or []
        for posting in postings:
            保存结果 = 保存ozon订单(
                posting,
                str(配置行.get("店铺选项") or "").strip(),
                str(配置行.get("ozon_id") or "").strip(),
                _识别ozon跨境履约方式(posting),
                同步类型,
            )
            _累计ozon保存结果(汇总, 保存结果)
        frappe.db.commit()
        汇总["分页"] += 1
        汇总["发货单"] += len(postings)
        if not result.get("has_next"):
            break
        if not postings:
            raise RuntimeError("Ozon FBS接口返回has_next，但当前分页没有订单")
        offset += len(postings)
    return 汇总


def _抓取ozon_fbo窗口(配置行, 同步类型, 开始utc, 结束utc):
    from fengjing_app.fengjing_business.doctype.ozon_order_storage.ozon_order_storage import (
        保存ozon订单,
    )

    汇总 = {"新建": 0, "更新": 0, "历史归档": 0, "分页": 0, "发货单": 0}
    offset = 0
    limit = 1000
    while True:
        数据 = {
            "dir": "ASC",
            "filter": {
                "since": _ozon_utc_string(开始utc),
                "to": _ozon_utc_string(结束utc),
            },
            "limit": limit,
            "offset": offset,
            "translit": True,
            "with": {"analytics_data": True, "financial_data": True},
        }
        响应 = _发送ozon订单请求(
            配置行, "https://api-seller.ozon.ru/v2/posting/fbo/list", 数据
        )
        if 响应 is None or 响应.status_code != 200:
            状态码 = 响应.status_code if 响应 is not None else "无响应"
            raise RuntimeError(
                f"Ozon FBO订单接口失败（HTTP {状态码}）："
                f"{_ozon错误摘要(响应) if 响应 is not None else ''}"
            )
        payload = 响应.json() or {}
        result = payload.get("result") or []
        if isinstance(result, dict):
            postings = result.get("postings") or []
            has_next = bool(result.get("has_next"))
        else:
            postings = result if isinstance(result, list) else []
            has_next = len(postings) >= limit
        for posting in postings:
            保存结果 = 保存ozon订单(
                posting,
                str(配置行.get("店铺选项") or "").strip(),
                str(配置行.get("ozon_id") or "").strip(),
                "FBO",
                同步类型,
            )
            _累计ozon保存结果(汇总, 保存结果)
        frappe.db.commit()
        汇总["分页"] += 1
        汇总["发货单"] += len(postings)
        if not has_next:
            break
        if not postings:
            raise RuntimeError("Ozon FBO接口需要继续分页，但当前分页没有订单")
        offset += len(postings)
    return 汇总


def _同步ozon订单窗口(配置行, 同步类型, 开始utc, 结束utc):
    if 开始utc >= 结束utc:
        return {"新建": 0, "更新": 0, "历史归档": 0, "分页": 0, "发货单": 0}
    if not frappe.utils.cint(配置行.get("抓取fbs及跨境订单")) and not frappe.utils.cint(
        配置行.get("抓取fbo订单")
    ):
        raise ValueError("请至少开启FBS/跨境订单或FBO订单中的一种")

    汇总 = {"新建": 0, "更新": 0, "历史归档": 0, "分页": 0, "发货单": 0}
    if frappe.utils.cint(配置行.get("抓取fbs及跨境订单")):
        结果 = _抓取ozon_fbs窗口(配置行, 同步类型, 开始utc, 结束utc)
        for key in 汇总:
            汇总[key] += 结果[key]
    if frappe.utils.cint(配置行.get("抓取fbo订单")):
        结果 = _抓取ozon_fbo窗口(配置行, 同步类型, 开始utc, 结束utc)
        for key in 汇总:
            汇总[key] += 结果[key]
    return 汇总


def _同步ozon订单范围(配置行, 同步类型, 开始utc, 结束utc):
    """Split every query into <=30-day windows and aggregate all results."""
    汇总 = {"新建": 0, "更新": 0, "历史归档": 0, "分页": 0, "发货单": 0, "窗口": 0}
    游标 = 开始utc
    while 游标 < 结束utc:
        窗口结束 = min(游标 + timedelta(days=OZON历史单次窗口天数), 结束utc)
        结果 = _同步ozon订单窗口(配置行, 同步类型, 游标, 窗口结束)
        for key in ("新建", "更新", "历史归档", "分页", "发货单"):
            汇总[key] += 结果[key]
        汇总["窗口"] += 1
        游标 = 窗口结束
    return 汇总


def _ozon历史计划(配置行):
    开始 = 配置行.get("历史已完整同步到") or 配置行.get("历史同步开始时间")
    结束 = 配置行.get("历史同步结束时间")
    if not 开始 or not 结束:
        raise ValueError("请先填写历史同步开始时间和历史同步结束时间")
    开始utc = _系统时间转utc(开始)
    配置结束utc = _系统时间转utc(结束)
    安全结束utc = datetime.now(timezone.utc) - timedelta(minutes=OZON安全延迟分钟)
    可用结束utc = min(配置结束utc, 安全结束utc)
    if 开始utc >= 可用结束utc:
        return {
            "status": "complete" if 开始utc >= 配置结束utc else "waiting",
            "cursor": 开始utc,
            "configured_end": 配置结束utc,
            "available_end": 可用结束utc,
        }
    return {
        "status": "ready",
        "cursor": 开始utc,
        "window_end": min(
            开始utc + timedelta(days=OZON历史单次窗口天数),
            可用结束utc,
        ),
        "configured_end": 配置结束utc,
        "available_end": 可用结束utc,
    }


def _ozon历史进度(配置行, 已完成utc):
    开始utc = _系统时间转utc(配置行.get("历史同步开始时间"))
    结束utc = _系统时间转utc(配置行.get("历史同步结束时间"))
    总秒数 = max((结束utc - 开始utc).total_seconds(), 1)
    完成秒数 = max(min((已完成utc - 开始utc).total_seconds(), 总秒数), 0)
    进度 = round(完成秒数 / 总秒数 * 100, 2)
    # 游标尚未真正到达结束时间时，不提前显示 100%。
    return 100 if 已完成utc >= 结束utc else min(进度, 99.99)


def _ozon固定核对下次时间(当前时间, 间隔天数):
    return get_datetime(当前时间).replace(
        hour=2, minute=59, second=0, microsecond=0
    ) + timedelta(days=max(frappe.utils.cint(间隔天数), 1))


@frappe.whitelist()
def 启动ozon历史订单同步(配置行名称):
    _, 行 = _取得ozon配置行(配置行名称)
    if not frappe.utils.cint(行.get("开启订单同步")):
        frappe.throw("请先开启订单同步总开关并保存")
    if not 行.get("历史同步开始时间") or not 行.get("历史同步结束时间"):
        frappe.throw("请先填写历史同步开始时间和历史同步结束时间")
    if _系统时间转utc(行.get("历史同步开始时间")) >= _系统时间转utc(
        行.get("历史同步结束时间")
    ):
        frappe.throw("历史同步开始时间必须早于结束时间")
    _更新ozon配置状态(
        配置行名称,
        历史同步状态="等待执行",
        当前任务状态="等待执行",
        当前执行类型="历史订单",
        历史同步最近错误="",
        最近错误="",
    )
    _ozon任务入队(配置行名称, "历史订单")
    return {"status": "queued", "message": "Ozon历史订单同步已进入后台队列"}


@frappe.whitelist()
def 启动ozon最新订单同步(配置行名称):
    _, 行 = _取得ozon配置行(配置行名称)
    if not frappe.utils.cint(行.get("开启订单同步")):
        frappe.throw("请先开启订单同步总开关并保存")
    _更新ozon配置状态(
        配置行名称,
        当前任务状态="等待执行",
        当前执行类型="最新订单增量",
        最近错误="",
    )
    _ozon任务入队(配置行名称, "最新订单增量")
    return {"status": "queued", "message": "Ozon最新订单同步已进入后台队列"}


def 执行ozon订单同步任务(配置行名称, 同步类型):
    lock = _ozon任务锁(配置行名称)
    if not lock.acquire(blocking=False):
        return {"status": "busy", "message": "该Ozon店铺已有订单同步任务运行"}

    开始时间 = frappe.utils.now_datetime()
    try:
        _更新ozon配置状态(
            配置行名称,
            当前任务状态="运行中",
            当前执行类型=同步类型,
            当前任务开始时间=开始时间,
            最近错误="",
        )
        _, 行 = _取得ozon配置行(配置行名称)
        if not frappe.utils.cint(行.get("开启订单同步")):
            raise ValueError("Ozon订单同步总开关已经关闭")

        if 同步类型 == "历史订单":
            _更新ozon配置状态(配置行名称, 历史同步状态="运行中")
            总汇总 = {"新建": 0, "更新": 0, "历史归档": 0, "分页": 0, "发货单": 0, "窗口": 0}
            while True:
                _, 行 = _取得ozon配置行(配置行名称)
                计划 = _ozon历史计划(行)
                if 计划["status"] != "ready":
                    完成 = 计划["status"] == "complete"
                    _更新ozon配置状态(
                        配置行名称,
                        历史同步状态="已完成" if 完成 else "已暂停",
                        历史同步进度=100 if 完成 else 行.get("历史同步进度"),
                        当前任务状态="成功",
                        当前执行类型="",
                        历史同步最近错误="",
                    )
                    return {"status": 计划["status"], "summary": 总汇总}

                本段 = _同步ozon订单窗口(
                    行, 同步类型, 计划["cursor"], 计划["window_end"]
                )
                for key in ("新建", "更新", "历史归档", "分页", "发货单"):
                    总汇总[key] += 本段[key]
                总汇总["窗口"] += 1
                _更新ozon配置状态(
                    配置行名称,
                    历史已完整同步到=_utc转系统时间字符串(计划["window_end"]),
                    历史同步进度=_ozon历史进度(行, 计划["window_end"]),
                    历史新增数量=总汇总["新建"],
                    历史更新数量=总汇总["更新"],
                )

        _, 行 = _取得ozon配置行(配置行名称)
        安全结束utc = datetime.now(timezone.utc) - timedelta(minutes=OZON安全延迟分钟)
        if 同步类型 == "最新订单增量":
            回看分钟 = max(frappe.utils.cint(行.get("最新订单回看分钟")), 1)
            断点 = 行.get("自动同步断点")
            if 断点:
                开始utc = _系统时间转utc(断点) - timedelta(minutes=回看分钟)
            else:
                开始utc = 安全结束utc - timedelta(minutes=回看分钟)
        else:
            天数 = {
                "7天核对": 7,
                "14天核对": 14,
                "30天核对": 30,
                "90天核对": 90,
                "180天核对": 180,
            }[同步类型]
            开始utc = 安全结束utc - timedelta(days=天数)

        汇总 = _同步ozon订单范围(
            行, 同步类型, 开始utc, 安全结束utc
        )
        当前时间 = frappe.utils.now_datetime()
        更新字段 = {
            "当前任务状态": "成功",
            "当前执行类型": "",
            "最近错误": "",
        }
        if 同步类型 == "最新订单增量":
            间隔 = max(frappe.utils.cint(行.get("最新订单同步间隔分钟")), 1)
            更新字段.update(
                {
                    "自动同步断点": _utc转系统时间字符串(安全结束utc),
                    "上次自动同步时间": 当前时间,
                    "下次自动同步时间": frappe.utils.add_to_date(
                        当前时间, minutes=间隔
                    ),
                    "上次自动同步结果": json.dumps(
                        {"状态": "成功", **汇总}, ensure_ascii=False
                    ),
                }
            )
        else:
            前缀 = 同步类型.replace("核对", "")
            间隔天数 = max(
                frappe.utils.cint(行.get(f"{前缀}核对间隔天数")), 1
            )
            更新字段[f"{前缀}最后核对时间"] = 当前时间
            更新字段[f"{前缀}下次核对时间"] = _ozon固定核对下次时间(
                当前时间, 间隔天数
            )
        _更新ozon配置状态(配置行名称, **更新字段)
        return {"status": "success", "summary": 汇总}
    except Exception as exc:
        错误 = str(exc)[:2000]
        更新字段 = {
            "当前任务状态": "失败",
            "当前执行类型": "",
            "最近错误": 错误,
        }
        if 同步类型 == "历史订单":
            更新字段.update(
                {"历史同步状态": "失败", "历史同步最近错误": 错误}
            )
        elif 同步类型 == "最新订单增量":
            更新字段.update(
                {
                    "上次自动同步时间": frappe.utils.now_datetime(),
                    "下次自动同步时间": frappe.utils.add_to_date(
                        frappe.utils.now_datetime(), minutes=15
                    ),
                    "上次自动同步结果": f"失败：{错误[:1800]}",
                }
            )
        else:
            前缀 = 同步类型.replace("核对", "")
            更新字段[f"{前缀}下次核对时间"] = frappe.utils.add_to_date(
                frappe.utils.now_datetime(), minutes=15
            )
        _更新ozon配置状态(配置行名称, **更新字段)
        frappe.logger("ozon_orders", allow_site=True).exception(
            "Ozon订单同步失败：配置行=%s，类型=%s",
            配置行名称,
            同步类型,
        )
        raise
    finally:
        try:
            lock.release()
        except Exception:
            pass


def _ozon任务入队(行名称, 同步类型):
    job_suffix = hashlib.sha256(
        f"{行名称}|{同步类型}".encode("utf-8")
    ).hexdigest()[:20]
    frappe.enqueue(
        执行ozon订单同步任务,
        queue="long",
        timeout=6 * 60 * 60,
        enqueue_after_commit=True,
        job_id=f"ozon-orders-{job_suffix}",
        deduplicate=True,
        配置行名称=行名称,
        同步类型=同步类型,
    )


def 定时执行ozon订单同步():
    """Queue due Ozon increment and tiered status-recheck jobs."""
    主表 = frappe.get_single(OZON配置主表)
    当前时间 = frappe.utils.now_datetime()
    for 行 in 主表.get("table_wckx") or []:
        if not frappe.utils.cint(行.get("开启订单同步")):
            continue
        try:
            if 行.get("当前任务状态") == "运行中":
                开始时间 = 行.get("当前任务开始时间")
                if not 开始时间 or (
                    当前时间 - get_datetime(开始时间)
                ).total_seconds() < 6 * 60 * 60:
                    continue

            历史状态 = 行.get("历史同步状态")
            # 容器重启或 worker 中断时，历史状态可能遗留为“运行中”。
            # 只要当前已没有正在运行的历史任务，就根据游标自动恢复。
            if 历史状态 == "运行中" and not (
                行.get("当前任务状态") == "运行中"
                and 行.get("当前执行类型") == "历史订单"
            ):
                历史计划 = _ozon历史计划(行)
                if 历史计划["status"] == "complete":
                    _更新ozon配置状态(
                        行.name,
                        历史同步状态="已完成",
                        历史同步进度=100,
                        历史同步最近错误="",
                    )
                    历史状态 = "已完成"
                elif 历史计划["status"] == "waiting":
                    _更新ozon配置状态(行.name, 历史同步状态="已暂停")
                    历史状态 = "已暂停"
                else:
                    _更新ozon配置状态(行.name, 历史同步状态="等待执行")
                    历史状态 = "等待执行"

            if 历史状态 in {"等待执行", "已暂停"}:
                if 行.get("历史同步开始时间") and 行.get("历史同步结束时间"):
                    历史游标 = _系统时间转utc(
                        行.get("历史已完整同步到")
                        or 行.get("历史同步开始时间")
                    )
                    可抓取结束 = min(
                        _系统时间转utc(行.get("历史同步结束时间")),
                        datetime.now(timezone.utc)
                        - timedelta(minutes=OZON安全延迟分钟),
                    )
                    if 历史游标 < 可抓取结束:
                        _ozon任务入队(行.name, "历史订单")
                        continue
            if not frappe.utils.cint(行.get("开启自动同步")):
                continue
            下次增量 = 行.get("下次自动同步时间")
            if not 下次增量 or get_datetime(下次增量) <= 当前时间:
                _ozon任务入队(行.name, "最新订单增量")
                continue
            for 前缀 in ("7天", "14天", "30天", "90天", "180天"):
                if not frappe.utils.cint(行.get(f"启用{前缀}核对")):
                    continue
                下次 = 行.get(f"{前缀}下次核对时间")
                if not 下次 or get_datetime(下次) <= 当前时间:
                    _ozon任务入队(行.name, f"{前缀}核对")
                    break
        except Exception:
            frappe.logger("ozon_orders", allow_site=True).exception(
                "Ozon订单定时任务入队失败：配置行=%s", 行.name
            )

