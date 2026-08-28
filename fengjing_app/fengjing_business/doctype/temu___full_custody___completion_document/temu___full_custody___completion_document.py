# Copyright (c) 2026, Fengjing E-Commerce and contributors
# For license information, please see license.txt

import frappe
import json
import os
import pandas as pd             # 处理 Excel
from urllib.parse import unquote  # 处理 URL 中文编码
from frappe.model.document import Document

class TEMUFullCustodyCompletionDocument(Document):
	pass  # 类保持为空即可

# 按下按钮开始获取附件
@frappe.whitelist()
def 批量处理文件(关联文档名称, 文件列表):

	# 开始挨个文件写入子表内
	def 财务明细_三区(区域, 主文档, 文件路径):
		# 统计小账本，用于记录当前文件的处理情况
		单个文件统计 = {
			"文件名称": os.path.basename(文件路径),
			"文件类型": f"财务明细({区域})",
			"涉及页签": set()
		}

		# 1. 加载 Excel 文件
		表格文件 = pd.ExcelFile(文件路径, engine='openpyxl')
		所有页签名称 = 表格文件.sheet_names

		for 页签 in 所有页签名称:
			单个文件统计["涉及页签"].add(页签)

			# --- 处理：售后问题 ---
			if "消费者及履约保障-售后问题" in 页签:
				数据表 = pd.read_excel(文件路径, sheet_name=页签, engine='openpyxl')

				# 缓存 SKU 映射，减少数据库查询次数
				所有平台SKU = [str(x) for x in 数据表["SKU ID"].dropna().unique().tolist()]
				SKU映射缓存 = {}
				# 建议在查询前加个判断，防止所有平台SKU为空列表导致全表扫描
				if 所有平台SKU:
					映射记录 = frappe.db.get_all(
						"Fengjing - Product Corresponding Platform - Main Table",
						filters={"平台sku": ["in", 所有平台SKU]},
						fields=["平台sku", "物料id", "物料名称"]
					)

					for 项 in 映射记录:
						# 使用 .get() 防止某个字段偶然为空导致 Key 系统报错
						sku_val = str(项.get("平台sku") or "")
						if sku_val:
							SKU映射缓存[sku_val] = {
								"id": 项.get("物料id"),
								"name": 项.get("物料名称")
							}
				for 索引, 行 in 数据表.iterrows():
					原始SKU = 行.get("SKU ID")
					# 如果表格文档内不存在skuid那整个行都不会写入
					if not 原始SKU or pd.isna(原始SKU):
						continue
					
					当前SKU = str(原始SKU)
					物料信息 = SKU映射缓存.get(当前SKU, {})
					物料ID = 物料信息.get("id", "")
					物料名称 = 物料信息.get("name", "")

					# 不存在的内容改为空置为了写入子表内部报错
					清洗行 = {str(k): ("" if pd.isna(v) else v) for k, v in 行.items()}

					# 写入售后问题子表
					主文档.append("售后问题", {
						"内部物料id": 物料ID, 
						"内部物料名称": 物料名称,
						"区域": 区域,
						"违规id": 清洗行.get("违规ID"),
						"sku_id": 当前SKU,
						"货品名称": 清洗行.get("货品名称"),
						"sku货号": 清洗行.get("SKU货号"),
						"赔付金额": 清洗行.get("赔付金额"),
						"币种": 清洗行.get("币种"),
						"账务时间": 清洗行.get("账务时间")
					})

			# --- 处理：售后补寄 ---
			if "消费者及履约保障-售后补寄" in 页签:
				数据表 = pd.read_excel(文件路径, sheet_name=页签, engine='openpyxl')

				for 索引, 行 in 数据表.iterrows():
					# 如果表格内不存在skuid就整个跳出,到下一行
					当前SKU = 行.get("SKU ID")
					if not 当前SKU:
						continue
					
					映射查询 = frappe.db.get_value(
						"Fengjing - Product Corresponding Platform - Main Table", 
						{"平台sku": 当前SKU}, 
						["物料id", "物料名称"],
						as_dict=1
					)
					物料ID = 映射查询.get("物料id") if 映射查询 else ""
					物料名称 = 映射查询.get("物料名称") if 映射查询 else ""

					清洗行 = {k: ("" if pd.isna(v) else v) for k, v in 行.items()}

					主文档.append("补寄", {
						"区域": 区域,
						"订单编号": 清洗行.get("订单编号"),
						"sku_id": 当前SKU,
						"货品名称": 清洗行.get("货品名称"),
						"sku货号": 清洗行.get("SKU货号"),
						"赔付金额": 清洗行.get("赔付金额"),
						"sku属性": 清洗行.get("SKU属性"),
						"数量": 清洗行.get("数量"),
						"内部物料id": 物料ID, 
						"内部物料名称": 物料名称
					})

			# --- 处理：交易结算 ---
			if "交易结算" in 页签:
				数据表 = pd.read_excel(文件路径, sheet_name=页签, engine='openpyxl')
				
				所有平台SKU = [str(x) for x in 数据表["SKU ID"].dropna().unique().tolist()]
				SKU映射缓存 = {}
				if 所有平台SKU:
					映射记录 = frappe.db.get_all(
						"Fengjing - Product Corresponding Platform - Main Table",
						filters={"平台sku": ["in", 所有平台SKU]},
						fields=["平台sku", "物料id", "物料名称"]
					)
					for 项 in 映射记录:
						SKU映射缓存[str(项["平台sku"])] = {"id": 项["物料id"], "name": 项["物料名称"]}

				for 索引, 行 in 数据表.iterrows():
					# 表格内skuid不存在就整个跳出
					原始SKU = 行.get("SKU ID")
					if not 原始SKU or pd.isna(原始SKU):
						continue
					
					当前SKU = str(原始SKU)
					物料信息 = SKU映射缓存.get(当前SKU)
					物料ID = 物料信息.get("id", "") if 物料信息 else ""
					物料名称 = 物料信息.get("name", "") if 物料信息 else ""

					# 数据清洗：特定列为空则变0，其余为空变空字符串
					清洗行 = {}
					for k, v in 行.items():
						if pd.isna(v):
							if any(关键字 in str(k) for 关键字 in ["金额", "数量", "价", "折扣", "券"]):
								清洗行[k] = 0
							else:
								清洗行[k] = " "
						else:
							清洗行[k] = v

					交易类型 = 清洗行.get("交易类型")
					行字典数据 = {
						"内部物料id": 物料ID, 
						"内部物料名称": 物料名称,
						"区域": 区域,
						"订单编号": 清洗行.get("订单编号"),
						"售后单号": 清洗行.get("售后单号"),
						"备货单号": 清洗行.get("备货单号"),
						"备货单类型": 清洗行.get("备货单类型"),
						"sku_id": 当前SKU,
						"sku货号": 清洗行.get("SKU货号"),
						"货品名称": 清洗行.get("货品名称"),
						"sku属性": 清洗行.get("SKU属性"),
						"数量": 清洗行.get("数量"),
						"单品券金额": 清洗行.get("单品券金额"),
						"店铺满减券金额": 清洗行.get("店铺满减券金额"),
						"申报价格折扣金额": 清洗行.get("申报价格折扣金额"),
						"交易类型": 交易类型,
						"金额": 清洗行.get("金额"),
						"币种": 清洗行.get("币种"),
						"账务时间": 清洗行.get("账务时间")
					}

					if 交易类型 == "销售回款":
						主文档.append("销售回款", 行字典数据)
					elif 交易类型 == "销售冲回":
						主文档.append("销售冲回", 行字典数据)
					elif 交易类型 == "非商责补贴":
						主文档.append("非商责补贴", 行字典数据)

		# 循环结束后统一保存
		主文档.save()
		return 单个文件统计

	# 开始写入卖家中心总表
	def 财务明细_卖家中心(主文档, 文件路径):
		# 统计小账本，用于记录当前文件的处理情况
		单个文件统计 = {
			"文件名称": os.path.basename(文件路径),
			"涉及页签": set()
		}

		# 1. 加载 Excel 文件
		表格文件 = pd.ExcelFile(文件路径, engine='openpyxl')
		所有页签名称 = 表格文件.sheet_names

		for 页签 in 所有页签名称:
			单个文件统计["涉及页签"].add(页签)

			# --- 处理：售后问题 ---
			if "账务明细列表" in 页签:
				数据表 = pd.read_excel(文件路径, sheet_name=页签, engine='openpyxl')
				# 假设 数据表 是你已经用 pandas 读取好的 DataFrame
				for 索引, 行 in 数据表.iterrows():
					# 处理备注：如果为空（NaN 或 None），则给一个空字符串
					备注内容 = 行.get("备注")
					if not 备注内容 or str(备注内容).lower() == 'nan':
						备注内容 = ""
					row_data = {
						"财务时间": 行.get("账务时间"),
						"账务类型": 行.get("账务类型"),
						"币种": 行.get("币种"),
						"收支金额": 行.get("收支金额"),
						"备注": 备注内容
					}
					# 分流写入
					if 行.get("账务类型") == "提现":
						主文档.append("结算已提现", row_data) # 假设字段名就是中文或对应Name
					elif 行.get("账务类型") == "结算":
						主文档.append("结算未提现", row_data)
					elif 行.get("账务类型") == "支出":
						备注 = str(行.get("备注") or "")
						
						# 初始化目标子表变量
						目标子表 = None
						
						# --- 二级分类判断开始 ---
						if "仓储综合服务费" in 备注:
							目标子表 = "仓储综合服务费"
							
						elif "消费者及履约保障-售后问题" in 备注:
							目标子表 = "全球财务明细_售后问题"
							
						elif "合规EPR环保费代扣代缴" in 备注:
							# 模糊匹配，解决货号随机变化的问题
							目标子表 = "合规epr环保费代扣代缴"
							
						elif "合规EPR物流包装环保费" in 备注:
							# 模糊匹配，解决货号随机变化的问题
							目标子表 = "合规epr物流包装代扣代缴"

						elif "推广服务费" in 备注:
							目标子表 = "推广服务"
							
						elif "消费者及履约保障-售后补寄" in 备注:
							目标子表 = "全球财务明细_售后补寄"
							
						else:
							# 如果备注不属于以上任何一种，直接抛出异常，中断程序并弹窗
							frappe.throw(f"很严重的错误！发现未定义的支出备注类型：【{备注}】，程序已停止运行。电话：15953992133")

						# --- 写入对应的子表 ---
						if 目标子表:
							主文档.append(目标子表, {
								"财务时间": 行.get("账务时间"),
								"账务类型": 行.get("账务类型"),
								"币种": 行.get("币种"),
								"收支金额": frappe.utils.flt(str(行.get("收支金额", 0)).replace('+', '')),
								"备注": 备注
							})
		# 保存
		主文档.save()
		return 单个文件统计




	# --- 主处理逻辑开始 ---
	# 1. 获取主文档并清空历史子表
	主文档 = frappe.get_doc("TEMU - Full Custody - Completion Document", 关联文档名称)
	待清空子表 = ["销售回款", "非商责补贴", "销售冲回", "售后问题", "补寄", "结算未提现", "结算已提现", "全球财务明细_售后问题", "全球财务明细_售后补寄", "推广服务", "仓储综合服务费", "合规epr物流包装代扣代缴", "合规epr环保费代扣代缴"]
	for 子表 in 待清空子表:
		主文档.set(子表, [])
	主文档.save()

	# 判断传入的 文件列表 是不是字符串，如果是，就把它解析成 Python 的列表（List）或字典（Dict）
	文件列表 = json.loads(文件列表) if isinstance(文件列表, str) else 文件列表

	# 2. 遍历附件列表进行处理
	三区_所有处理报告 = []
	卖家中心_所有处理报告 = []
	for 文件行 in 文件列表:
		文件链接 = 文件行.get("文件")
		文件类型 = 文件行.get("文件类型")
		# 如果不存在链接就跳出
		if not 文件链接:
			continue

		# 处理中文编码
		解码URL = unquote(文件链接)
		if "/private/files/" in 解码URL:
			相对路径 = "private/files/" + 解码URL.split("/private/files/")[1]
		elif "/files/" in 解码URL:
			相对路径 = "public/files/" + 解码URL.split("/files/")[1]
		else:
			相对路径 = 解码URL.split("/")[-2] + "/" + 解码URL.split("/")[-1]
		
		文件绝对路径 = frappe.get_site_path(相对路径)
		
		if not os.path.exists(文件绝对路径):
			# 找不到文件会跳出的
			frappe.throw(f"找不到物理文件：<br>{文件绝对路径}")

		三区_处理结果 = None
		卖家中心_处理结果 = None
		if 文件类型 == "财务明细(美区)":
			三区_处理结果 = 财务明细_三区("美区", 主文档, 文件绝对路径)
		elif 文件类型 == "财务明细(欧区)":
			三区_处理结果 = 财务明细_三区("欧区", 主文档, 文件绝对路径)
		elif 文件类型 == "财务明细(全球)":
			三区_处理结果 = 财务明细_三区("全球", 主文档, 文件绝对路径)
		elif 文件类型 == "财务明细(卖家中心)":
			卖家中心_处理结果 = 财务明细_卖家中心(主文档, 文件绝对路径)


		if 卖家中心_处理结果:
			卖家中心_所有处理报告.append(卖家中心_处理结果)
		if 三区_处理结果:
			三区_所有处理报告.append(三区_处理结果)

	# 3. 汇总并生成最终 HTML 报告
	if 三区_所有处理报告 and 卖家中心_所有处理报告:
		主文档.reload() # 重新拉取保存后的最新子表数据
		
		区域映射 = {
			"财务明细(美区)": "美区",
			"财务明细(欧区)": "欧区",
			"财务明细(全球)": "全球"
		}

		HTML内容 = """
		<div id="为了加宽度增加的识别id" style="background-color: #f8f9fa; border-left: 4px solid #6c757d; padding: 10px; margin-bottom: 20px; font-size: 13px; color: #555;">
			💡 <b>温馨提示：</b><br>
			以下汇总数据基于子表【实盘盘点】，如有数据不准确请联系：<br>
			📞 <b>电话：</b>+86 15953992133 &nbsp;&nbsp; 📧 <b>邮箱：</b>1060778506@qq.com
		</div>
		<div style="display: flex;flex-direction: row;flex-wrap: wrap;justify-content: space-around;align-content: flex-start;">
		"""


		# 1. 定义你需要统计的子表列表 (请确保这些是 DocType 里的真实 Name)
		子表名单 = [
			"结算未提现", "结算已提现", "全球财务明细_售后问题", 
			"全球财务明细_售后补寄", "推广服务", "仓储综合服务费", 
			"合规epr物流包装代扣代缴", "合规epr环保费代扣代缴"
		]

		# 初始化全表总计
		全表总数量 = 0
		全表总金额 = 0.0
		报告结果 = [] # 用于给前端弹窗显示的列表

		for 名 in 子表名单:
			子表数据 = 主文档.get(名) or []
			单表数量 = len(子表数据)
			单表金额 = 0.0
			
			for 行 in 子表数据:
				# 提取金额字符串，去掉可能存在的空格，转为浮点数进行运算
				金额串 = str(行.get("收支金额") or "0").replace(' ', '')
				try:
					单表金额 += frappe.utils.flt(金额串)
				except:
					pass # 防止非法字符导致程序崩溃
					
			# 累加到全表
			全表总数量 += 单表数量
			全表总金额 += 单表金额
			
			# 记录该表结果（保留两位小数）
			报告结果.append({
				"子表": 名,
				"数量": 单表数量,
				"金额": round(单表金额, 2)
			})

		# 构造中间的明细列表 HTML
		明细行_html = ""
		for res in 报告结果:
			明细行_html += f"""
			<div style="display: flex; justify-content: space-between; font-size: 14px; color: #444; border-bottom: 1px solid #eef2f7; padding: 6px 0;">
				<span>• {res['子表']} ({res['数量']} 行)</span>
				<span style="font-family: monospace; font-weight: bold;">{res['金额']:,.2f}</span>
			</div>
			"""

		# 最终组合模板
		html_report = f"""
		<div style="display: flex; justify-content: center; font-family: sans-serif;">
			<div style="border: 2px solid #2490ef; padding: 20px; background: #f0f7ff; border-radius: 12px; line-height: 1.8; width: 500px; box-shadow: 0 4px 15px rgba(0,0,0,0.05);">
				
				<div style="color: #2490ef; font-size: 18px; font-weight: bold; text-align: center; margin-bottom: 15px; border-bottom: 2px solid #2490ef; padding-bottom: 8px;">
					📋 财务数据实盘统计汇总
				</div>

				<div style="margin-bottom: 10px;">
					<b style="color: #2490ef; font-size: 15px;">📊 涉及页签明细：</b>
					<div style="margin-top: 8px; background: #fff; padding: 12px; border-radius: 8px; border: 1px solid #d1e7ff;">
						{明细行_html}
					</div>
				</div>

				<div style="background: #2490ef; color: #fff; padding: 15px; border-radius: 10px; margin-top: 15px; display: flex; justify-content: space-between; align-items: center;">
					<div style="font-size: 14px;">
						<b>全场合计总数：</b><br>{全表总数量} 行记录
					</div>
					<div style="font-size: 24px; font-weight: bold; text-align: right;">
						<span style="font-size: 14px; font-weight: normal;">总金额：</span><br>￥{全表总金额:,.2f}
					</div>
				</div>

				<div style="color: #888; font-size: 12px; margin-top: 15px; text-align: center;">
					* 统计完成时间：{frappe.utils.get_datetime_str(frappe.utils.now_datetime())}
				</div>
			</div>
		</div>
		"""


		全场未绑定总行数 = 0
		全场缺失SKU集合 = set()
		全场页签集合 = set()

		# 遍历每个文件的处理结果拼接卡片
		for 报告 in 三区_所有处理报告:
			当前文件类型 = 报告.get("文件类型")
			目标区域 = 区域映射.get(当前文件类型)
			涉及页签 = 报告.get("涉及页签", [])
			页签文本 = "".join([f"【{x}】" for x in 涉及页签])
			全场页签集合.update(涉及页签)

			if 目标区域:
				区域回款 = [d for d in 主文档.get("销售回款") if d.区域 == 目标区域]
				区域冲回 = [d for d in 主文档.get("销售冲回") if d.区域 == 目标区域]
				区域补贴 = [d for d in 主文档.get("非商责补贴") if d.区域 == 目标区域]
				区域售后 = [d for d in 主文档.get("售后问题") if d.区域 == 目标区域]
				区域补寄 = [d for d in 主文档.get("补寄") if d.区域 == 目标区域]

				区域总金额 = sum(frappe.utils.flt(x.金额) for x in (区域回款 + 区域冲回 + 区域补贴))
				
				全部行 = 区域回款 + 区域冲回 + 区域补贴 + 区域售后 + 区域补寄
				未绑定SKU列表 = list(set([str(行.sku_id) for 行 in 全部行 if not 行.内部物料名称]))
				未绑定行数 = len([行 for 行 in 全部行 if not 行.内部物料名称])
				
				全场未绑定总行数 += 未绑定行数
				全场缺失SKU集合.update(未绑定SKU列表)

				# 拼接文件明细 HTML（改为换行显示）
				HTML内容 += f"""
				<div style="border: 1px solid #d1d8dd; padding: 12px; margin-bottom: 10px; border-radius: 6px; line-height: 1.8;width: 48%;">
					<div style="color: #171717; font-weight: bold; border-bottom: 1px solid #eee; margin-bottom: 8px;">
						文件名称：{报告.get('文件名称')}
					</div>
					<b>文件类型：</b> {当前文件类型}<br>
					<b>涉及页签：</b> {页签文本 if 页签文本 else "无"}<br>
					<div style="margin-top: 5px; color: #2490ef; font-weight: bold;">
						结算类共：{len(区域回款) + len(区域冲回) + len(区域补贴)} 个
					</div>
					<div style="padding-left: 10px; font-size: 13px; color: #666;">
						• 非商责补贴：{len(区域补贴)}<br>
						• 销售冲回：{len(区域冲回)}<br>
						• 销售回款：{len(区域回款)}
					</div>
					<div style="color: #2490ef; font-weight: bold; margin-top: 5px;">
						结算总金额：{区域总金额:.2f}
					</div>
					<div style="margin-top: 5px; font-weight: bold;">
						售后信息：
					</div>
					<div style="padding-left: 10px; font-size: 13px; color: #666;">
						• 售后问题：{len(区域售后)} 个<br>
						• 售后补寄：{len(区域补寄)} 个
					</div>
					<div style="color: #ff5858; background: #fff5f5; padding: 5px; border-radius: 4px; margin-top: 8px; font-size: 12px;">
						<b>⚠️ 未绑定行数：</b>{未绑定行数} 行<br>
						<b>⚠️ 缺失SKU去重：</b>{", ".join(未绑定SKU列表) if 未绑定SKU列表 else "无"}
					</div>
				</div>
				"""

		# 4. 拼接全场汇总大卡片（改为换行显示）
		全场总金额 = sum(frappe.utils.flt(行.金额) for 表 in ["销售回款", "销售冲回", "非商责补贴"] for 行 in 主文档.get(表))
		汇总结算个数 = len(主文档.get("销售回款")) + len(主文档.get("销售冲回")) + len(主文档.get("非商责补贴"))
		缺失SKU显示 = ", ".join(全场缺失SKU集合) if 全场缺失SKU集合 else "无"
		全场页签文本 = "".join([f"【{x}】" for x in sorted(list(全场页签集合))])

		HTML内容 += f"""
			<div style="border: 2px solid #2490ef; padding: 15px; margin-bottom: 10px; background: #f0f7ff; border-radius: 8px; line-height: 2.0;width: 48%;">
				<div style="color: #2490ef; font-size: 16px; font-weight: bold; text-align: center; margin-bottom: 10px; border-bottom: 2px solid #2490ef;">
					📋 全场数据汇总 (数据库实盘统计)
				</div>
				<b>涉及页签汇总：</b><br>
				<div style="font-size: 13px; color: #444; line-height: 1.5;">{全场页签文本}</div>
				<div style="color: #2490ef; font-weight: bold;">
					结算三项总计：{汇总结算个数} 个
				</div>
				<div style="padding-left: 15px; font-size: 14px; color: #444;">
					• 非商责补贴总计：{len(主文档.get("非商责补贴"))}<br>
					• 销售冲回总计：{len(主文档.get("销售冲回"))}<br>
					• 销售回款总计：{len(主文档.get("销售回款"))}
				</div>
				<div style="color: #2490ef; font-size: 17px; font-weight: bold; margin-top: 5px;">
					全场实盘总金额：{全场总金额:.2f}
				</div>
				<div style="margin-top: 5px; font-weight: bold;">
					全场售后统计：
				</div>
				<div style="padding-left: 15px; font-size: 14px; color: #444;">
					• 售后问题总计：{len(主文档.get("售后问题"))}<br>
					• 售后补寄总计：{len(主文档.get("补寄"))}
				</div>
				<div style="color: #ff5858; background: #fff5f5; padding: 10px; border-radius: 4px; margin-top: 10px; border: 1px solid #ffcccc; font-size: 13px;">
					<b>⚠️ 全场未绑定总数：</b> {全场未绑定总行数} 行<br>
					<b>⚠️ 全场缺失平台SKU：</b> {缺失SKU显示}
				</div>
			</div>
		</div>
		"""
		HTML内容 = HTML内容 + html_report
		# 弹出最终报告
		
		# frappe.msgprint(msg=HTML内容, title="📊 财务处理最终对账报告", wide=True)
		# 返回给前端：HTML 内容 + 更新后的文档数据
		return {
			"html": HTML内容,
			"doc": 主文档  # 关键：把保存后的新状态传回去
		}

	return {
		"html": "<div style='color:red;'>⚠️ 未处理任何文件，请检查子表附件。</div>",
		"doc": 主文档  # 关键：把保存后的新状态传回去
	}