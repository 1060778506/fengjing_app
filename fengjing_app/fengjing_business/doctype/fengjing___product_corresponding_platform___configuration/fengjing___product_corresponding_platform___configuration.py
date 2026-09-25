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
from frappe.utils.scheduler import enable_scheduler, is_scheduler_disabled
from frappe import _


亚马逊订单安全延迟分钟 = 5
亚马逊订单续跑重叠分钟 = 10
亚马逊历史订单单次窗口小时 = 30 * 24
亚马逊订单突发请求上限 = 15
亚马逊订单令牌恢复秒数 = 180
亚马逊订单增量开始小时 = 6


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


def _亚马逊utc字符串(时间值):
    """Orders API使用的RFC3339 UTC格式。"""
    return 时间值.astimezone(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def 规划亚马逊历史订单时间窗口(
    历史同步开始时间,
    历史同步结束时间,
    历史已完整同步到=None,
    当前utc=None,
    安全延迟分钟=亚马逊订单安全延迟分钟,
    重叠分钟=亚马逊订单续跑重叠分钟,
    单次窗口小时=亚马逊历史订单单次窗口小时,
):
    """只规划下一段历史订单查询时间，不请求API，也不推进同步断点。

    配置页面中的时间按ERPNext系统时区理解；发给Amazon前统一转为UTC。
    请求结束边界不会超过“当前UTC-安全延迟”，因此不会把未来时间误记为完成。
    已有断点时向前重叠少量时间，后续写入程序以Amazon订单号更新去重。
    """
    if not 历史同步开始时间 or not 历史同步结束时间:
        raise ValueError("必须同时填写历史同步开始时间和历史同步结束时间")

    配置开始utc = _系统时间转utc(历史同步开始时间)
    配置结束utc = _系统时间转utc(历史同步结束时间)
    if 配置结束utc <= 配置开始utc:
        raise ValueError("历史同步结束时间必须晚于历史同步开始时间")

    if 当前utc:
        当前utc值 = get_datetime(当前utc)
        if 当前utc值.tzinfo is None:
            当前utc值 = 当前utc值.replace(tzinfo=timezone.utc)
        当前utc值 = 当前utc值.astimezone(timezone.utc)
    else:
        当前utc值 = datetime.now(timezone.utc)

    安全截止utc = 当前utc值 - timedelta(minutes=max(int(安全延迟分钟), 0))
    本次可用结束utc = min(配置结束utc, 安全截止utc)
    已完成utc = _系统时间转utc(历史已完整同步到)

    # 断点已经到达当前安全边界时不能再向前重叠，否则会反复抓取同一小段时间。
    if 已完成utc and 已完成utc >= 本次可用结束utc:
        return {
            "status": "waiting" if 配置结束utc > 安全截止utc else "complete",
            "message": (
                "已经同步到当前安全边界，等待结束时间到达"
                if 配置结束utc > 安全截止utc
                else "历史订单区间已经完整同步"
            ),
            "配置结束时间": _utc转系统时间字符串(配置结束utc),
            "安全可抓取到": _utc转系统时间字符串(安全截止utc),
            "历史已完整同步到": _utc转系统时间字符串(已完成utc),
            "配置结束时间是否尚未到达": 配置结束utc > 安全截止utc,
        }

    if 已完成utc:
        本次开始utc = max(
            配置开始utc,
            已完成utc - timedelta(minutes=max(int(重叠分钟), 0)),
        )
    else:
        本次开始utc = 配置开始utc

    if 本次开始utc >= 本次可用结束utc:
        return {
            "status": "waiting" if 配置结束utc > 安全截止utc else "complete",
            "message": (
                "结束边界尚未到达安全抓取时间，等待后续继续"
                if 配置结束utc > 安全截止utc
                else "历史订单区间已经完整同步"
            ),
            "配置结束时间": _utc转系统时间字符串(配置结束utc),
            "安全可抓取到": _utc转系统时间字符串(安全截止utc),
            "历史已完整同步到": (
                _utc转系统时间字符串(已完成utc) if 已完成utc else None
            ),
            "配置结束时间是否尚未到达": 配置结束utc > 安全截止utc,
        }

    本次结束utc = min(
        本次可用结束utc,
        本次开始utc + timedelta(hours=max(int(单次窗口小时), 1)),
    )
    return {
        "status": "ready",
        "created_after": _亚马逊utc字符串(本次开始utc),
        "created_before": _亚马逊utc字符串(本次结束utc),
        "本次开始时间": _utc转系统时间字符串(本次开始utc),
        "本次结束时间": _utc转系统时间字符串(本次结束utc),
        "安全可抓取到": _utc转系统时间字符串(安全截止utc),
        "配置结束时间": _utc转系统时间字符串(配置结束utc),
        "配置结束时间是否尚未到达": 配置结束utc > 安全截止utc,
        "本窗口完整成功后可推进到": _utc转系统时间字符串(本次结束utc),
        "说明": "只有本窗口全部分页成功并写入后，才能推进历史同步断点",
    }

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
            # 不只用于比较，也把规范化结果真正保存回子表。
            row.需要抓取数据的asin = asin
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
            卖家记号 = str(row.get("卖家记号") or "").strip()
            # Marketplace ID 统一大写；Seller ID 只清理误输入的首尾空格。
            row.站点id = 站点id
            row.卖家记号 = 卖家记号
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
        from fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log import (
            亚马逊请求,
            获取SP_API区域地址,
        )

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
        站点id = str(子表行.站点id or "").strip().upper()
        卖家记号 = str(子表行.卖家记号 or "").strip()

        缺少字段 = [
            字段名 for 字段名, 字段值 in (
                ("客户端编码", 客户端编码),
                ("客户端密钥", 客户端密钥),
                ("刷新令牌", 刷新令牌),
                ("站点ID", 站点id),
                ("卖家记号", 卖家记号),
            )
            if not 字段值
        ]
        if 缺少字段:
            return {
                "status": "error",
                "message": f"无法测试，缺少：{'、'.join(缺少字段)}",
            }

        sp_api地址 = 获取SP_API区域地址(站点id)
        if not sp_api地址:
            return {
                "status": "error",
                "message": f"无法识别站点ID {站点id} 所属的 SP-API 区域。",
            }
        
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
            响应对象 = 亚马逊请求("POST", api链接, data=数据, timeout=15, retries=3)
            
            # 2. 获取 JSON 内容
            返回结果 = 响应对象.json()

            # 3. 判断响应状态码（要用响应对象的 status_code）
            if 响应对象.status_code == 200:
                # 提取临时令牌
                临时令牌 = 返回结果.get("access_token")
                if not 临时令牌:
                    子表行.db_set("是否可用", f"不可用：{frappe.utils.now()}")
                    return {
                        "status": "error",
                        "message": "Amazon 授权响应成功，但没有返回 Access Token。",
                    }

                # 令牌成功后，再验证当前站点、卖家记号和 Listings API 权限。
                listings地址 = f"{sp_api地址}/listings/2021-08-01/items/{卖家记号}"
                listings响应 = 亚马逊请求(
                    "GET",
                    listings地址,
                    headers={
                        "X-Amz-Access-Token": 临时令牌,
                        "Accept": "application/json",
                        "User-Agent": "FengjingAmazonApiTest/1.0",
                    },
                    params={
                        "marketplaceIds": 站点id,
                        "includedData": "summaries",
                        "pageSize": 1,
                    },
                    timeout=30,
                    retries=3,
                )
                if listings响应.status_code != 200:
                    子表行.db_set("是否可用", f"不可用：{frappe.utils.now()}")
                    失败摘要 = frappe.utils.escape_html(
                        (listings响应.text or "无响应内容")[:500]
                    )
                    return {
                        "status": "error",
                        "message": (
                            f"授权令牌可用，但 Listings API 测试失败（HTTP "
                            f"{listings响应.status_code}）：{失败摘要}"
                        ),
                    }

                # 获取当前时间 (格式如: 2026-03-11 21:15:30)
                当前时间 = frappe.utils.now()
                显示内容 = f"可用：{当前时间}"
                
                # 更新子表当前行的字段
                子表行.db_set("是否可用", 显示内容) 

                return {
                    "status": "success",
                    "message": (
                        f"✅ 完整测试成功：授权令牌、站点ID、卖家记号和 Listings API 均可用。"
                        f"<br>站点ID：{站点id}<br>区域地址：{sp_api地址}<br>{显示内容}"
                    )
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


订单配置子表 = "Amazon retrieves order configuration - sub-table"
订单配置主表 = "Fengjing - Product Corresponding Platform - Configuration"


def _取得订单配置行(行名称):
    _确保数据库连接()
    主表 = frappe.get_single(订单配置主表)
    行 = next(
        (row for row in (主表.get("亚马逊抓取订单配置表") or []) if row.name == 行名称),
        None,
    )
    if not 行:
        raise ValueError(f"找不到Amazon订单抓取配置行：{行名称}")
    return 主表, 行


def _确保数据库连接():
    """Amazon限流可能等待数分钟；继续写入前自动恢复失效的MariaDB连接。"""
    try:
        frappe.db.sql("select 1")
    except Exception:
        try:
            frappe.db.close()
        except Exception:
            pass
        frappe.connect()


def _更新订单配置状态(行名称, **字段):
    _确保数据库连接()
    有效字段 = set(frappe.get_meta(订单配置子表).get_valid_columns())
    字段 = {key: value for key, value in 字段.items() if key in 有效字段}
    if 字段:
        frappe.db.set_value(订单配置子表, 行名称, 字段, update_modified=False)
        frappe.db.commit()


def _计算下次增量运行时间(当前时间, 间隔分钟):
    """15分钟增量仅在北京时间06:00至24:00运行。"""
    北京时间 = get_datetime(当前时间)
    if 北京时间.hour < 亚马逊订单增量开始小时:
        return 北京时间.replace(
            hour=亚马逊订单增量开始小时,
            minute=0,
            second=0,
            microsecond=0,
        )
    候选时间 = frappe.utils.add_to_date(
        北京时间, minutes=max(int(间隔分钟), 1)
    )
    if 候选时间.hour < 亚马逊订单增量开始小时:
        候选时间 = 候选时间.replace(
            hour=亚马逊订单增量开始小时,
            minute=0,
            second=0,
            microsecond=0,
        )
    return 候选时间


def _当前允许订单增量同步(当前时间=None):
    北京时间 = (
        get_datetime(当前时间)
        if 当前时间
        else datetime.now(ZoneInfo("Asia/Shanghai"))
    )
    return 北京时间.hour >= 亚马逊订单增量开始小时


def _计算订单整体下次运行时间(配置行, 覆盖字段=None, 当前时间=None):
    """返回该店铺所有自动任务中最早的下次运行时间。"""
    覆盖字段 = 覆盖字段 or {}
    当前时间 = 当前时间 or frappe.utils.now_datetime()

    def 取值(fieldname):
        return 覆盖字段.get(fieldname, 配置行.get(fieldname))

    增量下次 = 取值("新订单下次同步时间")
    if not 增量下次:
        增量下次 = (
            当前时间
            if _当前允许订单增量同步(当前时间)
            else get_datetime(当前时间).replace(
                hour=亚马逊订单增量开始小时,
                minute=0,
                second=0,
                microsecond=0,
            )
        )
    候选时间 = [增量下次]
    for 前缀 in ("7天", "14天", "30天", "90天", "180天"):
        if frappe.utils.cint(取值(f"启用{前缀}核对")):
            候选时间.append(取值(f"{前缀}下次核对时间") or 当前时间)
    return min(get_datetime(value) for value in 候选时间)


def _计算固定凌晨核对时间(当前时间, 间隔天数):
    """定期核对统一安排在ERPNext系统时区的02:59。"""
    当前时间 = get_datetime(当前时间)
    return (
        当前时间.replace(hour=2, minute=59, second=0, microsecond=0)
        + timedelta(days=max(int(间隔天数), 1))
    )


def _匹配订单api配置(主表, 配置行):
    店铺 = str(配置行.get("店铺") or "").strip()
    站点id = str(配置行.get("marketplace_id") or "").strip().upper()
    for api行 in 主表.get("亚马逊api") or []:
        if (
            str(api行.get("店铺选项") or "").strip() == 店铺
            and str(api行.get("站点id") or "").strip().upper() == 站点id
        ):
            if not frappe.utils.cint(api行.get("开启订单下载")):
                raise ValueError(f"店铺 {店铺} 的Amazon API没有开启订单下载")
            return api行
    raise ValueError(f"找不到店铺 {店铺}、站点 {站点id} 对应的Amazon API配置")


def _获取订单访问令牌(api行):
    from fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log import 亚马逊请求

    响应 = 亚马逊请求(
        "POST",
        "https://api.amazon.com/auth/o2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": str(api行.get("刷新令牌") or "").strip(),
            "client_id": str(api行.get("客户端编码") or "").strip(),
            "client_secret": str(api行.get("客户端密钥") or "").strip(),
        },
        timeout=20,
        retries=3,
    )
    if 响应.status_code != 200:
        raise RuntimeError(f"Amazon授权失败（HTTP {响应.status_code}）：{响应.text[:500]}")
    令牌 = (响应.json() or {}).get("access_token")
    if not 令牌:
        raise RuntimeError("Amazon授权成功但没有返回Access Token")
    return 令牌


def _订单api额度键(api行):
    """同一套Amazon授权的所有国家共享Orders API请求额度。"""
    授权标识 = "|".join((
        str(api行.get("客户端编码") or "").strip(),
        str(api行.get("刷新令牌") or "").strip(),
    ))
    摘要 = hashlib.sha256(授权标识.encode("utf-8")).hexdigest()[:24]
    return f"fengjing:amazon-orders-quota:{摘要}"


def _等待订单api额度(api行):
    """Redis共享令牌桶：允许有限突发，随后按Amazon默认速率逐步恢复。"""
    cache = frappe.cache()
    状态键 = _订单api额度键(api行)
    锁键 = f"{状态键}:lock"
    while True:
        等待秒数 = 0
        with cache.lock(cache.make_key(锁键), timeout=30, blocking_timeout=30):
            当前秒 = time.time()
            状态 = cache.get_value(状态键) or {}
            try:
                令牌 = float(状态.get("tokens", 亚马逊订单突发请求上限))
                更新时间 = float(状态.get("updated_at", 当前秒))
            except (TypeError, ValueError, AttributeError):
                令牌 = float(亚马逊订单突发请求上限)
                更新时间 = 当前秒

            令牌 = min(
                float(亚马逊订单突发请求上限),
                令牌 + max(当前秒 - 更新时间, 0) / 亚马逊订单令牌恢复秒数,
            )
            if 令牌 >= 1:
                cache.set_value(
                    状态键,
                    {"tokens": 令牌 - 1, "updated_at": 当前秒},
                    expires_in_sec=24 * 60 * 60,
                )
                return

            等待秒数 = max((1 - 令牌) * 亚马逊订单令牌恢复秒数, 1)
            cache.set_value(
                状态键,
                {"tokens": 令牌, "updated_at": 当前秒},
                expires_in_sec=24 * 60 * 60,
            )
        time.sleep(等待秒数)


def _标记订单api已经限流(api行):
    cache = frappe.cache()
    状态键 = _订单api额度键(api行)
    with cache.lock(cache.make_key(f"{状态键}:lock"), timeout=30, blocking_timeout=30):
        cache.set_value(
            状态键,
            {"tokens": 0, "updated_at": time.time()},
            expires_in_sec=24 * 60 * 60,
        )


def _发送订单api请求(api行, method, url, **kwargs):
    """Orders API专用请求：共享节流，并对429执行分钟级等待。"""
    from fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log import 亚马逊请求

    限流等待序列 = (60, 120, 180, 300, 300, 300)
    暂时错误等待序列 = (5, 15, 30, 60, 120, 180)
    最后响应 = None
    for 尝试序号 in range(len(限流等待序列)):
        _等待订单api额度(api行)
        try:
            响应 = 亚马逊请求(
                method,
                url,
                retries=1,
                **kwargs,
            )
        except requests.RequestException:
            if 尝试序号 == len(限流等待序列) - 1:
                raise
            time.sleep(暂时错误等待序列[尝试序号])
            continue

        最后响应 = 响应
        if 响应.status_code == 429:
            _标记订单api已经限流(api行)
            retry_after = 响应.headers.get("Retry-After")
            try:
                等待秒数 = float(retry_after) if retry_after else 限流等待序列[尝试序号]
            except (TypeError, ValueError):
                等待秒数 = 限流等待序列[尝试序号]
            time.sleep(max(等待秒数, 限流等待序列[尝试序号]))
            continue
        if 响应.status_code in {500, 502, 503, 504}:
            if 尝试序号 < len(暂时错误等待序列) - 1:
                time.sleep(暂时错误等待序列[尝试序号])
                continue
        return 响应
    return 最后响应


def _同步订单查询窗口(配置行, api行, 同步类型, 开始utc, 结束utc, 按更新时间=False):
    from fengjing_app.fengjing_business.doctype.amazon_order_synchronization.amazon_order_synchronization import 保存亚马逊订单
    from fengjing_app.fengjing_business.doctype.amazon_rank_sku_log.amazon_rank_sku_log import (
        SP_API站点区域,
        获取SP_API区域地址,
    )

    站点id = str(配置行.get("marketplace_id") or "").strip().upper()
    店铺 = str(配置行.get("店铺") or "").strip()
    api地址 = 获取SP_API区域地址(站点id)
    if not api地址:
        raise ValueError(f"无法识别Marketplace ID {站点id} 所属的SP-API区域")
    令牌 = _获取订单访问令牌(api行)
    地址 = f"{api地址}/orders/2026-01-01/orders"
    时间前缀 = "lastUpdated" if 按更新时间 else "created"
    参数 = {
        "marketplaceIds": 站点id,
        f"{时间前缀}After": _亚马逊utc字符串(开始utc),
        f"{时间前缀}Before": _亚马逊utc字符串(结束utc),
        "maxResultsPerPage": 100,
        # 新版Orders API可在一次请求中返回订单明细和履约、金额等资料。
        # 原始响应会完整保存，今后增加字段时无需重新抓取历史订单。
        "includedData": (
            "BUYER,RECIPIENT,PROCEEDS,EXPENSE,PROMOTION,CANCELLATION,"
            "FULFILLMENT,PACKAGES,TAX,PAYMENT,FULFILLMENT_ORDERS"
        ),
    }
    新建数量 = 更新数量 = 页面数量 = 0
    while True:
        响应 = _发送订单api请求(
            api行,
            "GET",
            地址,
            headers={
                "X-Amz-Access-Token": 令牌,
                "Accept": "application/json",
                "User-Agent": "FengjingAmazonOrders/1.0",
            },
            params=参数,
            timeout=45,
        )
        _确保数据库连接()
        if 响应.status_code != 200:
            raise RuntimeError(
                f"Orders API失败（HTTP {响应.status_code}）：{响应.text[:1000]}"
            )
        payload = 响应.json() or {}
        for 订单 in payload.get("orders") or []:
            动作, _ = 保存亚马逊订单(
                订单,
                店铺,
                站点id,
                SP_API站点区域.get(站点id, ""),
                同步类型,
            )
            if 动作 == "created":
                新建数量 += 1
            else:
                更新数量 += 1
        frappe.db.commit()
        页面数量 += 1
        next_token = (payload.get("pagination") or {}).get("nextToken")
        if not next_token:
            break
        # v2026-01-01翻页时保留完全相同的查询条件，只增加分页令牌。
        参数["paginationToken"] = next_token
    return {"新建": 新建数量, "更新": 更新数量, "分页": 页面数量}


def _订单任务锁(行名称):
    cache = frappe.cache()
    return cache.lock(
        cache.make_key(f"fengjing:amazon-orders:{行名称}"),
        timeout=6 * 60 * 60,
        blocking_timeout=0,
    )


def _订单历史待执行键(行名称):
    return f"fengjing:amazon-orders-history-pending:{行名称}"


def _标记订单历史待执行(行名称):
    frappe.cache().set_value(
        _订单历史待执行键(行名称),
        1,
        expires_in_sec=30 * 24 * 60 * 60,
    )


def _订单历史正在等待(行名称):
    return frappe.utils.cint(
        frappe.cache().get_value(_订单历史待执行键(行名称))
    ) == 1


def _清除订单历史待执行(行名称):
    frappe.cache().delete_value(_订单历史待执行键(行名称))


@frappe.whitelist()
def 启动亚马逊历史订单同步(配置行名称):
    """Queue the selected child-row history import and return immediately."""
    _, 行 = _取得订单配置行(配置行名称)
    if not 行.get("历史同步开始时间") or not 行.get("历史同步结束时间"):
        frappe.throw("请先填写历史同步开始时间和历史同步结束时间")
    _标记订单历史待执行(配置行名称)
    if 行.get("同步状态") == "同步中":
        return {
            "status": "waiting",
            "message": f"当前正在执行{行.get('当前执行类型') or '其他订单同步'}，历史订单已排队，完成后会自动继续。",
        }
    _更新订单配置状态(
        配置行名称,
        同步状态="等待执行",
        当前执行类型="历史订单",
        最近错误="",
        下次运行时间=frappe.utils.now_datetime(),
    )
    _订单任务入队(配置行名称, "历史订单")
    return {"status": "queued", "message": "历史订单同步已经进入后台队列"}


def 执行亚马逊订单同步任务(配置行名称, 同步类型):
    lock = _订单任务锁(配置行名称)
    if not lock.acquire(blocking=False):
        return {"status": "busy", "message": "该店铺已有订单同步任务运行"}
    开始时间 = frappe.utils.now_datetime()
    汇总 = {"新建": 0, "更新": 0, "分页": 0, "窗口": 0}
    try:
        _更新订单配置状态(
            配置行名称,
            同步状态="同步中",
            当前执行类型=同步类型,
            最近执行时间=开始时间,
            上次运行时间=开始时间,
            最近错误="",
        )
        主表, 行 = _取得订单配置行(配置行名称)
        api行 = _匹配订单api配置(主表, 行)

        if 同步类型 == "历史订单":
            while True:
                主表, 行 = _取得订单配置行(配置行名称)
                计划 = 规划亚马逊历史订单时间窗口(
                    行.get("历史同步开始时间"),
                    行.get("历史同步结束时间"),
                    行.get("历史已完整同步到"),
                )
                if 计划["status"] != "ready":
                    最终状态 = "成功" if 计划["status"] == "complete" else "等待执行"
                    _更新订单配置状态(
                        配置行名称,
                        同步状态=最终状态,
                        当前执行类型="",
                        最近完成时间=frappe.utils.now_datetime(),
                        上次运行结果=json.dumps({"状态": 最终状态, **汇总}, ensure_ascii=False),
                        下次运行时间=(
                            frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=15)
                            if 计划["status"] == "waiting"
                            else frappe.utils.now_datetime()
                        ),
                        历史抓取日志=json.dumps({**汇总, "时间计划": 计划}, ensure_ascii=False),
                    )
                    if 计划["status"] == "complete":
                        _清除订单历史待执行(配置行名称)
                    return {"status": 计划["status"], "summary": 汇总, "plan": 计划}
                开始utc = get_datetime(计划["created_after"].replace("Z", "+00:00"))
                结束utc = get_datetime(计划["created_before"].replace("Z", "+00:00"))
                本段 = _同步订单查询窗口(行, api行, 同步类型, 开始utc, 结束utc)
                for key in ("新建", "更新", "分页"):
                    汇总[key] += 本段[key]
                汇总["窗口"] += 1
                _更新订单配置状态(
                    配置行名称,
                    历史已完整同步到=计划["本窗口完整成功后可推进到"],
                    历史抓取日志=json.dumps(汇总, ensure_ascii=False),
                )

        主表, 行 = _取得订单配置行(配置行名称)
        现在utc = datetime.now(timezone.utc)
        安全结束utc = 现在utc - timedelta(minutes=亚马逊订单安全延迟分钟)
        if 同步类型 == "新订单增量":
            基准 = (
                行.get("新订单最后完整同步时间")
                or 行.get("历史同步结束时间")
                or 行.get("历史同步开始时间")
            )
            if not 基准:
                基准 = _utc转系统时间字符串(安全结束utc - timedelta(days=1))
            开始utc = _系统时间转utc(基准) - timedelta(minutes=亚马逊订单续跑重叠分钟)
            结束utc = 安全结束utc
            日志字段 = "自动抓取日志"
        else:
            天数 = {
                "7天核对": 7,
                "14天核对": 14,
                "30天核对": 30,
                "90天核对": 90,
                "180天核对": 180,
            }[同步类型]
            开始utc = 安全结束utc - timedelta(days=天数)
            结束utc = 安全结束utc
            日志字段 = "自动抓取日志"

        本段 = _同步订单查询窗口(行, api行, 同步类型, 开始utc, 结束utc, 按更新时间=True)
        汇总.update(本段)
        当前系统时间 = frappe.utils.now_datetime()
        更新字段 = {
            "同步状态": "成功",
            "当前执行类型": "",
            "最近完成时间": 当前系统时间,
            日志字段: json.dumps(汇总, ensure_ascii=False),
        }
        if 同步类型 == "新订单增量":
            间隔 = max(frappe.utils.cint(行.get("新订单间隔分钟")), 1)
            下次运行时间 = _计算下次增量运行时间(当前系统时间, 间隔)
            更新字段.update({
                "新订单最后完整同步时间": _utc转系统时间字符串(结束utc),
                "新订单下次同步时间": 下次运行时间,
            })
        else:
            前缀 = 同步类型.replace("核对", "")
            间隔天数 = max(frappe.utils.cint(行.get(f"{前缀}核对间隔天数")), 1)
            下次运行时间 = _计算固定凌晨核对时间(当前系统时间, 间隔天数)
            更新字段[f"{前缀}最后核对时间"] = 当前系统时间
            更新字段[f"{前缀}下次核对时间"] = 下次运行时间
        更新字段.update({
            "上次运行结果": json.dumps({"状态": "成功", **汇总}, ensure_ascii=False),
        })
        更新字段["下次运行时间"] = _计算订单整体下次运行时间(
            行, 更新字段, 当前系统时间
        )
        _更新订单配置状态(配置行名称, **更新字段)
        return {"status": "success", "summary": 汇总}
    except Exception as exc:
        失败后下次运行时间 = (
            _计算下次增量运行时间(frappe.utils.now_datetime(), 15)
            if 同步类型 == "新订单增量"
            else frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=15)
        )
        _更新订单配置状态(
            配置行名称,
            同步状态="失败",
            当前执行类型="",
            最近完成时间=frappe.utils.now_datetime(),
            最近错误=str(exc)[:2000],
            上次运行结果=f"失败：{str(exc)[:1800]}",
            下次运行时间=失败后下次运行时间,
        )
        frappe.logger("amazon_orders", allow_site=True).exception(
            "Amazon订单同步失败：配置行=%s，类型=%s", 配置行名称, 同步类型
        )
        raise
    finally:
        try:
            lock.release()
        except Exception:
            pass


