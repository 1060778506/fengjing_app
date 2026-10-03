import * as X6 from "@antv/x6";
import * as d3Force from "d3-force";
import * as echarts from "echarts";
import * as React from "react";
import * as ReactDOM from "react-dom";
import * as ReactDOMClient from "react-dom/client";
import * as THREE from "three";

// Keep third-party packages behind one application-owned namespace. This avoids
// adding generic globals such as `Graph` or `THREE` to every Frappe Desk page.
window.FengjingLibraries = Object.freeze({
	echarts,
	X6,
	THREE,
	d3Force,
	React,
	ReactDOM,
	ReactDOMClient,
});

