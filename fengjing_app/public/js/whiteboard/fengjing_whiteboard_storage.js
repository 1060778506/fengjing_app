import { serializeAsJSON } from "@excalidraw/excalidraw";

const API_ROOT =
	"fengjing_app.fengjing_business.doctype.fengjin_excalidraw_whiteboard_storage.fengjin_excalidraw_whiteboard_storage";
const DOCTYPE = "Fengjin Excalidraw whiteboard storage";
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

function parseManifest(value) {
	try {
		const manifest = typeof value === "string" ? JSON.parse(value) : value;
		return manifest && typeof manifest === "object" && !Array.isArray(manifest) ? manifest : {};
	} catch (_error) {
		return {};
	}
}

function canonicalScene(value) {
	return JSON.stringify(parseScene(value));
}

function capturedScene(elements, appState, files) {
	const scene = parseScene(serializeAsJSON(elements, appState, files, "database"));
	return canonicalScene({
		...scene,
		// Excalidraw 的 database 序列化会主动移除 files。这里仅在浏览器内
		// 保留图片二进制，提交服务器前仍由 sceneWithoutFiles() 删除。
		files: files && typeof files === "object" ? files : {},
	});
}

function sceneWithoutFiles(value) {
	return JSON.stringify({ ...parseScene(value), files: {} });
}

function sceneWithoutPendingImages(value) {
	const scene = parseScene(value);
	return JSON.stringify({
		...scene,
		elements: scene.elements.filter(element => element?.type !== "image" || element?.isDeleted),
		files: {},
	});
}

function activeFileIds(value) {
	return new Set(
		parseScene(value).elements
			.filter(element => element?.type === "image" && !element?.isDeleted && element?.fileId)
			.map(element => String(element.fileId))
	);
}

function sceneFileIds(value) {
	return new Set(
		parseScene(value).elements
			.filter(element => element?.type === "image" && element?.fileId)
			.map(element => String(element.fileId))
	);
}

function cleanText(value, fallback = "") {
	const cleaned = String(value || "").trim();
	return (cleaned || fallback).slice(0, 140);
}

function extensionForMimeType(mimeType) {
	return {
		"image/avif": "avif",
		"image/bmp": "bmp",
		"image/gif": "gif",
		"image/ico": "ico",
		"image/jpeg": "jpg",
		"image/jfif": "jfif",
		"image/png": "png",
		"image/svg+xml": "svg",
		"image/webp": "webp",
		"image/x-icon": "ico",
	}[mimeType] || "bin";
}

async function dataURLToBlob(dataURL) {
	const response = await fetch(dataURL);
	if (!response.ok) throw new Error("无法读取白板图片数据");
	return response.blob();
}

function blobToDataURL(blob) {
	return new Promise((resolve, reject) => {
		const reader = new FileReader();
		reader.onload = () => resolve(reader.result);
		reader.onerror = () => reject(reader.error || new Error("无法读取白板图片"));
		reader.readAsDataURL(blob);
	});
}

function responseError(payload, fallback) {
	if (payload?.message && typeof payload.message === "string") return payload.message;
	if (payload?.exception) return payload.exception;
	try {
		const messages = JSON.parse(payload?._server_messages || "[]");
		if (messages.length) {
			const first = JSON.parse(messages[0]);
			return first.message || fallback;
		}
	} catch (_error) {
		// 使用通用错误信息。
	}
	return fallback;
}

async function uploadPrivateImage(boardName, fileId, binaryFile) {
	if (!binaryFile?.dataURL) throw new Error(`图片 ${fileId} 没有可上传的数据`);
	const blob = await dataURLToBlob(binaryFile.dataURL);
	const mimeType = binaryFile.mimeType || blob.type || "application/octet-stream";
	if (!mimeType.startsWith("image/")) throw new Error(`图片 ${fileId} 的文件格式无效`);
	const extension = extensionForMimeType(mimeType);
	const formData = new FormData();
	formData.append("file", new File([blob], `whiteboard-${fileId}.${extension}`, { type: mimeType }));
	formData.append("is_private", "1");
	formData.append("doctype", DOCTYPE);
	formData.append("docname", boardName);
	formData.append("fieldname", "image_manifest");

	const response = await fetch("/api/method/upload_file", {
		method: "POST",
		credentials: "same-origin",
		headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
		body: formData,
	});
	const payload = await response.json().catch(() => ({}));
	if (!response.ok || !payload.message?.name || !payload.message?.file_url) {
		throw new Error(responseError(payload, `图片 ${fileId} 上传失败`));
	}
	return {
		file_document: payload.message.name,
		file_url: payload.message.file_url,
		file_name: payload.message.file_name || `whiteboard-${fileId}.${extension}`,
		mime_type: mimeType,
		size: Number(payload.message.file_size) || blob.size,
		created: Number(binaryFile.created) || Date.now(),
		version: Number(binaryFile.version) || 1,
	};
}

