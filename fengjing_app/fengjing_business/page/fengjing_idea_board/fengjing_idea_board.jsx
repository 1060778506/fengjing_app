import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Kanban, Editor, Willow, registerEditorItem } from "@svar-ui/react-kanban";
import "@svar-ui/react-kanban/all.css";
import "./idea_board.css";

const BASE = "fengjing_app.fengjing_business.page.fengjing_idea_board.fengjing_idea_board";
const DRAFT_PREFIX = "fjib-draft-";
const COLUMNS = [
	{ id: "收集箱", label: "收集箱", css: "fjib-stage-inbox" },
	{ id: "待评估", label: "待评估", css: "fjib-stage-review" },
	{ id: "待做", label: "待做", css: "fjib-stage-ready" },
	{ id: "进行中", label: "进行中", css: "fjib-stage-active" },
	{ id: "已完成", label: "已完成", css: "fjib-stage-done" },
].map((column) => ({ ...column, addCard: true, cardLimit: true }));
const RENDER_CONFIG = { fixedColumnWidth: false, columnScroll: true };
const EMPTY_FILTERS = new Map();
const CARD_SHAPE = { menu: { options: [
	{ id: "edit-card", text: "编辑", icon: "wxi-edit" },
	{ id: "delete-card", text: "删除", icon: "wxi-delete" },
] } };
const EDITOR_BAR = { items: [
	{ comp: "icon", icon: "wxi-close", id: "close", title: "关闭" },
	{ comp: "spacer" },
	{ comp: "button", id: "delete", text: "删除", type: "danger" },
	{ comp: "button", id: "save", text: "保存", type: "primary" },
] };

function DateField({ value, onChange }) {
	return <input className="fjib-date-input" type="date" value={value || ""}
		onChange={(event) => onChange({ value: event.target.value })} />;
}
registerEditorItem("fjib-date", DateField);

const EDITOR_ITEMS = [
	{ comp: "text", key: "label", label: "想法", required: true },
	{ comp: "textarea", key: "description", label: "说明" },
	{ comp: "text", key: "category", label: "分类" },
	{ comp: "richselect", key: "column", label: "进展", options: COLUMNS.map(({ id, label }) => ({ id, label })) },
	{ comp: "richselect", key: "priority", label: "优先级", options: ["普通", "重要", "紧急"].map((id) => ({ id, label: id })) },
	{ comp: "fjib-date", key: "target_date", label: "计划日期（可不填）" },
];

async function call(method, args = {}) {
	const result = await window.frappe.call({ method: `${BASE}.${method}`, args });
	return result.message;
}

function showError(action, error) {
	console.error(action, error);
	window.frappe.show_alert({ message: `${action}失败，请重试`, indicator: "red" }, 6);
}

function isDraft(id) {
	return typeof id === "string" && id.startsWith(DRAFT_PREFIX);
}

function toDocument(card) {
	return {
		title: String(card.label || "").trim(),
		details: card.description || "",
		category: card.category || "",
		status: card.column || "收集箱",
		priority: card.priority || "普通",
		target_date: card.target_date || null,
	};
}

function confirmDelete(label) {
	return new Promise((resolve) => {
		window.frappe.confirm(
			`确定删除“${window.frappe.utils.escape_html(label || "这条想法")}”吗？`,
			() => resolve(true),
			() => resolve(false)
		);
	});
}

function IdeaCard({ card }) {
	return <div className="fjib-card">
		<button type="button" className="fjib-card-menu" data-action="menu" aria-label="卡片菜单">···</button>
		{(card.category || card.priority !== "普通") && <div className="fjib-card-top">
			{card.category && <span className="fjib-tag">{card.category}</span>}
			{card.priority !== "普通" && <span className={`fjib-priority ${card.priority === "紧急" ? "urgent" : ""}`}>{card.priority}</span>}
		</div>}
		<div className="fjib-card-title">{card.label || "新想法"}</div>
		{card.description && <div className="fjib-card-description">{card.description}</div>}
		<div className="fjib-card-footer">
			<span className="fjib-date">{isDraft(card.id) ? "尚未保存" : card.target_date ? `计划 ${card.target_date}` : "随时处理"}</span>
			<span className="fjib-card-open" aria-hidden="true">↗</span>
		</div>
	</div>;
}

