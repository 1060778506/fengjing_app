const fs = require("fs");
const path = require("path");

const appRoot = path.resolve(__dirname, "../../..");
const entryPoint = path.join(
	appRoot,
	"fengjing_app/public/js/calendar/fengjing_calendar.entry.js"
);
const outputDirectory = path.join(
	appRoot,
	"fengjing_app/public/calendar/fullcalendar"
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
				"找不到 esbuild。请在 Frappe Bench 环境中执行 bench build。",
				{ cause: error }
			);
		}
	}
}

async function build() {
	fs.rmSync(outputDirectory, { recursive: true, force: true });
	fs.mkdirSync(outputDirectory, { recursive: true });

	await loadEsbuild().build({
		entryPoints: [entryPoint],
		outfile: path.join(outputDirectory, "fengjing_calendar.js"),
		bundle: true,
		format: "iife",
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