async function hydrateSceneImages(value, manifestValue) {
	const scene = parseScene(value);
	const manifest = parseManifest(manifestValue);
	const files = {};
	let missing = 0;

	// 同时恢复已删除元素的图片，使用户重新打开白板后仍可执行“撤销删除”。
	for (const fileId of sceneFileIds(scene)) {
		const entry = manifest[fileId];
		if (!entry?.file_url) {
			missing += 1;
			continue;
		}
		try {
			const response = await fetch(entry.file_url, { credentials: "same-origin" });
			if (!response.ok) throw new Error(`HTTP ${response.status}`);
			const blob = await response.blob();
			const mimeType = entry.mime_type || blob.type;
			if (!mimeType?.startsWith("image/")) throw new Error("返回内容不是图片");
			files[fileId] = {
				id: fileId,
				dataURL: await blobToDataURL(blob),
				mimeType,
				created: Number(entry.created) || Date.now(),
				lastRetrieved: Date.now(),
				version: Number(entry.version) || 1,
			};
		} catch (error) {
			missing += 1;
			console.warn(`白板图片 ${fileId} 读取失败`, error);
		}
	}

	return { scene: { ...scene, files }, missing };
}

export class FengjingWhiteboardStorage {
	constructor({ onStatusChange, onDocumentChange, autosaveDelay = AUTOSAVE_DELAY } = {}) {
		this.onStatusChange = onStatusChange || (() => {});
		this.onDocumentChange = onDocumentChange || (() => {});
		this.autosaveDelay = autosaveDelay;
		this.captureEnabled = false;
		this.saveTimer = null;
		this.savingPromise = null;
		this.creationAllowed = false;
		this.imageManifest = {};
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

	async restoreDraft() {
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
			this.imageManifest = parseManifest(draft.image_manifest);
			this.savedMetadata = {
				whiteboard_title: draft.saved_whiteboard_title || this.current.whiteboard_title,
				folder_name: draft.saved_folder_name || this.current.folder_name,
			};
			const hydrated = await hydrateSceneImages(draft.scene, this.imageManifest);
			this.latestSceneJson = canonicalScene(hydrated.scene);
			this.lastSavedSceneJson = "";
			this.dirty = true;
			this.notifyDocument();
			this.setStatus(
				hydrated.missing ? "error" : "restored",
				hydrated.missing ? `已恢复草稿，${hydrated.missing}张图片读取失败` : "已恢复浏览器临时草稿"
			);
			return this.scene;
		} catch (_error) {
			this.clearDraft();
			return this.scene;
		}
	}

