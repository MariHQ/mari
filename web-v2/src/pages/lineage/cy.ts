// Cytoscape setup for the lineage page: dagre registration + the tuned
// editorial-notebook stylesheet. DO NOT tweak layout/style values here —
// they were verified against the live graph.

import cytoscape from "cytoscape";
import dagre from "cytoscape-dagre";

cytoscape.use(dagre);

// ————— cytoscape stylesheet (editorial-notebook look) —————

export const CY_STYLE: any[] = [
  {
    selector: "node",
    style: {
      shape: "round-rectangle",
      "background-color": "#ffffff",
      "border-width": 1.5,
      "border-color": "data(ring)",
      label: "data(label)",
      "font-family": "Inter, sans-serif",
      "font-size": 12,
      color: "#10263b",
      "text-wrap": "wrap",
      "text-max-width": "130",
      "text-valign": "center",
      "text-halign": "center",
      width: "label",
      height: "label",
      padding: "11px",
      "underlay-color": "data(uc)",
      "underlay-opacity": 0.14,
      "underlay-padding": 3,
      "underlay-shape": "round-rectangle",
      "overlay-opacity": 0,
    },
  },
  { selector: "node.warn", style: { "background-color": "#f7f2ed" } },
  // edited after the as-of date: the node existed then, but isn't this version
  { selector: "node.future", style: { "border-style": "dashed" } },
  {
    selector: "node.focal",
    style: {
      "border-color": "#1c3f60", "border-width": 2.5,
      "underlay-color": "#1c3f60", "underlay-opacity": 0.16, "underlay-padding": 6,
    },
  },
  { selector: "node.sel", style: { "border-color": "#1c3f60", "border-width": 2.5 } },
  {
    selector: "node.pick",
    style: {
      "border-color": "#35549d", "border-width": 2.5,
      "underlay-color": "#35549d", "underlay-opacity": 0.15,
    },
  },
  // provenance: upstream doc edited after the focal's last update
  { selector: "node.changed", style: { "border-color": "#b23a1e", "border-width": 2.5, "border-style": "double" } },
  {
    selector: "edge",
    style: {
      "curve-style": "bezier",
      width: 1.3,
      opacity: 0.32,
      "line-color": "data(color)",
      "target-arrow-shape": "triangle",
      "target-arrow-color": "data(color)",
      "arrow-scale": 0.7,
      "overlay-opacity": 0,
    },
  },
  { selector: "edge.dashed", style: { "line-style": "dashed" } },
  // selected/traced edges punch back through the faded field at full
  // strength and render above it, so the one relation you care about still
  // reads clearly against the density
  { selector: "edge.sel", style: { width: 3, opacity: 1, "arrow-scale": 1, "z-compound-depth": "top" } },

  // ————— roll-up additions (LINEAGE-ROLLUP-CONTRACT §Web half) —————
  // macro node: one card standing in for a whole gh:<repo>:<kind> bucket.
  // Doubled border + a heavier underlay read as a stack of cards; the count
  // lives in the label ("89 commits" over the repo sub-label).
  {
    selector: "node.macro",
    style: {
      "border-style": "double",
      "border-width": 4,
      "font-size": 12.5,
      "font-weight": 600,
      "text-max-width": "150",
      padding: "13px",
      "underlay-opacity": 0.22,
      "underlay-padding": 7,
    },
  },
  // "+N more" paging stub inside an expanded group
  {
    selector: "node.stub",
    style: {
      "border-style": "dashed",
      "background-color": "#f0f2f5",
      color: "#6f7c89",
      "font-size": 11,
      padding: "8px",
    },
  },
  // aggregated (re-pointed) edges carry a "references ×12" count label
  {
    selector: "edge[glabel]",
    style: {
      label: "data(glabel)",
      "font-size": 9,
      "font-family": "JetBrains Mono, monospace",
      color: "#6f7c89",
      "text-background-color": "#ffffff",
      "text-background-opacity": 0.9,
      "text-background-padding": "2px",
      "text-background-shape": "round-rectangle",
      "text-rotation": "autorotate",
    },
  },

  { selector: ".dim", style: { opacity: 0.2 } },
];
