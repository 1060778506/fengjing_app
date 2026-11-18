const fs = require("fs");
const path = require("path");

const appRoot = path.resolve(__dirname, "../../..");
const source = path.join(appRoot, "fengjing_app/fengjing_business/page/fengjing_idea_board/fengjing_idea_board.jsx");
const output = path.join(appRoot, "fengjing_app/public/idea_board");

function loadEsbuild() {
	try { return require("esbuild"); }
	catch { return require(path.resolve(appRoot, "../frappe/node_modules/esbuild")); }
}

async function build() {
	fs.rmSync(output, { recursive: true, force: true });
	fs.mkdirSync(output, { recursive: true });
	await loadEsbuild().build({
		entryPoints: { fengjing_idea_board: source },
		outdir: output,
		bundle: true,
		format: "esm",
		platform: "browser",
		target: ["es2020"],
		conditions: ["production", "browser", "import"],
		mainFields: ["browser", "module", "main"],
		define: { "process.env.NODE_ENV": JSON.stringify("production") },
		minify: true,
		logLevel: "info",
	});
}

build().catch((error) => { console.error(error); process.exit(1); });
