# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import frappe
import requests
import json
from frappe.model.document import Document
from frappe.utils import now
from datetime import datetime, timedelta, timezone
from frappe.utils import get_datetime, add_to_date

class AmazonRankSKULog(Document):
    pass




def 定时执行亚马逊抓取排名的函数():
    """
    这是专门给定时任务调用的入口
    它会强制将 '忽视定时抓取' 设为 0，从而触发你写的拦截逻辑
    """
    # 因为主表 ID 是固定的，这里不需要传 docname
    获取sku排名(忽视定时抓取=0)

#忽视定时抓取=1  只是一个默认值是1
@frappe.whitelist()
def 获取sku排名(docname=None,忽视定时抓取=1):

    # --- 你原本定义好的部分 ---
    tz_beijing = timezone(timedelta(hours=8))
    now = datetime.now(tz_beijing)
    启动程序时间 = now.strftime("%Y-%m-%d %H:%M:%S")

    # 1. 获取 API 列表配置
    配置主表名称 = "Fengjing - Product Corresponding Platform - Configuration"
    main_doc = frappe.get_doc(配置主表名称)

    # 3. 判断是否要“拦截”
    if frappe.utils.cint(忽视定时抓取) == 0:
        if frappe.utils.cint(main_doc.开启定时抓取 or 0) == 0:
            #frappe.log_error("亚马逊抓取商品排名，定时任务触发，但主表未开启定时抓取选项", "抓取任务跳过")
            print("亚马逊抓取商品排名，定时任务触发，但主表未开启定时抓取选项", "抓取任务跳过")
            return {
                "status": "ignored",
                "message": "系统未开启定时抓取权限，已自动跳过。"
            }
        else:
            # --- 【优化逻辑：使用你定义的 启动程序时间 进行校验】 ---
            # 1. 处理间隔分钟逻辑：非整数、小于60，全部强制设为60
            raw_interval = frappe.utils.cint(main_doc.间隔分钟)
            间隔分钟 = 60 if raw_interval < 60 else raw_interval

            上次抓取时间 = main_doc.上次抓取时间

            if 上次抓取时间:
                # 2. 计算下一次允许抓取的临界点
                下次允许抓取的时间 = add_to_date(上次抓取时间, minutes=间隔分钟)
                
                # 3. 写入字段：更新计算出的“临界点”到主表
                main_doc.下次允许抓取的时间 = 下次允许抓取的时间
                main_doc.save(ignore_permissions=True)
                frappe.db.commit()

                # 4. 对比你定义的“启动程序时间”
                # 使用 get_datetime 确保“启动程序时间”从字符串转为可对比的时间对象
                if get_datetime(启动程序时间) < get_datetime(下次允许抓取的时间):
                    print(f"时间未到。启动时间是: {启动程序时间}，下次抓取应在: {下次允许抓取的时间}")
                    return {
                        "status": "too_early",
                        "message": f"未到间隔时间。下次抓取时间：{下次允许抓取的时间}"
                    }
            
            print(f"校验通过（启动时间：{启动程序时间}），开始执行定时抓取...")
    else:
        # 忽视定时抓取 == 1，代表是按钮点的，直接通过
        print(f"手动点击触发（启动时间：{启动程序时间}），无视定时开关，准备执行...")

    # --- 后续抓取逻辑 ---


    # 2. 获取 API 子表
    api_table = main_doc.get("亚马逊api") or []
    # 只有所有店铺、站点的商品列表全部分页抓取成功后，
    # 才允许根据 Amazon 当前 ASIN 集合清理本地子表，避免接口失败时误删。
    亚马逊当前全部ASIN = set()
    所有商品列表均完整成功 = bool(api_table)
    for i, row in enumerate(api_table, 1):
        # 3. 提取基础信息

        # 4. 提取三个核心加密密钥
        客户端编码 = row.get_password("客户端编码")
        客户端密钥 = row.get_password("客户端密钥")
        刷新令牌 = row.get_password("刷新令牌")
        站点id = row.站点id
        卖家记号 = row.卖家记号
        临时秘钥 = 去获取临时秘钥(客户端编码, 客户端密钥, 刷新令牌)

        # 注意：这个接口需要 sellerId (也叫 Merchant ID)
        # 你可以从 api_row 里的某个字段获取，或者在获取 Token 时拿到的数据里找
        endpoint = f"https://sellingpartnerapi-na.amazon.com/listings/2021-08-01/items/{卖家记号}"
        
        headers = {
            "X-Amz-Access-Token": 临时秘钥,
            "Accept": "application/json"
        }
        
        # 关键参数：通过 marketplaceIds 过滤
        基础参数 = {
            "marketplaceIds": 站点id,
            "includedData": "summaries", # 只要概要信息，包含 SKU 和 ASIN
            "pageSize": 20
        }

        # 1. 创建一个空列表作为“篮子”
        结果列表 = []
        当前页令牌 = None
        当前商品列表完整成功 = True

        while True:
            params = dict(基础参数)
            if 当前页令牌:
                params["pageToken"] = 当前页令牌

            try:
                返回值 = requests.get(
                    endpoint,
                    headers=headers,
                    params=params,
                    timeout=30
                )
            except requests.RequestException as 列表请求错误:
                当前商品列表完整成功 = False
                print(f"查询失败-网络异常: {列表请求错误}")
                break

            print(endpoint)
            print(params)

            if 返回值.status_code != 200:
                当前商品列表完整成功 = False
                print(f"查询失败-可能秘钥错误: {返回值.text}")
                break

            返回数据 = 返回值.json()
            全部产品 = 返回数据.get("items", [])

            for 单个产品_原始 in 全部产品:
                SKU = 单个产品_原始.get("sku")
                摘要列表 = 单个产品_原始.get("summaries", [])
                
                if 摘要列表:
                    s = 摘要列表[0]
                    
                    # 2. 提取数据（保持你原来的逻辑）
                    ASIN = s.get("asin")
                    站点id = s.get("marketplaceId")
                    商品标题 = s.get("itemName")
                    产品类型 = s.get("productType")
                    成色 = s.get("conditionType")
                    状态 = ", ".join(s.get("status", []))
                    创建时间 = s.get("createdDate")
                    最后更新时间 = s.get("lastUpdatedDate")
                    
                    图片信息 = s.get("mainImage", {})
                    主图链接 = 图片信息.get("link")
                    图片宽 = 图片信息.get("width", "未提及")
                    图片高 = 图片信息.get("height", "未提及")

                    # 3. 把这些信息打包成一个“字典”
                    产品字典 = {
                        "商品列表api_ASIN": ASIN,
                        "商品列表api_SKU": SKU,
                        "商品列表api_站点id": 站点id,
                        "商品列表api_商品标题": 商品标题,
                        "商品列表api_产品类型": 产品类型,
                        "商品列表api_成色": 成色,
                        "商品列表api_状态": 状态,
                        "商品列表api_创建时间": 创建时间,
                        "商品列表api_最后更新时间": 最后更新时间,
                        "商品列表api_主图链接": 主图链接,
                        "商品列表api_图片宽": 图片宽,
                        "商品列表api_图片高": 图片高
                    }

                    # 4. 把字典装进篮子里
                    结果列表.append(产品字典)
                    if ASIN:
                        亚马逊当前全部ASIN.add(ASIN)
                    
                    # 依然可以保留打印，方便调试
                    print(f"已装载 SKU: {SKU}")

            当前页令牌 = (返回数据.get("pagination") or {}).get("nextToken")
            if not 当前页令牌:
                break

        if not 当前商品列表完整成功:
            所有商品列表均完整成功 = False

        # 5. 循环结束后，你可以根据需要处理这个结果列表
        print(f"\n成功装载了 {len(结果列表)} 个产品数据")


        for item in 结果列表:
            # 1. 提取当前产品的 ASIN
            商品列表api_ASIN = item.get('商品列表api_ASIN') # 拿着 SKU 是为了后面存日志时知道是谁的排名
            商品列表api_SKU = item.get('商品列表api_SKU')
            商品列表api_站点id = item.get('商品列表api_站点id')
            商品列表api_商品标题 = item.get('商品列表api_商品标题')
            商品列表api_产品类型 = item.get('商品列表api_产品类型')
            商品列表api_成色 = item.get('商品列表api_成色')
            商品列表api_状态 = item.get('商品列表api_状态')
            商品列表api_创建时间 = item.get('商品列表api_创建时间')
            商品列表api_最后更新时间 = item.get('商品列表api_最后更新时间')
            商品列表api_主图链接 = item.get('商品列表api_主图链接')
            商品列表api_图片宽 = item.get('商品列表api_图片宽')
            商品列表api_图片高 = item.get('商品列表api_图片高')

            # 标记变量：默认没找到
            asin_found = False
            should_skip = False
            # --- 2. 遍历子表进行比对 ---
            # 请确保 '抓取asin配置的子表' 是你在主表里设置的字段名 (Field Name)
            for row in main_doc.抓取asin配置的子表: 
                if row.需要抓取数据的asin == 商品列表api_ASIN:
                    asin_found = True
                    # 如果找到了，检查勾选状态
                    if int(row.是否监听排名 or 0) == 1:
                        print(f"ASIN {商品列表api_ASIN} 已存在且已勾选，继续执行。")
                        # 这里执行你后续的抓取和写入 Log 的逻辑
                        rank_data = 获取亚马逊商品销售排名(商品列表api_ASIN, 临时秘钥, 站点id)
                    else:
                        print(f"ASIN {商品列表api_ASIN} 已存在但未勾选，跳过。")
                        should_skip = True 
                    break # 既然找到了 ASIN，就不需要再看子表的其他行了



            # --- 3. 处理跳过逻辑 ---
            if should_skip:
                continue # 【关键】这里会跳过当前的 item，直接处理下一个产品


            # --- 3. 处理“没有这个asin”的情况 ---
            if not asin_found:
                print(f"没有找到 ASIN {商品列表api_ASIN}，正在自动添加并开启监控...")
                
                # 向子表添加新行
                main_doc.append("抓取asin配置的子表", {
                    "需要抓取数据的asin": 商品列表api_ASIN,
                    "是否监听排名": 1  # 直接打开勾选
                })
                
                # 保存主表修改
                main_doc.save(ignore_permissions=True)
                frappe.db.commit()
                
                # 添加完后，这里可以继续执行你获取数据的逻辑
                print("添加成功，开始获取该 ASIN 的数据...")

                rank_data = 获取亚马逊商品销售排名(商品列表api_ASIN, 临时秘钥, 站点id)


            排名api_asin = rank_data.get('排名api_asin')
            排名api_站点ID = rank_data.get('排名api_站点ID')
            排名api_商品名称 = rank_data.get('排名api_商品名称')
            排名api_品牌 = rank_data.get('排名api_品牌')
            排名api_制造商 = rank_data.get('排名api_制造商')
            排名api_型号 = rank_data.get('排名api_型号')
            排名api_零件编号 = rank_data.get('排名api_零件编号')
            排名api_颜色 = rank_data.get('排名api_颜色')
            排名api_尺寸 = rank_data.get('排名api_尺寸')
            排名api_样式 = rank_data.get('排名api_样式')
            排名api_主类目排名 = rank_data.get('排名api_主类目排名')
            排名api_主类目名称 = rank_data.get('排名api_主类目名称')
            排名api_主类目链接 = rank_data.get('排名api_主类目链接')
            排名api_细分类目排名 = rank_data.get('排名api_细分类目排名')
            排名api_细分类目名称 = rank_data.get('排名api_细分类目名称')
            排名api_细分类目链接 = rank_data.get('排名api_细分类目链接')
            排名api_分类ID = rank_data.get('排名api_分类ID')
            排名api_浏览节点名称 = rank_data.get('排名api_浏览节点名称')
            排名api_浏览节点ID = rank_data.get('排名api_浏览节点ID')
            排名api_网站显示分组 = rank_data.get('排名api_网站显示分组')
            排名api_网站显示分组名称 = rank_data.get('排名api_网站显示分组名称')
            排名api_商品分类类型 = rank_data.get('排名api_商品分类类型')
            排名api_成人用品 = rank_data.get('排名api_成人用品')
            排名api_亲笔签名 = rank_data.get('排名api_亲笔签名')
            排名api_纪念品 = rank_data.get('排名api_纪念品')
            排名api_支持以旧换新 = rank_data.get('排名api_支持以旧换新')
            排名api_包装数量 = rank_data.get('排名api_包装数量')
            排名api_发布日期 = rank_data.get('排名api_发布日期')

            # --- 开始写入 Frappe 数据库 ---

            # 1. 初始化物料变量（防止匹配失败报错）
            matched_material_id = None

            # 2. 去“对应平台主表”匹配 SKU
            if 商品列表api_SKU:
                print(商品列表api_SKU)
                # 获取“物料id”字段的内容
                matched_material_id = frappe.db.get_value(
                    "Fengjing - Product Corresponding Platform - Main Table", 
                    {"平台sku": 商品列表api_SKU}, 
                    "物料id"
                )
            print(matched_material_id)
            try:
                # 创建新文档对象
                log_doc = frappe.get_doc({
                    "doctype": "Amazon Rank SKU Log",
                    "绑定的物料": matched_material_id,
                    "抓取数据的时间": 启动程序时间,
                    # --- 商品列表 API 字段映射 ---
                    "商品列表api_asin": 商品列表api_ASIN,
                    "商品列表api_sku": 商品列表api_SKU,
                    "商品列表api_站点id": 商品列表api_站点id,
                    "商品列表api_商品标题": 商品列表api_商品标题,
                    "商品列表api_产品类型": 商品列表api_产品类型,
                    "商品列表api_成色": 商品列表api_成色,
                    "商品列表api_状态": 商品列表api_状态,
                    "商品列表api_创建时间": 商品列表api_创建时间,
                    "商品列表api_最后更新时间": 商品列表api_最后更新时间,
                    "商品列表api_主图链接": 商品列表api_主图链接,
                    "商品列表api_图片宽": frappe.utils.cint(商品列表api_图片宽),  # 强制转整数
                    "商品列表api_图片高": frappe.utils.cint(商品列表api_图片高),  # 强制转整数
                    
                    # --- 排名 API 字段映射 ---
                    "排名api_asin": 排名api_asin,
                    "排名api_站点id": 排名api_站点ID,
                    "排名api_商品名称": 排名api_商品名称,
                    "排名api_品牌": 排名api_品牌,
                    "排名api_制造商": 排名api_制造商,
                    "排名api_型号": 排名api_型号,
                    "排名api_零件编号": 排名api_零件编号,
                    "排名api_颜色": 排名api_颜色,
                    "排名api_尺寸": 排名api_尺寸,
                    "排名api_样式": 排名api_样式,
                    "排名api_主类目排名": frappe.utils.cint(排名api_主类目排名) if 排名api_主类目排名 else 0,
                    "排名api_主类目名称": 排名api_主类目名称,
                    "排名api_主类目链接": 排名api_主类目链接,
                    "排名api_细分类目排名": frappe.utils.cint(排名api_细分类目排名) if 排名api_细分类目排名 else 0,
                    "排名api_细分类目名称": 排名api_细分类目名称,
                    "排名api_细分类目链接": 排名api_细分类目链接,
                    "排名api_分类id": 排名api_分类ID,
                    "排名api_浏览节点名称": 排名api_浏览节点名称,
                    "排名api_浏览节点id": 排名api_浏览节点ID,
                    "排名api_网站显示分组": 排名api_网站显示分组,
                    "排名api_网站显示分组名称": 排名api_网站显示分组名称,
                    "排名api_商品分类类型": 排名api_商品分类类型,
                    "排名api_成人用品": 排名api_成人用品,
                    "排名api_亲笔签名": 排名api_亲笔签名,
                    "排名api_纪念品": 排名api_纪念品,
                    "排名api_支持以旧换新": 排名api_支持以旧换新,
                    "排名api_包装数量": frappe.utils.cint(排名api_包装数量) if 排名api_包装数量 else 0,
                    "排名api_发布日期": 排名api_发布日期
                })

                # 执行插入数据库操作
                log_doc.insert(ignore_permissions=True)
                
                # 如果是在循环中执行，建议在循环结束后统一 commit，或者每条 commit 保证实时保存
                frappe.db.commit()





                # 2. 假设 商品列表api_ASIN 是你当前正在处理的 ASIN
                target_asin = 商品列表api_ASIN 

                # 3. 遍历子表，寻找匹配的行
                updated = False
                for row in main_doc.抓取asin配置的子表:
                    if row.需要抓取数据的asin == target_asin:
                        # 找到了对应的行，只更新这一行的时间
                        row.上次抓取的时间 = 启动程序时间
                        updated = True
                        break  # 找到后跳出循环，节省性能

                # 4. 只有在找到并修改了内容的情况下才保存
                if updated:
                    main_doc.save(ignore_permissions=True)
                    frappe.db.commit()


            except Exception as save_error:
                # 如果写入数据库失败，记录详细日志
                frappe.log_error(
                    title="Amazon Rank SKU Log 写入失败",
                    message=f"错误原因: {str(save_error)}\n追踪信息: {frappe.get_traceback()}"
                )
			#然后从这里吧以上信息写入到表内


    # 只有 Amazon 的所有商品列表均完整返回时，才同步删除已不存在的 ASIN。
    # 这里只删除配置子表行，不删除历史排名日志。
    if 所有商品列表均完整成功:
        原有ASIN数量 = len(main_doc.抓取asin配置的子表)
        保留的ASIN行 = [
            row for row in main_doc.抓取asin配置的子表
            if row.需要抓取数据的asin in 亚马逊当前全部ASIN
        ]
        main_doc.set("抓取asin配置的子表", 保留的ASIN行)
        已删除ASIN数量 = 原有ASIN数量 - len(保留的ASIN行)
        print(f"ASIN 配置同步完成，删除了 {已删除ASIN数量} 条 Amazon 已不存在的配置。")
    else:
        print("Amazon 商品列表未完整抓取，为避免误删，本次不同步删除 ASIN 配置。")

    #全部循环完成，开始记录抓取的总时间
    main_doc.上次抓取时间 = 启动程序时间
    # 获取纠正后的间隔分钟（确保保底 60 分钟）
    raw_interval = frappe.utils.cint(main_doc.间隔分钟)
    间隔分钟 = 60 if raw_interval < 60 else raw_interval
    
    # 重新计算临界点
    main_doc.下次允许抓取的时间 = add_to_date(启动程序时间, minutes=间隔分钟)
    main_doc.save(ignore_permissions=True)
    frappe.db.commit()

    return None


