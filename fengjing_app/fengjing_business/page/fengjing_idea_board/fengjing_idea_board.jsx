import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Kanban, Willow } from "@svar-ui/react-kanban";
import "@svar-ui/react-kanban/style.css";
import "./idea_board.css";

const BASE = "fengjing_app.fengjing_business.page.fengjing_idea_board.fengjing_idea_board";
const COLUMNS = ["收集箱", "待评估", "待做", "进行中", "已完成"].map((id) => ({ id, label: id, addCard: false }));
const OPTIONS = COLUMNS.map(({ id }) => id).join("\n");

async function call(method, args = {}) {
	const result = await window.frappe.call({ method: `${BASE}.${method}`, args });
	return result.message;
}

function showError(action, error) {
	console.error(action, error);
	window.frappe.show_alert({ message: `${action}失败，请重试`, indicator: "red" }, 6);
}

function IdeaCard({ card, onEdit }) {
	return <div className="fjib-card" onClick={(event) => { event.stopPropagation(); onEdit(card); }}>
		<div className="fjib-card-title">{card.label}</div>
		{card.description && <div className="fjib-card-description">{card.description}</div>}
		<div className="fjib-card-meta">
			{card.category && <span className="fjib-tag">{card.category}</span>}
			{card.priority !== "普通" && <span className={`fjib-priority ${card.priority === "紧急" ? "urgent" : ""}`}>{card.priority}</span>}
			{card.target_date && <span className="fjib-date">{card.target_date}</span>}
		</div>
	</div>;
}

function Board() {
	const [cards, setCards] = useState(null);
	const [boardKey, setBoardKey] = useState(0);
	const apiRef = useRef(null);
	const moveQueue = useRef(Promise.resolve());

	const reload = useCallback(async () => {
		try {
			setCards(await call("get_cards") || []);
			setBoardKey((value) => value + 1);
		} catch (error) { showError("读取想法池", error); }
	}, []);
	useEffect(() => { reload(); }, [reload]);

	const openDialog = useCallback((card = null) => {
		const editing = Boolean(card?.id);
		const dialog = new window.frappe.ui.Dialog({
			title: editing ? "编辑想法" : "记录想法",
			fields: [
				{ fieldname: "title", fieldtype: "Data", label: "想法", reqd: 1, default: card?.label || "" },
				{ fieldname: "details", fieldtype: "Small Text", label: "说明", default: card?.description || "" },
				{ fieldname: "category", fieldtype: "Data", label: "分类", default: card?.category || "" },
				...(!editing ? [{ fieldname: "status", fieldtype: "Select", label: "放入", options: OPTIONS, default: "收集箱" }] : []),
				{ fieldname: "priority", fieldtype: "Select", label: "优先级", options: "普通\n重要\n紧急", default: card?.priority || "普通" },
				{ fieldname: "target_date", fieldtype: "Date", label: "计划日期（可不填）", default: card?.target_date || "" },
			],
			primary_action_label: "保存",
			primary_action: async (values) => {
				if (!values) return;
				try {
					const saved = await call("save_card", {
						name: editing ? card.id : undefined,
						data: JSON.stringify({ ...values, status: editing ? card.column : values.status }),
					});
					if (apiRef.current) {
						await apiRef.current.exec(editing ? "update-card" : "add-card", { id: saved.id, card: saved });
						setCards(apiRef.current.getCards());
					} else await reload();
					dialog.hide();
					window.frappe.show_alert({ message: "想法已保存", indicator: "green" });
				} catch (error) { showError("保存想法", error); }
			},
		});
		dialog.show();
		if (editing) {
			const button = document.createElement("button");
			button.type = "button";
			button.className = "btn btn-danger btn-sm";
			button.textContent = "删除";
			dialog.$wrapper.find(".modal-footer").prepend(button);
			button.addEventListener("click", () => {
				window.frappe.confirm(`确定删除“${window.frappe.utils.escape_html(card.label)}”吗？`, async () => {
					try {
						await call("delete_card", { name: card.id });
						if (apiRef.current) {
							await apiRef.current.exec("delete-card", { id: card.id });
							setCards(apiRef.current.getCards());
						} else await reload();
						dialog.hide();
						window.frappe.show_alert({ message: "想法已删除", indicator: "green" });
					} catch (error) { showError("删除想法", error); }
				});
			});
		}
	}, [reload]);

	const handleMove = useCallback(({ id, column, before }) => {
		const target = column || apiRef.current?.getCards().find((item) => item.id === id)?.column;
		if (!target) return;
		moveQueue.current = moveQueue.current.then(() => call("move_card", {
			name: id, column: target, before: before || null,
		})).catch(async (error) => { showError("移动想法", error); await reload(); });
	}, [reload]);

	return <div className="fjib-app">
		<div className="fjib-header">
			<div><h2>经营想法</h2><p>随时记录，按进展拖动；计划日期可以留空。</p></div>
			<div className="fjib-header-actions">
				<span>{cards?.length || 0} 个想法</span>
				<button className="btn btn-primary" onClick={() => openDialog()}>＋ 记录想法</button>
			</div>
		</div>
		{cards === null ? <div className="fjib-loading">正在读取…</div> : <Willow>
			<Kanban key={boardKey} cards={cards} columns={COLUMNS} init={(api) => { apiRef.current = api; }}
				cardContent={({ card }) => <IdeaCard card={card} onEdit={openDialog} />}
				onMoveCard={handleMove} />
		</Willow>}
	</div>;
}

export function mountIdeaBoard(element) {
	const root = createRoot(element);
	root.render(<Board />);
	return root;
}
