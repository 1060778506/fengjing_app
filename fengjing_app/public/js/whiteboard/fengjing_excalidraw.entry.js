import React from "react";
import { createRoot } from "react-dom/client";
import { Excalidraw } from "@excalidraw/excalidraw";
import { FengjingWhiteboardStorage } from "./fengjing_whiteboard_storage";

const mountedRoots = new WeakMap();

function unmount(container) {
	const controller = mountedRoots.get(container);
	if (!controller) return;
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
