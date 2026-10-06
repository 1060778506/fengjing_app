const fs = require("fs");
const path = require("path");

const appRoot = path.resolve(__dirname, "..");
const entryPoint = path.join(
	appRoot,
	"fengjing_app/public/js/whiteboard/fengjing_excalidraw.entry.js"
);
const outputDirectory = path.join(
	appRoot,
	"fengjing_app/public/whiteboard/excalidraw"
);

function loadEsbuild() {
	try {
		return require("esbuild");
	} catch (error) {
		const frappeEsbuild = path.resolve(appRoot, "../frappe/node_modules/esbuild");
		try {
			return require(frappeEsbuild);
		} catch (_frappeError) {
			throw new Error(
				"找不到 esbuild。请在 Frappe Bench 环境中执行 bench build，或为 fengjing_app 安装 esbuild。",
				{ cause: error }
			);
		}
	}
}

async function build() {
	fs.rmSync(outputDirectory, { recursive: true, force: true });
	fs.mkdirSync(outputDirectory, { recursive: true });

	await loadEsbuild().build({
		entryPoints: { fengjing_excalidraw: entryPoint },
		outdir: outputDirectory,
		entryNames: "[name]",
		chunkNames: "chunks/[name]-[hash]",
		assetNames: "assets/[name]-[hash]",
		bundle: true,
		splitting: true,
		format: "esm",
		platform: "browser",
		target: ["es2020"],
		conditions: ["production", "browser", "import"],
		mainFields: ["browser", "module", "main"],
		define: {
			"process.env.NODE_ENV": JSON.stringify("production"),
		},
		minify: true,
		sourcemap: false,
		logLevel: "info",
	});
}

build().catch((error) => {
	console.error(error);
	process.exit(1);
});