	captureChange(elements, appState, files) {
		if (!this.captureEnabled) return;
		const nextScene = capturedScene(elements, appState, files);
		if (nextScene === this.latestSceneJson) return;
		this.latestSceneJson = nextScene;
		this.refreshDirtyState();
		this.writeDraft();
		this.setStatus("dirty", "未保存");
		const hasPendingImage = [...activeFileIds(nextScene)].some(fileId => !this.imageManifest[fileId]);
		if (this.current.name || this.creationAllowed) {
			this.scheduleSave(hasPendingImage ? 0 : this.autosaveDelay);
		} else {
			this.setStatus("new", "尚未新建，内容仅保存在当前页面");
		}
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
			if (this.current.name || this.creationAllowed) {
				this.scheduleSave(this.autosaveDelay);
			} else {
				this.setStatus("new", "尚未新建，内容仅保存在当前页面");
			}
		}
	}

	refreshDirtyState() {
		this.dirty =
			this.latestSceneJson !== this.lastSavedSceneJson ||
			this.current.whiteboard_title !== this.savedMetadata.whiteboard_title ||
			this.current.folder_name !== this.savedMetadata.folder_name;
	}

	scheduleSave(delay = this.autosaveDelay) {
		clearTimeout(this.saveTimer);
		this.saveTimer = setTimeout(() => {
			this.saveNow({ auto: true }).catch(() => {});
		}, delay);
	}

	async uploadMissingImages(sceneValue, boardName, initialManifest = this.imageManifest) {
		const scene = parseScene(sceneValue);
		const manifest = { ...parseManifest(initialManifest) };
		for (const fileId of activeFileIds(scene)) {
			if (manifest[fileId]?.file_document) continue;
			const binaryFile = scene.files[fileId];
			manifest[fileId] = await uploadPrivateImage(boardName, fileId, binaryFile);
			this.imageManifest = { ...manifest };
			this.writeDraft();
		}
		return manifest;
	}

	async saveNow({ auto = false, force = false } = {}) {
		clearTimeout(this.saveTimer);
		this.saveTimer = null;
		if (!force && !this.dirty) return this.document;
		if (this.savingPromise) return this.savingPromise;
		if (auto && !this.current.name && !this.creationAllowed) {
			this.setStatus("new", "尚未新建，未写入白板记录");
			return this.document;
		}

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
				const imageIds = activeFileIds(snapshot.scene);
				if (!snapshot.name) {
					this.creationAllowed = true;
					saved = await this.call("create_whiteboard", {
						whiteboard_title: snapshot.whiteboard_title,
						folder_name: snapshot.folder_name,
						whiteboard_data: imageIds.size
							? sceneWithoutPendingImages(snapshot.scene)
							: sceneWithoutFiles(snapshot.scene),
						image_manifest: {},
					});
					snapshot.name = saved.name;
					snapshot.revision = Number(saved.revision) || 1;
					this.current = { ...this.current, ...saved };
					this.imageManifest = {};
					this.notifyDocument();
				}

				if (imageIds.size || this.current.name !== saved?.name) {
					const manifest = await this.uploadMissingImages(snapshot.scene, snapshot.name, this.imageManifest);
					saved = await this.call("save_whiteboard", {
						name: snapshot.name,
						whiteboard_data: sceneWithoutFiles(snapshot.scene),
						image_manifest: manifest,
						expected_revision: snapshot.revision,
						whiteboard_title: snapshot.whiteboard_title,
						folder_name: snapshot.folder_name,
					});
				}

				this.current = {
					...this.current,
					...saved,
					whiteboard_title: saved.whiteboard_title || snapshot.whiteboard_title,
					folder_name: saved.folder_name || "",
				};
				this.imageManifest = parseManifest(saved.image_manifest);
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
		const imageIds = activeFileIds(snapshot);
		this.setStatus("saving", "正在另存为…");
		try {
			this.creationAllowed = true;
			let saved = await this.call("create_whiteboard", {
				whiteboard_title: title,
				folder_name: folder,
				whiteboard_data: imageIds.size ? sceneWithoutPendingImages(snapshot) : sceneWithoutFiles(snapshot),
				image_manifest: {},
			});
			this.current = { ...saved };
			this.imageManifest = {};
			this.notifyDocument();

			if (imageIds.size) {
				const manifest = await this.uploadMissingImages(snapshot, saved.name, {});
				saved = await this.call("save_whiteboard", {
					name: saved.name,
					whiteboard_data: sceneWithoutFiles(snapshot),
					image_manifest: manifest,
					expected_revision: saved.revision,
					whiteboard_title: title,
					folder_name: folder,
				});
			}

			this.current = { ...saved };
			this.imageManifest = parseManifest(saved.image_manifest);
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
		this.creationAllowed = true;
		this.imageManifest = parseManifest(doc.image_manifest);
		this.savedMetadata = {
			whiteboard_title: this.current.whiteboard_title,
			folder_name: this.current.folder_name,
		};
		const hydrated = await hydrateSceneImages(doc.whiteboard_data, this.imageManifest);
		this.latestSceneJson = canonicalScene(hydrated.scene);
		this.lastSavedSceneJson = this.latestSceneJson;
		this.dirty = false;
		this.clearDraft();
		this.notifyDocument();
		this.setStatus(
			hydrated.missing ? "error" : "saved",
			hydrated.missing ? `已打开，${hydrated.missing}张图片读取失败` : "已打开"
		);
		return this.scene;
	}

	async deleteCurrent() {
		if (this.savingPromise) await this.savingPromise;
		if (!this.current.name) throw new Error("当前白板尚未保存，无需删除");
		const deleted = await this.call("delete_whiteboard", { name: this.current.name }, {
			freeze: true,
			freezeMessage: "正在删除白板…",
		});
		const scene = this.startNew({ allowCreate: false });
		this.setStatus("new", "白板已删除");
		return { deleted, document: this.document, scene };
	}

	startNew({ allowCreate = true } = {}) {
		clearTimeout(this.saveTimer);
		this.saveTimer = null;
		this.current = {
			name: null,
			whiteboard_title: DEFAULT_TITLE,
			folder_name: "",
			revision: 0,
		};
		this.imageManifest = {};
		this.creationAllowed = Boolean(allowCreate);
		this.savedMetadata = { whiteboard_title: DEFAULT_TITLE, folder_name: "" };
		this.latestSceneJson = canonicalScene(emptyScene());
		this.lastSavedSceneJson = this.latestSceneJson;
		this.dirty = false;
		this.clearDraft();
		this.notifyDocument();
		this.setStatus("new", "新白板");
		return this.scene;
	}

	async createNew() {
		if (this.savingPromise) await this.savingPromise;
		const scene = this.startNew({ allowCreate: true });
		this.setStatus("saving", "正在新建白板…");
		try {
			const saved = await this.call("create_whiteboard", {
				whiteboard_title: this.current.whiteboard_title,
				folder_name: this.current.folder_name,
				whiteboard_data: sceneWithoutFiles(scene),
				image_manifest: {},
			});
			this.current = { ...this.current, ...saved };
			this.imageManifest = parseManifest(saved.image_manifest);
			this.savedMetadata = {
				whiteboard_title: this.current.whiteboard_title,
				folder_name: this.current.folder_name,
			};
			this.lastSavedSceneJson = this.latestSceneJson;
			this.dirty = false;
			this.clearDraft();
			this.notifyDocument();
			this.setStatus("saved", "新白板已创建");
			return { document: this.document, scene: this.scene };
		} catch (error) {
			this.creationAllowed = false;
			this.setStatus("error", "新建白板失败");
			throw error;
		}
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
					scene: sceneWithoutFiles(this.latestSceneJson),
					image_manifest: this.imageManifest,
					updated_at: new Date().toISOString(),
				})
			);
		} catch (_error) {
			// 浏览器禁用本地存储时，服务器保存仍然可以继续。
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
