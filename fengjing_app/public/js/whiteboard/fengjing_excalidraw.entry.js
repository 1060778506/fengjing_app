import React from "react";
import { createRoot } from "react-dom/client";
import {
	CaptureUpdateAction,
	Excalidraw,
	convertToExcalidrawElements,
	getDataURL,
	viewportCoordsToSceneCoords,
} from "@excalidraw/excalidraw";
import { FengjingWhiteboardStorage } from "./fengjing_whiteboard_storage";

const mountedRoots = new WeakMap();
const MAX_DROPPED_IMAGE_WIDTH = 520;
const MAX_DROPPED_IMAGE_HEIGHT = 420;
const DROPPED_IMAGE_GAP = 32;

function createFileId() {
	const randomPart = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
	return `fengjing-${randomPart.replace(/[^A-Za-z0-9_-]/g, "")}`;
}

function loadImageSize(dataURL) {
	return new Promise((resolve, reject) => {
		const image = new Image();
		image.onload = () => resolve({ width: image.naturalWidth || 1, height: image.naturalHeight || 1 });
		image.onerror = () => reject(new Error("无法读取图片尺寸"));
		image.src = dataURL;
	});
}

function fitImageSize(width, height) {
	const scale = Math.min(MAX_DROPPED_IMAGE_WIDTH / width, MAX_DROPPED_IMAGE_HEIGHT / height, 1);
	return {
		width: Math.max(1, Math.round(width * scale)),
		height: Math.max(1, Math.round(height * scale)),
	};
}

async function insertDroppedImages(event, controller, imageFiles) {
	const api = controller.api;
	const appState = api.getAppState();
	const origin = viewportCoordsToSceneCoords(
		{ clientX: event.clientX, clientY: event.clientY },
		appState
	);
	const prepared = [];

	for (const file of imageFiles) {
		const dataURL = await getDataURL(file);
		const naturalSize = await loadImageSize(dataURL);
		prepared.push({
			file,
			dataURL,
			id: createFileId(),
			...fitImageSize(naturalSize.width, naturalSize.height),
		});
	}

	const columnCount = Math.max(1, Math.ceil(Math.sqrt(prepared.length)));
	const cellWidth = Math.max(...prepared.map(image => image.width)) + DROPPED_IMAGE_GAP;
	const cellHeight = Math.max(...prepared.map(image => image.height)) + DROPPED_IMAGE_GAP;
	const skeletons = prepared.map((image, index) => ({
		type: "image",
		x: origin.x + (index % columnCount) * cellWidth,
		y: origin.y + Math.floor(index / columnCount) * cellHeight,
		width: image.width,
		height: image.height,
		fileId: image.id,
		status: "saved",
		scale: [1, 1],
		crop: null,
	}));
	const newElements = convertToExcalidrawElements(skeletons, { regenerateIds: true });

	api.addFiles(prepared.map(image => ({
		id: image.id,
		dataURL: image.dataURL,
		mimeType: image.file.type,
		created: Date.now(),
		lastRetrieved: Date.now(),
		version: 1,
	})));
	api.updateScene({
		elements: [...api.getSceneElementsIncludingDeleted(), ...newElements],
		appState: {
			selectedElementIds: Object.fromEntries(newElements.map(element => [element.id, true])),
		},
		captureUpdate: CaptureUpdateAction.IMMEDIATELY,
	});
	api.setToast({ message: `已插入${prepared.length}张图片`, duration: 3000 });
}

function installMultiImageDrop(container, controller) {
	const onDrop = event => {
		const droppedFiles = Array.from(event.dataTransfer?.files || []);
		const imageFiles = droppedFiles.filter(file => file.type?.startsWith("image/"));
		if (!controller.api || droppedFiles.length < 2 || imageFiles.length !== droppedFiles.length) return;

		event.preventDefault();
		event.stopPropagation();
		event.stopImmediatePropagation();
		insertDroppedImages(event, controller, imageFiles).catch(error => {
			console.error("多张白板图片插入失败", error);
			controller.api?.setToast({ message: error?.message || "多张图片插入失败", duration: 5000 });
		});
	};
	container.addEventListener("drop", onDrop, true);
	return () => container.removeEventListener("drop", onDrop, true);
}

function unmount(container) {
	const controller = mountedRoots.get(container);
	if (!controller) return;
	controller.removeMultiImageDrop?.();
	controller.root.unmount();
	mountedRoots.delete(container);
}

function mount(container, properties = {}) {
	if (!(container instanceof HTMLElement)) {
		throw new TypeError("Excalidraw需要有效的页面容器");
	}

	unmount(container);
	const controller = {
		api: null,
		properties: { ...properties },
		root: null,
	};
	controller.removeMultiImageDrop = installMultiImageDrop(container, controller);
	const apiCallback = properties.excalidrawAPI;
	const root = createRoot(container);
	controller.root = root;
	root.render(
		React.createElement(Excalidraw, {
			langCode: "zh-CN",
			...properties,
			excalidrawAPI: (api) => {
				controller.api = api;
				if (typeof apiCallback === "function") apiCallback(api);
			},
		})
	);
	mountedRoots.set(container, controller);
	return controller;
}

function replaceScene(container, scene) {
	const controller = mountedRoots.get(container);
	if (!controller) throw new Error("白板尚未挂载");
	return mount(container, {
		...controller.properties,
		initialData: scene,
	});
}

function getApi(container) {
	return mountedRoots.get(container)?.api || null;
}

const FengjingWhiteboard = Object.freeze({ getApi, mount, replaceScene, unmount });

window.FengjingWhiteboard = FengjingWhiteboard;

export { FengjingWhiteboardStorage, getApi, mount, replaceScene, unmount };
export default FengjingWhiteboard;
