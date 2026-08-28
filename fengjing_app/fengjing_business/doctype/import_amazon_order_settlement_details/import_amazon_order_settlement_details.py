# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt


import frappe
import csv
import os
import io
import json
import hashlib
from frappe.utils import flt, get_files_path
from frappe.model.document import Document
from frappe.utils.file_manager import save_file, get_file_path
from frappe import _
from fengjing_app import fengmaode

class ImportAmazonordersettlementdetails(Document):

    #去下载翻译的文件了,去公共区域
    @frappe.whitelist(methods=["GET", "POST", "HEAD"])
    def 下载翻译过的结算订单文件(self):
        文件路径 = get_file_path(self.添加csv文件)
        # 3. 执行拿到的函数，并传入参数
        处理函数 = getattr(fengmaode, "翻译亚马逊表格")
        处理函数(文件路径, "Amazon Settlement Detail",self)

    @frappe.whitelist()
    def 处理csv文件进行导入(self):
        # 同步前端临时数据,可能没有保存,读取实时的数据
        if frappe.form_dict.doc:
            # 将前端传来的 JSON 字符串解析为字典
            前端单据数据 = json.loads(frappe.form_dict.doc)
            # 将这些最新的改动更新到当前的实例对象（self）中
            self.update(前端单据数据)

        # 先删除旧的关联，准备重新写入 不管三七二十一先重置
        frappe.db.sql("DELETE FROM `tabAmazon Settlement Detail` WHERE `链接主表` = %s", (self.name,))
        frappe.db.commit()

        #先清零
        #self.开始统计各种数据总和()
        #self.总行数 = 0

        # 4. 获取文件路径 (第4处修改)
        添加csv文件 = get_file_path(self.添加csv文件)
        if not os.path.exists(添加csv文件):
            frappe.msgprint("找不到附件物理文件,请重新上传,应该是系统问题,文件的接口更新了? 电话:15953992133, 进行修复", title="文件错误", indicator="red")
            return True

        # 去看看三表是否一致,如果不一致不用回来了
        处理函数 = getattr(fengmaode, "亚马逊文件关联表存储表是否一致")
        文件字段映射字典 = 处理函数(添加csv文件, "Amazon Settlement Detail", self.店铺国家)

        if 文件字段映射字典 is None:
            # 说明 fengmaode 内部已经报错并 return 了
            # 这里直接结束当前函数，防止跑到第 117 行触发 AttributeError
            return

        # 开始写入
        # 1. 自动判断编码 (一行流)
        编码 = 'utf-8-sig'
        try: open(添加csv文件, 'r', encoding=编码).read(4096)
        except: 编码 = 'gbk'
        # 确定编码后开始循环
        最终导入列表 = []
        with open(添加csv文件, 'r', encoding=编码) as f_in:
            样本 = f_in.read(4096)
            f_in.seek(0)
            # 谁的数量多,确定格式
            tab个数 = 样本.count('\t')
            逗号个数 = 样本.count(',')
            分隔符 = '\t' if tab个数 > 逗号个数 else ','
            # 不能直接作为键
            读取器 = csv.reader(f_in, delimiter=分隔符)
            跳过行数 = 0
            for 行列表 in 读取器:
                # 清理空格，判断这行是不是我们要的表头
                有效列 = [格.strip() for 格 in 行列表 if 格.strip()]
                # 亚马逊表头通常很长（>20列），描述行很短（1列）
                if len(有效列) < 5: 
                    pass
                    跳过行数 += 1
                else:
                    # 找到了疑似表头的一行！停止普通读取
                    break

            #找全部的数据
            f_in.seek(0)
            # 彻底跳过刚才那些描述行
            for _ in range(跳过行数):
                f_in.readline()
            # 现在的第一行就是真正的表头了
            读取器 = csv.DictReader(f_in, delimiter=分隔符)
            for 行数据 in 读取器:
                #存在就下一步
                转换后的行 = {}
                for 原始键, 原始值 in 行数据.items():
                    # 1. 清洗键名（防止两端空格干扰）
                    清洗后的键 = str(原始键).strip()
                    # 2. 去【文件字段映射字典】里找对应的内部字段
                    内部字段名 = 文件字段映射字典.get(清洗后的键)
                    if 内部字段名:
                        # 找到映射，进行替换
                        转换后的行[内部字段名] = 原始值
                    else:
                        # 这通常发生在：标题校验通过了，但读取数据时由于格式错乱产生了未定义的列
                        错误消息 = f"<b>数据行解析失败！</b><br>" \
                                f"发现未知标题：【{原始键}】<br>" \
                                f"该标题未在映射关系中定义，请检查文件格式是否对齐。"
                        frappe.throw(错误消息, title="数据解析异常")
                最终导入列表.append(转换后的行)


        # 存储最终带有 MD5 指纹的数据列表
        带指纹的导入列表 = []
        # 1. 遍历刚才转换成功的【最终导入列表】
        for 行数据 in 最终导入列表:
            # --- A. 排序：确保字典按照 Key 的字母顺序排列 ---
            # 这一步是为了防止：{'a':1, 'b':2} 和 {'b':2, 'a':1} 生成不同的 MD5
            排序后的数据 = dict(sorted(行数据.items()))
            
            # --- B. 序列化：转为 JSON 字符串以便哈希 ---
            # ensure_ascii=False 是为了处理中文，sort_keys=True 是双重保险
            数据字符串 = json.dumps(排序后的数据, sort_keys=True, ensure_ascii=False)
            
            # --- C. 生成 MD5 指纹 ---
            md5_对象 = hashlib.md5(数据字符串.encode('utf-8'))
            行指纹 = md5_对象.hexdigest()
            
            # --- D. 追加 MD5 到行数据中 ---
            # 假设你的子表里有一个字段叫 `行唯一指纹` 或 `md5`
            行数据["行唯一标识md5"] = 行指纹
            行数据["链接主表"] = self.name
            行数据["链接店铺"] = self.选择店铺
            行数据["亚马逊店铺国家"] = self.店铺国家
            
            带指纹的导入列表.append(行数据)


        # 1. 拿着 md5 全盘扫描已有的数据
        # 使用 set 存储 MD5 池，性能最优
        已存在的MD5池 = set(frappe.get_all("Amazon Settlement Detail", pluck="行唯一标识md5"))
        # 2. 准备装重复信息的篮子
        重复的行信息 = []
        # 3. 循环带指纹的列表进行比对
        for 行数据 in 带指纹的导入列表:
            当前行md5 = 行数据.get("行唯一标识md5")
            if 当前行md5 in 已存在的MD5池:
                # 发现重复！提取关键信息用于弹窗显示
                # 这里的 key 名建议和你字典转换后的 key 名对齐
                fnsku = 行数据.get("结算明细表_订单编号", "未知结算明细表_订单编号")
                重复项描述 = f"订单编号: {fnsku} (MD5: {当前行md5[:8]}...)"
                重复的行信息.append(重复项描述)

        # --- 4. 关键：扫描完成后，如果有重复，立即弹窗并停止 ---
        if 重复的行信息:
            # 拼接重复信息，限制显示前 10 条，防止弹窗太长
            显示列表 = "<br>".join(重复的行信息[:10])
            if len(重复的行信息) > 10:
                显示列表 += f"<br>总共重复 {len(重复的行信息)} 条重复记录"
            # frappe.throw 会直接中止程序执行，且数据库不会有任何改动（自动回滚事务）
            frappe.msgprint(
                title="检测到重复数据，导入已中止",
                msg=(
                    f"为了确保账目准确，系统已拦截本次导入。该批次中有部分数据在系统中已存在。<br><br>"
                    f"<b>重复项明细（前10条）：</b><br>{显示列表}<br><br>"
                    f"请检查文件内容或已导入的记录，删除重复项后再试。"
                )
            )
            return True



        # 5. 如果没有重复，继续执行入库逻辑...
        记录导入总数 = 0
        for 行数据 in 带指纹的导入列表:
            # 创建一条新的【亚马逊订单汇总】记录
            汇总单据 = frappe.new_doc("Amazon Settlement Detail")
            
            # --- A. 批量填入数据 ---
            # 使用 update 方法可以直接把字典里的键值对一次性同步到单据字段中
            汇总单据.update(行数据)
            
            # --- B. 插入数据库 ---
            # ignore_permissions=True 确保后台逻辑不受权限拦截，加快速度
            汇总单据.insert(ignore_permissions=True)
            
            记录导入总数 += 1

        # --- 6. 完工后的反馈与保存 ---
        # 更新本单据的统计信息（假设你的主表有“总行数”字段）
        self.总行数 = 记录导入总数
        self.save()

        self.开始统计各种数据总和()
        # 4. 在最终的提示中使用这个返回的内容
        # 1. 接收返回的文字
        绑定结果 = self.全局绑定开始绑定结算订单()
        # 2. 直接放入 msgprint
        frappe.msgprint(
            msg=f"<b>全流程完成！</b><br>"
                f"已导入 {记录导入总数} 条数据。<br>"
                f"全局绑定状态：{绑定结果}", # 直接显示那段文字
            title="操作成功",
            indicator="green"
        )
        return True





    @frappe.whitelist()
    def 全局绑定开始绑定结算订单(self):
        # 1. 获取所有基础订单,预先处理一下
        基础订单 = frappe.get_all("Amazon Order Summary",filters={},fields=["*"])
        if not 基础订单:
            frappe.msgprint("基础订单存储表中没有任何记录,不会执行关联操作")
            return False
        # 基础订单开始处理
        for 基础订单_单个 in 基础订单:
            # 注意：这里的 "单品状态" 请确保是数据库里的字段 ID
            if 基础订单_单个.get("订单状态") == "Cancelled":
                frappe.db.set_value("Amazon Order Summary", 基础订单_单个.name, "是否被亚马逊结算订单关联_类型", "取消订单")
            if 基础订单_单个.get("订单状态") == "Pending":
                frappe.db.set_value("Amazon Order Summary", 基础订单_单个.name, "是否被亚马逊结算订单关联_类型", "待定订单")
        frappe.db.commit()

        # 开始处理结算订单
        结算订单 = frappe.get_all("Amazon Settlement Detail",filters={},fields=["*"])
        if not 结算订单:
            frappe.msgprint(_("亚马逊结算明细存储表中没有任何记录"))
            return False
        基础订单不存在 = []
        已经绑定订单 = []
        for 单个结算订单 in 结算订单:
            # 1. 预处理：获取字段值
            # --- 1. 从单行数据中提取所有变量 (拆开写，方便其他地方调用) ---
            结算订单_订单编号 = 单个结算订单.get("结算明细表_订单编号")
            结算订单_卖家SKU = 单个结算订单.get("结算明细表_卖家sku")
            结算订单_购买数量 = 单个结算订单.get("结算明细表_数量")
            结算订单_日期时间 = 单个结算订单.get("结算明细表_日期_时间")
            结算订单_订单城市 = 单个结算订单.get("结算明细表_订单城市")
            结算订单_订单州省 = 单个结算订单.get("结算明细表_订单州_省")
            结算订单_邮政编码 = 单个结算订单.get("结算明细表_邮编")



            # 2. “解绑”旧关联（保持原有逻辑，建议放在匹配成功后再解绑，或者保留在此处）
            基础订单绑定 = 单个结算订单.链接亚马逊基础订单表 
            if 基础订单绑定:
                frappe.db.set_value("Amazon Order Summary", 基础订单绑定, "是否被亚马逊结算订单关联_类型", None)
            frappe.db.set_value("Amazon Settlement Detail", 单个结算订单.name, "链接亚马逊基础订单表", None)
            frappe.db.set_value("Amazon Settlement Detail", 单个结算订单.name, "是否找到数据", None)

            # 3. 执行查询（不要立即覆盖变量，保留列表以供判断）
            # --- 2. 组装成给数据库查询用的字典 (按需组合) ---
            结算信息 = {
                "亚马逊订单号": 结算订单_订单编号,
                "卖家SKU": 结算订单_卖家SKU,
                "购买数量": 结算订单_购买数量,
            }
            匹配列表 = frappe.get_all("Amazon Order Summary", filters=结算信息, fields=["name"])
            匹配数量 = len(匹配列表)

            # 4. 逻辑判断与状态处理
            if 匹配数量 >= 2:
                # 订单编号，数量，SKUID，这三个一模一样的情况下才走到这里
                # 发现重复项，抛出异常（frappe.throw 会自动回滚事务，保护数据）
                冲突ID = ", ".join([d.name for d in 匹配列表])
                frappe.throw(
                    f"检测到重复数据！订单号 <b>{结算信息['亚马逊订单号']}</b> 存在 {匹配数量} 条记录。<br>"
                    f"冲突 ID: {冲突ID}，请处理后再绑定。"
                )
            elif 匹配数量 == 1:
                # --- Success! 匹配成功后的核心操作 ---
                目标ID = 匹配列表[0].name
                # 建立双向关联
                frappe.db.set_value("Amazon Settlement Detail", 单个结算订单.name, "是否找到数据", "基础订单")
                frappe.db.set_value("Amazon Settlement Detail", 单个结算订单.name, "链接亚马逊基础订单表", 目标ID)
                frappe.db.set_value("Amazon Order Summary", 目标ID, "是否被亚马逊结算订单关联_类型", "结算订单_关联_唯一")
                已经绑定订单.append({
                            "结算明细ID": 单个结算订单.name,
                            "订单编号": 结算订单_订单编号
                        })
            else:
                # 匹配数量 是0的话到这里了，
                # 直接尝试获取这两个字段的值
                res = frappe.get_value("Amazon Settlement Detail", 
                    {"结算明细表_订单编号": 结算订单_订单编号}, 
                    ["结算明细表_卖家SKU", "结算明细表_数量"], 
                    as_dict=1
                )
                sku = res.get("结算明细表_卖家SKU")
                qty = res.get("结算明细表_数量")

                # 进一步检查字段内容是否真的“有东西” (不是 None 或 空字符串)
                if sku and qty is not None:

                    # 如果没有找到的话，有可能订单被拆分了。必须是本主表情况下才能进行统一绑定，如果是一个
                    # 一个拆分订单刚好分在了不同的主表，这种情况比较巧合
                    #链接主表_值 = 单个结算订单.get("链接主表")

                    #这个代码解释的意思是，如果这7个存在一模一样的内容，那就把数量进行相加
                    结算订单有多少重复的 = frappe.db.get_value(
                        "Amazon Settlement Detail",
                        filters={
                            #"链接主表": 链接主表_值,
                            "结算明细表_订单编号": 结算订单_订单编号,
                            "结算明细表_卖家SKU": 结算订单_卖家SKU,
                            "结算明细表_数量": 结算订单_购买数量,
                            "结算明细表_日期_时间": 结算订单_日期时间,
                            "结算明细表_订单城市": 结算订单_订单城市,
                            "结算明细表_订单州_省": 结算订单_订单州省,
                            "结算明细表_邮编": 结算订单_邮政编码,
                        },
                        fieldname="sum(结算明细表_数量)" # 直接使用 SQL 聚合函数
                    )
                    if 结算订单有多少重复的 is not None:
                        # 2. 如果汇总后的数量和单行数量不一致，说明确实存在拆分，用汇总数量重新匹配
                        总数量 = int(结算订单有多少重复的)
                        基础订单名称 = frappe.get_all("Amazon Order Summary",
                            filters={
                                "亚马逊订单号": 结算订单_订单编号,
                                "卖家SKU": 结算订单_卖家SKU,
                                "购买数量": 总数量,
                                "目的地省州": 结算订单_订单州省
                            },
                            fields=["name"]
                        )
                        # 3. 再次判断重新匹配的结果
                        if 基础订单名称:
                            # 匹配成功！执行绑定逻辑（更新字段等），确定是拆分了
                            目标ID = 基础订单名称[0].name
                            # 明确指定子表的 DocType 名称
                            frappe.db.set_value("Amazon Settlement Detail", 单个结算订单.name, "是否找到数据", "基础订单")
                            frappe.db.set_value("Amazon Settlement Detail", 单个结算订单.name, "链接亚马逊基础订单表", 目标ID)
                            frappe.db.set_value("Amazon Order Summary", 目标ID, "是否被亚马逊结算订单关联_类型", "结算订单_关联_拆分")
                            已经绑定订单.append({
                                        "结算明细ID": 单个结算订单.name,
                                        "订单编号": 结算订单_订单编号
                                    })
                        else:
                            frappe.db.set_value("Amazon Settlement Detail", 单个结算订单.name, "是否找到数据", "基础订单不存在")
                            #去基础订单查询后不存在,只有一种原因,因为基础订单没有,需要记录
                            基础订单不存在.append({
                                        "结算明细ID": 单个结算订单.name,
                                        "订单编号": 结算订单_订单编号
                                    })
                    else:
                        #不可能运行到这里,因为sum是空的话那就为0.0
                        pass
                else:
                    #到这里100%不是订单了
                    pass

            #从这里开始,绑定,移除订单
            移除订单的详细信息 = frappe.get_all("Amazon removes order details",filters={"订单编号": 结算订单_订单编号,},fields=["name"])
            if 移除订单的详细信息:
                #基础订单
                基础订单 = frappe.get_all("Amazon Order Summary",filters={"卖家自定义订单号": 结算订单_订单编号,},fields=["name"])
                基础订单数量 = len(基础订单)
                if 基础订单数量 == 1:
                    frappe.db.set_value("Amazon Settlement Detail", 单个结算订单.name, "是否找到数据", "弃置货物")
                    frappe.db.set_value("Amazon Order Summary", 基础订单[0].name, "是否被亚马逊结算订单关联_类型", "弃置订单_关联")
                    #绑定结算明细
                    frappe.db.set_value(
                        "Amazon Settlement Detail", 
                        {"结算明细表_订单编号": 结算订单_订单编号}, # 所有的过滤条件
                        "链接亚马逊基础订单表",                    # 目标字段
                        基础订单[0].name,        # 目标值
                        update_modified=False          # 可选：不更新修改时间，保持数据整洁
                    )
                    #详细弃置需要绑定
                    frappe.db.set_value(
                        "Amazon removes order details", 
                        {"订单编号": 结算订单_订单编号}, # 所有的过滤条件
                        "链接亚马逊基础订单表",                    # 目标字段
                        基础订单[0].name,        # 目标值
                        update_modified=False          # 可选：不更新修改时间，保持数据整洁
                    )
                else:
                    #移除订单,存在,但是基础订单不存在
                    单号 = ", ".join([d.name for d in 基础订单])
                    frappe.throw(
                        msg=f"数据异常：订单号【{结算订单_订单编号}】在总表中匹配到了 {基础订单数量} 条记录！<br>涉及单号：{单号}<br>请先清理总表中的重复数据。",
                        title="匹配歧义报错"
                    )


        frappe.db.commit()
        # 1. 提取前10个订单编号（仅编号）
        总计不存在数量 = len(基础订单不存在)
        总计存在数量 = len(已经绑定订单)
        # 2. 构造纯文字汇报
        # 修改 Python 代码中的拼接部分
        if 总计不存在数量 > 0:
            # 开头加 4 个 &nbsp; 相当于缩进两个汉字的宽度
            返回文字 = f"<br>&nbsp;&nbsp;&nbsp;&nbsp;全局已绑定 {总计存在数量} 条数据<br>"
            返回文字 += f"&nbsp;&nbsp;&nbsp;&nbsp;全局基础订单不存在：{总计不存在数量}<br>"
        else:
            返回文字 = f"<br>&nbsp;&nbsp;&nbsp;&nbsp;全局已绑定 {总计存在数量} 条数据<br>"

        # 3. 【核心修改】直接返回这个字符串
        return 返回文字

    @frappe.whitelist()
    def 开始统计各种数据总和(self):
        """
        专门负责统计当前导入批次的各项金额，并将结果回写到当前的 Session（主表）中
        """
        数据库函数 = """
            SELECT 
                IFNULL(SUM(`结算明细表_产品销售额`), 0) as s1, 
                IFNULL(SUM(`结算明细表_销售税`), 0) as s2,
                IFNULL(SUM(`结算明细表_运费收入`), 0) as s3,
                IFNULL(SUM(`结算明细表_运费税金`), 0) as s4,
                IFNULL(SUM(`结算明细表_礼品包装费`), 0) as s5,
                IFNULL(SUM(`结算明细表_礼品包装税`), 0) as s6,
                IFNULL(SUM(`结算明细表_促销折扣`), 0) as s7,
                IFNULL(SUM(`结算明细表_促销折扣税`), 0) as s8,
                IFNULL(SUM(`结算明细表_平台代扣税`), 0) as s9,
                IFNULL(SUM(`结算明细表_销售佣金`), 0) as s10,
                IFNULL(SUM(`结算明细表_fba配送费`), 0) as s11,
                IFNULL(SUM(`结算明细表_其他交易费`), 0) as s12,
                IFNULL(SUM(`结算明细表_其他`), 0) as s13,
                IFNULL(SUM(`结算明细表_总计`), 0) as s14
            FROM `tabAmazon Settlement Detail`
            WHERE `链接主表` = %s
        """
        # 注意：Frappe 子表的关联字段通常是 'parent'，如果是你自定义的 '链接主表' 请保持原样
        
        查询结果 = frappe.db.sql(数据库函数, (self.name,), as_dict=True)
        res = 查询结果[0]
        # --- 数据回写阶段 ---
        # 1. 定义需要累加的键名，必须是比14大一个15才能包括14
        需要累加的键 = [f"s{i}" for i in range(1, 15)]

        # 2. 执行加总：在加之前，强制用 flt() 把每个值转成浮点数
        # 这样即使数据库返回的是字符串 "100.5" 或 None，都能正确参与计算
        从分项计算的总和 = sum([flt(res.get(key, 0)) for key in 需要累加的键])

        # 3. 回写并保留两位小数
        self.校对金额 = 0
        self.校对金额 = flt(从分项计算的总和, 2)
        # 将统计结果保存到数据库的主表中
        # ignore_permissions=True 确保即使当前用户权限受限也能完成统计更新
        self.save(ignore_permissions=True)
        # 强制提交事务，确保前端 reload_doc() 时能看到最新的统计数字
        frappe.db.commit()
        
        return True
