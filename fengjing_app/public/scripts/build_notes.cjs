const path = require("node:path");

const appRoot = path.resolve(__dirname, "../../..");
const source = path.join(appRoot, "fengjing_app/fengjing_business/page/fengjing_notes/fengjing_notes.jsx");
const output = path.join(appRoot, "fengjing_app/public/notes");

function loadEsbuild() {
	try {
		return require("esbuild");
	} catch {
		return require(path.resolve(appRoot, "../frappe/node_modules/esbuild"));
	}
}

loadEsbuild().build({
	entryPoints: { fengjing_notes: source },
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
}).catch((error) => {
	console.error(error);
	process.exitCode = 1;
});
