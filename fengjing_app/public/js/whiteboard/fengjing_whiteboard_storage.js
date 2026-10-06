import { serializeAsJSON } from "@excalidraw/excalidraw";

const API_ROOT =
	"fengjing_app.fengjing_business.doctype.fengjin_excalidraw_whiteboard_storage.fengjin_excalidraw_whiteboard_storage";
const DEFAULT_TITLE = "未命名白板";
const AUTOSAVE_DELAY = 2000;

function emptyScene() {
	return {
		type: "excalidraw",
		version: 2,
		source: "fengjing_app",
		elements: [],
		appState: {},
		files: {},
	};
}

function parseScene(value) {
	try {
		const scene = typeof value === "string" ? JSON.parse(value) : value;
		if (!scene || typeof scene !== "object" || Array.isArray(scene)) return emptyScene();
		return {
			...scene,
			elements: Array.isArray(scene.elements) ? scene.elements : [],
			appState: scene.appState && typeof scene.appState === "object" ? scene.appState : {},
			files: scene.files && typeof scene.files === "object" ? scene.files : {},
		};
	} catch (_error) {
		return emptyScene();
	}
}

function canonicalScene(value) {
	return JSON.stringify(parseScene(value));
}

function cleanText(value, fallback = "") {
	const cleaned = String(value || "").trim();
	return (cleaned || fallback).slice(0, 140);
}

export class FengjingWhiteboardStorage {
	constructor({ onStatusChange, onDocumentChange, autosaveDelay = AUTOSAVE_DELAY } = {}) {
		this.onStatusChange = onStatusChange || (() => {});
		this.onDocumentChange = onDocumentChange || (() => {});
		this.autosaveDelay = autosaveDelay;
		this.captureEnabled = false;
		this.saveTimer = null;
		this.savingPromise = null;
		this.current = {
			name: null,
			whiteboard_title: DEFAULT_TITLE,
			folder_name: "",
			revision: 0,
		};
		this.savedMetadata = { whiteboard_title: DEFAULT_TITLE, folder_name: "" };
		this.latestSceneJson = canonicalScene(emptyScene());
		this.lastSavedSceneJson = this.latestSceneJson;
		this.dirty = false;
		this.draftKey = `fengjing-whiteboard-draft:${frappe.session?.user || "Guest"}`;
	}

	async call(method, args = {}, options = {}) {
		const response = await frappe.call({
			method: `${API_ROOT}.${method}`,
			args,
			freeze: Boolean(options.freeze),
			freeze_message: options.freezeMessage,
		});
		return response.message;
	}

	get isDirty() {
		return this.dirty;
	}

	get document() {
		return { ...this.current };
	}

	get scene() {
		return parseScene(this.latestSceneJson);
	}

	setCaptureEnabled(enabled) {
		this.captureEnabled = Boolean(enabled);
	}

	restoreDraft() {
		try {
			const raw = localStorage.getItem(this.draftKey);
			if (!raw) return this.scene;
			const draft = JSON.parse(raw);
			if (!draft?.dirty || !draft.scene) return this.scene;
			this.current = {
				name: draft.name || null,
				whiteboard_title: cleanText(draft.whiteboard_title, DEFAULT_TITLE),
				folder_name: cleanText(draft.folder_name),
				revision: Number(draft.revision) || 0,
			};
			this.savedMetadata = {
				whiteboard_title: draft.saved_whiteboard_title || this.current.whiteboard_title,
				folder_name: draft.saved_folder_name || this.current.folder_name,
			};
			this.latestSceneJson = canonicalScene(draft.scene);
			this.lastSavedSceneJson = "";
			this.dirty = true;
			this.notifyDocument();
			this.setStatus("restored", "已恢复浏览器临时草稿");
			return this.scene;
		} catch (_error) {
			this.clearDraft();
			return this.scene;
		}
	}

	captureChange(elements, appState, files) {
		if (!this.captureEnabled) return;
		const nextScene = canonicalScene(serializeAsJSON(elements, appState, files, "database"));
		if (nextScene === this.latestSceneJson) return;
		this.latestSceneJson = nextScene;
		this.refreshDirtyState();
		this.writeDraft();
		this.setStatus("dirty", "未保存");
		this.scheduleSave();
	}

	setMetadata({ whiteboard_title, folder_name } = {}) {
		if (whiteboard_title !== undefined) {
			this.current.whiteboard_title = cleanText(whiteboard_title, DEFAULT_TITLE);
		}
		if (folder_name !== undefined) {
			this.current.folder_name = cleanText(folder_name);
		}
		this.refreshDirtyState();
		this.notifyDocument();
		if (this.dirty) {
			this.writeDraft();
			this.setStatus("dirty", "未保存");
			this.scheduleSave();
		}
	}

	refreshDirtyState() {
		this.dirty =
			this.latestSceneJson !== this.lastSavedSceneJson ||
			this.current.whiteboard_title !== this.savedMetadata.whiteboard_title ||
			this.current.folder_name !== this.savedMetadata.folder_name;
	}

	scheduleSave() {
		clearTimeout(this.saveTimer);
		this.saveTimer = setTimeout(() => {
			this.saveNow({ auto: true }).catch(() => {});
		}, this.autosaveDelay);
	}

