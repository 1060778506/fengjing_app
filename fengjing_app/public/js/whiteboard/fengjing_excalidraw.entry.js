import React from "react";
import { createRoot } from "react-dom/client";
import { Excalidraw } from "@excalidraw/excalidraw";

const mountedRoots = new WeakMap();

function unmount(container) {
	const root = mountedRoots.get(container);
	if (!root) return;
	root.unmount();
	mountedRoots.delete(container);
}

function mount(container, properties = {}) {
	if (!(container instanceof HTMLElement)) {
		throw new TypeError("Excalidraw需要有效的页面容器");
	}

	unmount(container);
	const root = createRoot(container);
	root.render(
		React.createElement(Excalidraw, {
			langCode: "zh-CN",
			...properties,
		})
	);
	mountedRoots.set(container, root);
	return root;
}

const FengjingWhiteboard = Object.freeze({ mount, unmount });

window.FengjingWhiteboard = FengjingWhiteboard;

export { mount, unmount };
export default FengjingWhiteboard;
