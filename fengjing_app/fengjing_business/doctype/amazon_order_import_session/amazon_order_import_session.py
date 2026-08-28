# -*- coding: utf-8 -*-
# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
import csv
import os
import json
import io
import hashlib
import datetime
from frappe.utils.file_manager import get_file_path
from frappe.utils import flt, get_files_path
from datetime import datetime, timedelta
from frappe import _
from fengjing_app import fengmaode

class AmazonOrderImportSession(Document):


    #去下载翻译的文件了,去公共区域
    @frappe.whitelist(methods=["GET", "POST", "HEAD"])
    def 下载修改过的基础订单文件(self):
        文件路径 = get_file_path(self.订单原始文件)
        # 3. 执行拿到的函数，并传入参数
        处理函数 = getattr(fengmaode, "翻译亚马逊表格")
        处理函数(文件路径, "Amazon Order Summary",self)


    @frappe.whitelist()
    def 开始导入订单(self):
        # 同步前端临时数据,可能没有保存,读取实时的数据
        if frappe.form_dict.doc:
            # 将前端传来的 JSON 字符串解析为字典
            前端单据数据 = json.loads(frappe.form_dict.doc)
            # 将这些最新的改动更新到当前的实例对象（self）中
            self.update(前端单据数据)

        # 先删除旧的关联，准备重新写入 不管三七二十一先重置
        frappe.db.sql("DELETE FROM `tabAmazon Order Summary` WHERE `导入数据页关联` = %s", (self.name,))
        frappe.db.commit()

        #金额先清零
        self.开始统计各种金额()
        self.总行数 = 0

        # 1. 先准备一个空字典，用序号做键
        已存在的子表数据 = {}
        # 2. 遍历子表，使用 enumerate 拿到序号 (idx)
        # start=1 表示从第 1 行开始计数，对应 ERP 界面上的行号
        for 序号, d in enumerate(self.导入内容, start=1):
            # 清洗并打包数据
            行数据快照 = {
                "亚马逊订单号": str(d.亚马逊订单号).strip(),
                "下单时间": str(d.下单时间).strip(),
                "数量": str(d.数量).strip(),
                "卖家skuid": str(d.卖家skuid).strip()
            }
            # 4. 以序号为键存入字典
            已存在的子表数据[序号] = 行数据快照

        # 核心判断：如果子表是空的 (第3处修改)
        if not 已存在的子表数据:
            frappe.msgprint("子表『导入内容』中没有订单号，并且之前没有记录任何订单。", title="无任务", indicator="blue")
            # 没有导入成功直接返回
            return True

        # 4. 获取文件路径 (第4处修改)
        订单原始文件路径 = get_file_path(self.订单原始文件)
        if not os.path.exists(订单原始文件路径):
            frappe.msgprint("找不到附件物理文件,请重新上传,应该是系统问题,文件的接口更新了? 电话:15953992133, 进行修复", title="文件错误", indicator="red")
            return True

        # 去看看三表是否一致,如果不一致不用回来了
        处理函数 = getattr(fengmaode, "亚马逊文件关联表存储表是否一致")
        文件字段映射字典 = 处理函数(订单原始文件路径, "Amazon Order Summary", self.站点区)
        if 文件字段映射字典 is None:
            # 说明 fengmaode 内部已经报错并 return 了
            # 这里直接结束当前函数，防止跑到第 117 行触发 AttributeError
            return

        # 开始写入
        # 1. 自动判断编码 (一行流)
        编码 = 'utf-8-sig'
        try: open(订单原始文件路径, 'r', encoding=编码).read(4096)
        except: 编码 = 'gbk'
        # 确定编码后开始循环
        最终导入列表 = []
        with open(订单原始文件路径, 'r', encoding=编码) as f_in:
            样本 = f_in.read(4096)
            f_in.seek(0)
            # 谁的数量多,确定格式
            tab个数 = 样本.count('\t')
            逗号个数 = 样本.count(',')
            分隔符 = '\t' if tab个数 > 逗号个数 else ','
            # 它会自动把第一行撕下来当成 Key (键)
            读取器 = csv.DictReader(f_in, delimiter=分隔符)
            准入名单 = {
                f"{v['亚马逊订单号']}|{v['卖家skuid']}|{v['数量']}|{v['下单时间']}" 
                for v in 已存在的子表数据.values()
            }
            for 行数据 in 读取器:
                # 2. 提取当前文件行核心字段
                文件订单号 = str(行数据.get('amazon-order-id', '')).strip()
                文件SKU = str(行数据.get('sku', '')).strip()
                文件数量 = str(行数据.get('quantity', '')).strip()
                文件时间 = str(行数据.get('purchase-date', '')).strip()
                # 组合当前行的“指纹”
                当前行指纹 = f"{文件订单号}|{文件SKU}|{文件数量}|{文件时间}"
                # 3. 核心逻辑：如果【不在】准入名单里，说明是多余数据，直接跳出
                if 当前行指纹 not in 准入名单:
                    break
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
            行数据["本页md5"] = 行指纹
            行数据["导入数据页关联"] = self.name
            行数据["目标店铺"] = self.目标店铺
            
            带指纹的导入列表.append(行数据)







        # 1. 拿着 md5 全盘扫描已有的数据
        # 使用 set 存储 MD5 池，性能最优
        已存在的MD5池 = set(frappe.get_all("Amazon Order Summary", pluck="本页md5"))
        # 2. 准备装重复信息的篮子
        重复的行信息 = []
        # 3. 循环带指纹的列表进行比对
        for 行数据 in 带指纹的导入列表:
            当前行md5 = 行数据.get("本页md5")
            
            if 当前行md5 in 已存在的MD5池:
                # 发现重复！提取关键信息用于弹窗显示
                # 这里的 key 名建议和你字典转换后的 key 名对齐
                订单号 = 行数据.get("亚马逊订单号", "未知订单")
                SKU = 行数据.get("卖家sku", "未知SKU")
                
                重复项描述 = f"• 订单: {订单号} | SKU: {SKU} (MD5: {当前行md5[:8]}...)"
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
            汇总单据 = frappe.new_doc("Amazon Order Summary")
            
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

        # 弹出成功提示
        frappe.msgprint(
            msg=f"<b>导入完成！</b><br>已成功入库 {记录导入总数} 条订单数据。",
            title="操作成功",
            indicator="green"
        )
        return True




    @frappe.whitelist()
    def 开始统计各种金额(self):
        """
        专门负责统计当前导入批次的各项金额，并将结果回写到当前的 Session（主表）中
        """
        # 使用 SQL 聚合函数 SUM 直接计算数据库中所有属于本次导入任务的明细金额
        # IFNULL(..., 0) 的作用是：如果还没导入数据，求和结果会是 None，此时强制转为 0，防止后面计算报错
        数据库函数 = """
            SELECT 
                IFNULL(SUM(`商品售价`), 0) as s1, 
                IFNULL(SUM(`商品税费`), 0) as s2, 
                IFNULL(SUM(`买家支付的运费`), 0) as s3, 
                IFNULL(SUM(`运费税`), 0) as s4,
                IFNULL(SUM(`礼品包装费`), 0) as s5, 
                IFNULL(SUM(`礼品包装税`), 0) as s6,
                IFNULL(SUM(`商品促销折扣`), 0) as s7, 
                IFNULL(SUM(`运费促销折扣`), 0) as s8
            FROM `tabAmazon Order Summary`
            WHERE `导入数据页关联` = %s
        """
        # 执行查询，self.name 就是当前导入任务的 ID
        查询到的内容 = frappe.db.sql(数据库函数, (self.name,), as_dict=True)
        
        # 获取查询结果的第一行（也是唯一一行）
        res = 查询到的内容[0]

        # --- 数据回写阶段 ---
        # 使用 flt(数值, 2) 强制保留两位小数，确保符合货币格式
        self.商品售价 = flt(res.s1, 2)
        self.商品税费 = flt(res.s2, 2)
        self.买家支付的运费 = flt(res.s3, 2)
        self.运费税 = flt(res.s4, 2)
        self.礼品包装费 = flt(res.s5, 2)
        self.礼品包装税 = flt(res.s6, 2)
        self.商品促销折扣 = flt(res.s7, 2)
        self.运费促销折扣 = flt(res.s8, 2)

        # 计算【最终总计】：将以上所有分项金额累加，并再次进行两位小数格式化
        # 提示：注意折扣字段在亚马逊原始数据中通常是负数，所以这里用加法即可
        self.最终总计 = flt(
            self.商品售价 + self.商品税费 + self.买家支付的运费 + self.运费税 + 
            self.礼品包装费 + self.礼品包装税 + self.商品促销折扣 + self.运费促销折扣, 
            2
        )
        # 将统计结果保存到数据库的主表中
        # ignore_permissions=True 确保即使当前用户权限受限也能完成统计更新
        self.save(ignore_permissions=True)
        
        # 强制提交事务，确保前端 reload_doc() 时能看到最新的统计数字
        frappe.db.commit()
        
        return True