def _订单任务入队(行名称, 同步类型):
    frappe.enqueue(
        执行亚马逊订单同步任务,
        queue="long",
        timeout=6 * 60 * 60,
        enqueue_after_commit=True,
        job_id=f"amazon-orders-{行名称}-{同步类型}",
        deduplicate=True,
        配置行名称=行名称,
        同步类型=同步类型,
    )


def 定时执行亚马逊订单同步():
    """Called by scheduler; enqueue at most one due task for each configured store."""
    主表 = frappe.get_single(订单配置主表)
    当前时间 = frappe.utils.now_datetime()
    for 行 in 主表.get("亚马逊抓取订单配置表") or []:
        try:
            if not frappe.utils.cint(行.get("启用自动抓取")):
                # 手工点击的历史任务不受自动同步开关限制。
                if not _订单历史正在等待(行.name):
                    continue
            if 行.get("同步状态") == "同步中":
                continue
            # 失败后至少等待15分钟再重试，避免每分钟连续打满Amazon接口。
            if (
                行.get("同步状态") == "失败"
                and 行.get("下次运行时间")
                and get_datetime(行.get("下次运行时间")) > 当前时间
            ):
                continue
            _匹配订单api配置(主表, 行)
            if _订单历史正在等待(行.name):
                if (
                    行.get("同步状态") == "等待执行"
                    and 行.get("下次运行时间")
                    and get_datetime(行.get("下次运行时间")) > 当前时间
                ):
                    continue
                _更新订单配置状态(
                    行.name,
                    同步状态="等待执行",
                    当前执行类型="历史订单",
                )
                _订单任务入队(行.name, "历史订单")
                continue
            # 手工启动但因未来边界暂停的历史任务，由调度器继续推进。
            if 行.get("同步状态") == "等待执行" and 行.get("历史同步开始时间") and 行.get("历史同步结束时间"):
                _订单任务入队(行.name, "历史订单")
                continue
            if (
                _当前允许订单增量同步()
                and (
                    not 行.get("新订单下次同步时间")
                    or get_datetime(行.get("新订单下次同步时间")) <= 当前时间
                )
            ):
                _订单任务入队(行.name, "新订单增量")
                continue
            for 前缀 in ("7天", "14天", "30天", "90天", "180天"):
                if not frappe.utils.cint(行.get(f"启用{前缀}核对")):
                    continue
                下次 = 行.get(f"{前缀}下次核对时间")
                if not 下次 or get_datetime(下次) <= 当前时间:
                    _订单任务入队(行.name, f"{前缀}核对")
                    break
        except Exception:
            frappe.logger("amazon_orders", allow_site=True).exception(
                "Amazon订单定时任务入队失败：配置行=%s", 行.name
            )


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