function Board() {
	const [cards, setCards] = useState(null);
	const [boardKey, setBoardKey] = useState(0);
	const [api, setApi] = useState(null);
	const apiRef = useRef(null);
	const allowStoredAdd = useRef(false);
	const moveQueue = useRef(Promise.resolve());
	const draftNumber = useRef(0);
	const statusById = useRef(new Map());

	const reload = useCallback(async () => {
		try {
			const latest = await call("get_cards") || [];
			statusById.current = new Map(latest.map((card) => [card.id, card.column]));
			setApi(null);
			apiRef.current = null;
			setCards(latest);
			setBoardKey((value) => value + 1);
		} catch (error) { showError("读取想法池", error); }
	}, []);
	useEffect(() => { reload(); }, [reload]);

	const handleUpdate = useCallback(async (payload, instance) => {
		const current = instance.getCards().find((item) => item.id === payload.id);
		if (!current) return false;
		const merged = { ...current, ...payload.card };
		const data = toDocument(merged);
		if (!data.title) {
			window.frappe.show_alert({ message: "请填写想法标题", indicator: "orange" }, 5);
			return false;
		}
		try {
			if (isDraft(payload.id)) {
				const saved = await call("save_card", { data: JSON.stringify(data) });
				setTimeout(async () => {
					try {
						await instance.exec("delete-card", { id: payload.id });
						allowStoredAdd.current = true;
						try { await instance.exec("add-card", { id: saved.id, card: saved }); }
						finally { allowStoredAdd.current = false; }
						await instance.exec("select-card", { id: null });
						statusById.current.set(saved.id, saved.column);
						setCards(instance.getCards());
						window.frappe.show_alert({ message: "想法已保存", indicator: "green" });
					} catch (error) { showError("显示新想法", error); await reload(); }
				}, 0);
				return false;
			}
			if (data.status !== (statusById.current.get(payload.id) || current.column)) {
				await call("move_card", { name: payload.id, column: data.status });
			}
			const saved = await call("save_card", { name: payload.id, data: JSON.stringify(data) });
			statusById.current.set(payload.id, saved.column);
			payload.card = saved;
			window.frappe.show_alert({ message: "想法已保存", indicator: "green" });
			return true;
		} catch (error) {
			showError("保存想法", error);
			await reload();
			return false;
		}
	}, [reload]);

	const handleDelete = useCallback(async (payload, instance) => {
		if (isDraft(payload.id)) return true;
		const card = instance.getCards().find((item) => item.id === payload.id);
		if (!card || !await confirmDelete(card.label)) return false;
		try {
			await call("delete_card", { name: payload.id });
			return true;
		} catch (error) { showError("删除想法", error); return false; }
	}, []);

	const handleMove = useCallback(({ id, column, before }) => {
		if (isDraft(id)) return;
		const instance = apiRef.current;
		const target = column || instance?.getCards().find((item) => item.id === id)?.column;
		if (!target) return;
		moveQueue.current = moveQueue.current.then(() => call("move_card", {
			name: id, column: target, before: before || null,
		})).then(() => {
			statusById.current.set(id, target);
			setCards(instance.getCards());
		})
			.catch(async (error) => { showError("移动想法", error); await reload(); });
	}, [reload]);

	const handleEditorAction = useCallback(({ item, values }) => {
		const instance = apiRef.current;
		if (!instance) return;
		if (item.id === "delete" && values?.id) instance.exec("delete-card", { id: values.id });
		if (item.id === "close" && isDraft(values?.id)) instance.exec("delete-card", { id: values.id });
	}, []);

	const init = useCallback((instance) => {
		apiRef.current = instance;
		setApi(instance);
		instance.intercept("add-card", (payload) => {
			if (allowStoredAdd.current) return true;
			payload.id = `${DRAFT_PREFIX}${Date.now()}-${++draftNumber.current}`;
			payload.card = {
				label: "", description: "", category: "", priority: "普通", target_date: "",
				column: "收集箱", ...payload.card,
			};
			payload.edit = true;
			return true;
		});
		instance.intercept("update-card", (payload) => handleUpdate(payload, instance));
		instance.intercept("delete-card", (payload) => handleDelete(payload, instance));
	}, [handleUpdate, handleDelete]);

	return <div className="fjib-app">
		{cards === null ? <div className="fjib-loading">正在读取…</div> : <div className="fjib-board-shell"><Willow>
			<div className="fjib-board-inner">
				<div className="fjib-board-canvas">
					<Kanban key={boardKey} cards={cards} columns={COLUMNS} render={RENDER_CONFIG} filters={EMPTY_FILTERS}
						card={CARD_SHAPE} init={init} cardContent={({ card }) => <IdeaCard card={card} />}
						onMoveCard={handleMove} onDeleteCard={({ id }) => {
							if (!isDraft(id)) {
								statusById.current.delete(id);
								apiRef.current?.exec("select-card", { id: null });
								setCards(apiRef.current?.getCards() || []);
							}
						}} />
					{api && <Editor api={api} items={EDITOR_ITEMS} placement="modal" autoSave={false}
						topBar={EDITOR_BAR} bottomBar={false} onAction={handleEditorAction} />}
				</div>
			</div>
		</Willow></div>}
	</div>;
}

export function mountIdeaBoard(element) {
	const root = createRoot(element);
	root.render(<Board />);
	return root;
}