def 去获取临时秘钥(客户端编码, 客户端密钥, 刷新令牌):
    """向亚马逊 OAuth2 接口申请 Access Token"""
    url = "https://api.amazon.com/auth/o2/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": 客户端编码,
        "client_secret": 客户端密钥,
        "refresh_token": 刷新令牌
    }
    try:
        res = requests.post(url, data=payload, timeout=10)
        if res.status_code == 200:
            # 获取成功就返回临时秘钥
            return res.json().get("access_token")
        else:
            frappe.log_error(f"Auth 失败: {res.text}", "亚马逊认证错误-应该是秘钥错误？")
    except Exception as e:
        frappe.log_error(f"Auth 请求异常: {str(e)}", "获取亚马逊认证网络错误")
    return None


def 获取亚马逊商品销售排名(asin, 临时秘钥, 站点id):
    # 必须包含 salesRanks 才能看到排名
    api_url = f"https://sellingpartnerapi-na.amazon.com/catalog/2022-04-01/items/{asin}"
    params = {
        "marketplaceIds": 站点id,
        "includedData": "summaries,salesRanks"
    }
    headers = {
        "X-Amz-Access-Token": 临时秘钥,
        "Accept": "application/json",
        "User-Agent": "TestApp/1.0"
    }
    try:
        response = requests.get(api_url, headers=headers, params=params)
        if response.status_code == 200:

            # 直接获取字典对象，不要用 json.dumps 转换成字符串
            your_raw_json = response.json()
            # 此时 your_raw_json 是字典，可以安全使用 .get()
            # --- 核心解析逻辑 ---
            summaries_list = your_raw_json.get("summaries", [])
            summary = summaries_list[0] if summaries_list else {}

            sales_ranks_list = your_raw_json.get("salesRanks", [])
            # 取第一组销售排名数据（如果存在）
            sales_ranks = sales_ranks_list[0] if sales_ranks_list else {}

            # 重点：防御性提取列表中的第一个字典
            # 如果列表为空，则返回一个空字典 {}，这样后续的 .get() 永远不会报错
            c_ranks = sales_ranks.get("classificationRanks", [{}])[0] if sales_ranks.get("classificationRanks") else {}
            d_ranks = sales_ranks.get("displayGroupRanks", [{}])[0] if sales_ranks.get("displayGroupRanks") else {}

            browse = summary.get("browseClassification", {})

            # --- 构建结果字典 ---
            result = {
                # --- 核心标识 ---
                "排名api_asin": your_raw_json.get("asin"),
                "排名api_站点ID": summary.get("marketplaceId"),

                # --- 商品基础描述 ---
                "排名api_商品名称": summary.get("itemName"),
                "排名api_品牌": summary.get("brand"),
                "排名api_制造商": summary.get("manufacturer"),
                "排名api_型号": summary.get("modelNumber"),
                "排名api_零件编号": summary.get("partNumber", "未提及"), 
                "排名api_颜色": summary.get("color", "未提及"), 
                "排名api_尺寸": summary.get("size", "未提及"), 
                "排名api_样式": summary.get("style", "未提及"), 

                # --- 排名数据 ---
                "排名api_主类目排名": d_ranks.get("rank"), 
                "排名api_主类目名称": d_ranks.get("title"), 
                "排名api_主类目链接": d_ranks.get("link"), 
                "排名api_细分类目排名": c_ranks.get("rank"), 
                "排名api_细分类目名称": c_ranks.get("title"), 
                "排名api_细分类目链接": c_ranks.get("link"), 

                # --- 分类与展示逻辑 ---
                "排名api_分类ID": c_ranks.get("classificationId"),
                "排名api_浏览节点名称": browse.get("displayName"),
                "排名api_浏览节点ID": browse.get("classificationId"),
                "排名api_网站显示分组": summary.get("websiteDisplayGroup"),
                "排名api_网站显示分组名称": summary.get("websiteDisplayGroupName"),
                "排名api_商品分类类型": summary.get("itemClassification"),

                # --- 状态与标志 ---
                "排名api_成人用品": "是" if summary.get("adultProduct") else "否",
                "排名api_亲笔签名": "是" if summary.get("autographed") else "否",
                "排名api_纪念品": "是" if summary.get("memorabilia") else "否",
                "排名api_支持以旧换新": "是" if summary.get("tradeInEligible") else "否",
                "排名api_包装数量": summary.get("packageQuantity"),
                "排名api_发布日期": summary.get("releaseDate", "未提及")
            }
            return result
        else:
            frappe.log_error(title="Auth 失败", message=response.text)
    except Exception as e:
        frappe.log_error(f"Auth 请求异常: {str(e)}")
    return None
