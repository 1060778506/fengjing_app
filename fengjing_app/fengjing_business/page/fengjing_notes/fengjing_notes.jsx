import React, { useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { zh } from "@blocknote/core/locales";
import { BlockNoteView } from "@blocknote/mantine";
import { useCreateBlockNote } from "@blocknote/react";
import "@blocknote/mantine/style.css";
import "./notes.css";

const doctype = "Fengjing Note";

async function request(method, args) {
	const response = await frappe.call({ method: `frappe.client.${method}`, args });
	return response.message;
}

async function uploadAttachment(note, file) {
	const data = new FormData();
	data.append("file", file);
	data.append("doctype", doctype);
	data.append("docname", note);
	data.append("is_private", "1");
	const response = await fetch("/api/method/upload_file", {
		method: "POST",
		credentials: "same-origin",
		headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
		body: data,
	});
	if (!response.ok) throw new Error("附件上传失败");
	const { message } = await response.json();
	if (!message?.file_url) throw new Error("附件上传失败");
	return message.file_url;
}

function NoteEditor({ note, editorRef, busy, onSaved, onDelete }) {
	const [title, setTitle] = useState(note.title);
	const [status, setStatus] = useState("已保存");
	const [uploading, setUploading] = useState(0);
	const [uploadError, setUploadError] = useState(false);
	const [failed, setFailed] = useState(false);
	const version = useRef(note.modified);
	const timer = useRef(null);
	const inFlight = useRef(null);
	const activeUploads = useRef(0);
	const current = useRef(null);
	const saved = useRef(null);

	const blocks = JSON.parse(note.blocks || "[]");
	const editor = useCreateBlockNote({
		initialContent: blocks.length ? blocks : undefined,
		dictionary: zh,
		uploadFile: async (file) => {
			activeUploads.current += 1;
			setUploading(activeUploads.current);
			setUploadError(false);
			try {
				return await uploadAttachment(note.name, file);
			} catch (error) {
				setUploadError(true);
				throw error;
			} finally {
				activeUploads.current -= 1;
				setUploading(activeUploads.current);
				if (!activeUploads.current && saved.current !== current.current) {
					clearTimeout(timer.current);
					timer.current = setTimeout(flush, 650);
				}
			}
		},
	});
	if (current.current === null) {
		current.current = { title: note.title, blocks: JSON.stringify(editor.document) };
		saved.current = current.current;
	}

	const flush = useCallback(() => {
		clearTimeout(timer.current);
		if (activeUploads.current) return Promise.resolve(false);
		if (inFlight.current) return inFlight.current;
		if (saved.current === current.current) return Promise.resolve(true);

		setStatus("正在保存…");
		setFailed(false);
		inFlight.current = (async () => {
			while (saved.current !== current.current) {
				const draft = current.current;
				const updated = await request("save", {
					doc: {
						...note,
						modified: version.current,
						title: draft.title.trim() || "未命名笔记",
						blocks: draft.blocks,
					},
				});
				version.current = updated.modified;
				saved.current = draft;
				onSaved(updated);
			}
			setStatus("已保存");
			return true;
		})().catch(() => {
			setStatus("保存失败");
			setFailed(true);
			return false;
		}).finally(() => {
			inFlight.current = null;
		});
		return inFlight.current;
	}, [note, onSaved]);

	useImperativeHandle(editorRef, () => ({ flush }), [flush]);

	useEffect(() => {
		const warn = (event) => {
			if (saved.current === current.current && !activeUploads.current) return;
			event.preventDefault();
			event.returnValue = "";
		};
		window.addEventListener("beforeunload", warn);
		return () => {
			clearTimeout(timer.current);
			window.removeEventListener("beforeunload", warn);
		};
	}, []);

	function schedule(field, value) {
		if (current.current[field] === value) return;
		current.current = { ...current.current, [field]: value };
		setStatus("待保存");
		clearTimeout(timer.current);
		timer.current = setTimeout(flush, 650);
	}

	return (
		<section className="fj-notes-paper" aria-label="笔记正文">
			<div className="fj-notes-actions">
				<span role="status">{uploading ? "正在上传附件…" : uploadError ? "附件上传失败，可重试" : status}</span>
				{failed && <button type="button" onClick={flush}>重试保存</button>}
				<button type="button" className="fj-notes-delete" onClick={onDelete} disabled={busy || Boolean(uploading)}>
					删除
				</button>
			</div>
			<input
				className="fj-notes-title"
				aria-label="笔记标题"
				placeholder="未命名笔记"
				value={title}
				maxLength={140}
				disabled={busy}
				onChange={(event) => {
					setTitle(event.target.value);
					schedule("title", event.target.value);
				}}
			/>
			<BlockNoteView
				editor={editor}
				editable={!busy}
				className="fj-notes-editor"
				contentEditableProps={{ className: "fj-notes-editable" }}
				onChange={() => schedule("blocks", JSON.stringify(editor.document))}
			/>
		</section>
	);
}

function Notes() {
	const [notes, setNotes] = useState([]);
	const [active, setActive] = useState(null);
	const [search, setSearch] = useState("");
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState("");
	const editorRef = useRef(null);
	const operation = useRef(false);

	const remember = useCallback(({ name, title, modified }) => {
		setNotes((list) => [{ name, title, modified }, ...list.filter((item) => item.name !== name)]);
	}, []);

	const load = useCallback(async () => {
		setLoading(true);
		setError("");
		try {
			const rows = await request("get_list", {
				doctype,
				fields: ["name", "title", "modified"],
				filters: { owner: frappe.session.user },
				order_by: "modified desc",
				limit_page_length: 0,
			});
			setNotes(rows);
			if (rows.length) {
				setActive(await request("get", { doctype, name: rows[0].name }));
			}
		} catch {
			setError("读取笔记失败，请重试。");
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => { void load(); }, [load]);

	async function perform(action) {
		if (operation.current) return;
		operation.current = true;
		setLoading(true);
		setError("");
		try {
			if (editorRef.current && !(await editorRef.current.flush())) {
				setError("请等待附件上传完成，或重试保存后再继续。");
				return;
			}
			await action();
		} catch {
			setError("操作失败，请重试。");
		} finally {
			operation.current = false;
			setLoading(false);
		}
	}

	function createNote() {
		perform(async () => {
			const note = await request("insert", { doc: { doctype, title: "未命名笔记" } });
			remember(note);
			setSearch("");
			setActive(note);
		});
	}

	function deleteNote() {
		const name = active.name;
		frappe.confirm("删除这条笔记及其附件？", () => {
			perform(async () => {
				await request("delete", { doctype, name });
				setNotes((list) => list.filter((note) => note.name !== name));
				setActive(null);
			});
		});
	}

	const query = search.trim().toLowerCase();
	const visible = notes.filter((note) => note.title.toLowerCase().includes(query));

	return (
		<div className="fj-notes">
			<aside className="fj-notes-sidebar" aria-label="笔记列表">
				<button type="button" className="fj-notes-new" disabled={loading} onClick={createNote}>＋ 新建笔记</button>
				<input type="search" className="fj-notes-search" aria-label="搜索笔记标题"
					placeholder="搜索笔记标题" value={search} onChange={(event) => setSearch(event.target.value)} />
				<ul className="fj-notes-list">
					{visible.map((note) => (
						<li key={note.name}>
							<button type="button" disabled={loading} aria-current={active?.name === note.name ? "page" : undefined}
								onClick={() => {
									if (active?.name !== note.name) {
										perform(async () => setActive(await request("get", { doctype, name: note.name })));
									}
								}}>
								<span>{note.title}</span>
								<time dateTime={note.modified?.replace(" ", "T")}>{note.modified?.slice(0, 16)}</time>
							</button>
						</li>
					))}
				</ul>
				{!loading && !visible.length && <p className="fj-notes-empty-list">{query ? "没有匹配的笔记" : "暂无笔记"}</p>}
			</aside>
			<div className="fj-notes-main">
				{error && <div className="fj-notes-error" role="alert">
					{error} {!active && <button type="button" onClick={load}>重新加载</button>}
				</div>}
				{active ? (
					<NoteEditor key={active.name} note={active} editorRef={editorRef} busy={loading}
						onSaved={remember} onDelete={deleteNote} />
				) : (
					<div className="fj-notes-empty">
						{loading ? "正在读取笔记…" : notes.length ? "请从左侧选择一条笔记" :
							<button type="button" className="fj-notes-new" onClick={createNote}>新建第一条笔记</button>}
					</div>
				)}
			</div>
		</div>
	);
}

export function mountNotes(element) {
	createRoot(element).render(<Notes />);
}