	async saveNow({ auto = false, force = false } = {}) {
		clearTimeout(this.saveTimer);
		this.saveTimer = null;
		if (!force && !this.dirty) return this.document;
		if (this.savingPromise) return this.savingPromise;

		const snapshot = {
			scene: this.latestSceneJson,
			whiteboard_title: cleanText(this.current.whiteboard_title, DEFAULT_TITLE),
			folder_name: cleanText(this.current.folder_name),
			name: this.current.name,
			revision: Number(this.current.revision) || 0,
		};
		this.setStatus("saving", auto ? "正在自动保存…" : "正在保存…");

		this.savingPromise = (async () => {
			try {
				let saved;
				if (snapshot.name) {
					saved = await this.call("save_whiteboard", {
						name: snapshot.name,
						whiteboard_data: snapshot.scene,
						expected_revision: snapshot.revision,
						whiteboard_title: snapshot.whiteboard_title,
						folder_name: snapshot.folder_name,
					});
				} else {
					saved = await this.call("create_whiteboard", {
						whiteboard_title: snapshot.whiteboard_title,
						folder_name: snapshot.folder_name,
						whiteboard_data: snapshot.scene,
					});
				}

				this.current = {
					...this.current,
					...saved,
					whiteboard_title: saved.whiteboard_title || snapshot.whiteboard_title,
					folder_name: saved.folder_name || "",
				};
				this.lastSavedSceneJson = snapshot.scene;
				this.savedMetadata = {
					whiteboard_title: snapshot.whiteboard_title,
					folder_name: snapshot.folder_name,
				};
				this.refreshDirtyState();
				this.notifyDocument();
				if (this.dirty) {
					this.writeDraft();
					this.setStatus("dirty", "有新的修改等待保存");
					this.scheduleSave();
				} else {
					this.clearDraft();
					this.setStatus("saved", "已保存");
				}
				return this.document;
			} catch (error) {
				this.dirty = true;
				this.writeDraft();
				this.setStatus("error", "保存失败，已保留浏览器临时草稿");
				throw error;
			} finally {
				this.savingPromise = null;
			}
		})();

		return this.savingPromise;
	}

	async saveAs(whiteboard_title, folder_name = this.current.folder_name) {
		if (this.savingPromise) await this.savingPromise;
		const snapshot = this.latestSceneJson;
		const title = cleanText(whiteboard_title, DEFAULT_TITLE);
		const folder = cleanText(folder_name);
		this.setStatus("saving", "正在另存为…");
		try {
			const saved = await this.call("create_whiteboard", {
				whiteboard_title: title,
				folder_name: folder,
				whiteboard_data: snapshot,
			});
			this.current = { ...saved };
			this.savedMetadata = { whiteboard_title: title, folder_name: folder };
			this.lastSavedSceneJson = snapshot;
			this.refreshDirtyState();
			this.notifyDocument();
			if (this.dirty) {
				this.writeDraft();
				this.scheduleSave();
			} else {
				this.clearDraft();
			}
			this.setStatus(this.dirty ? "dirty" : "saved", this.dirty ? "有新的修改等待保存" : "已保存");
			return this.document;
		} catch (error) {
			this.setStatus("error", "另存为失败");
			throw error;
		}
	}

	async load(name) {
		clearTimeout(this.saveTimer);
		this.saveTimer = null;
		this.setStatus("loading", "正在打开…");
		const doc = await this.call("get_whiteboard", { name }, { freeze: true, freezeMessage: "正在打开白板…" });
		this.current = {
			name: doc.name,
			whiteboard_title: doc.whiteboard_title || DEFAULT_TITLE,
			folder_name: doc.folder_name || "",
			revision: Number(doc.revision) || 0,
			preview_image: doc.preview_image || "",
		};
		this.savedMetadata = {
			whiteboard_title: this.current.whiteboard_title,
			folder_name: this.current.folder_name,
		};
		this.latestSceneJson = canonicalScene(doc.whiteboard_data);
		this.lastSavedSceneJson = this.latestSceneJson;
		this.dirty = false;
		this.clearDraft();
		this.notifyDocument();
		this.setStatus("saved", "已打开");
		return this.scene;
	}

	startNew() {
		clearTimeout(this.saveTimer);
		this.saveTimer = null;
		this.current = {
			name: null,
			whiteboard_title: DEFAULT_TITLE,
			folder_name: "",
			revision: 0,
		};
		this.savedMetadata = { whiteboard_title: DEFAULT_TITLE, folder_name: "" };
		this.latestSceneJson = canonicalScene(emptyScene());
		this.lastSavedSceneJson = this.latestSceneJson;
		this.dirty = false;
		this.clearDraft();
		this.notifyDocument();
		this.setStatus("new", "新白板");
		return this.scene;
	}

	async list(searchText = "") {
		return (await this.call("list_whiteboards", { search_text: searchText, limit: 200 })) || [];
	}

	writeDraft() {
		try {
			localStorage.setItem(
				this.draftKey,
				JSON.stringify({
					dirty: true,
					name: this.current.name,
					whiteboard_title: this.current.whiteboard_title,
					folder_name: this.current.folder_name,
					revision: this.current.revision,
					saved_whiteboard_title: this.savedMetadata.whiteboard_title,
					saved_folder_name: this.savedMetadata.folder_name,
					scene: this.latestSceneJson,
					updated_at: new Date().toISOString(),
				})
			);
		} catch (_error) {
			// 浏览器存储空间不足时，服务器保存仍然可以继续。
		}
	}

	clearDraft() {
		try {
			localStorage.removeItem(this.draftKey);
		} catch (_error) {
			// 浏览器禁用本地存储时无需中断白板。
		}
	}

	setStatus(status, message) {
		this.onStatusChange({ status, message, dirty: this.dirty, document: this.document });
	}

	notifyDocument() {
		this.onDocumentChange(this.document);
	}

	destroy() {
		clearTimeout(this.saveTimer);
		this.saveTimer = null;
	}
}

export { DEFAULT_TITLE, emptyScene, parseScene };
