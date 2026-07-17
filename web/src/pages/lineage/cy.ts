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
      "background-color": "#fdfaf2",
      "border-width": 1.5,
      "border-color": "data(ring)",
      label: "data(label)",
      "font-family": "Lora, Georgia, serif",
      "font-size": 12,
      color: "#33302a",
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
  { selector: "node.warn", style: { "background-color": "#faeee8" } },
  // edited after the as-of date: the node existed then, but isn't this version
  { selector: "node.future", style: { "border-style": "dashed" } },
  {
    selector: "node.focal",
    style: {
      "border-color": "#b04e2c", "border-width": 2.5,
      "underlay-color": "#b04e2c", "underlay-opacity": 0.16, "underlay-padding": 6,
    },
  },
  { selector: "node.sel", style: { "border-color": "#b04e2c", "border-width": 2.5 } },
  {
    selector: "node.pick",
    style: {
      "border-color": "#35549d", "border-width": 2.5,
      "underlay-color": "#35549d", "underlay-opacity": 0.15,
    },
  },
  // provenance: upstream doc edited after the focal's last update
  { selector: "node.changed", style: { "border-color": "#c0392b", "border-width": 2.5, "border-style": "double" } },
  {
    selector: "edge",
    style: {
      "curve-style": "bezier",
      width: 1.8,
      "line-color": "data(color)",
      "target-arrow-shape": "triangle",
      "target-arrow-color": "data(color)",
      "arrow-scale": 0.85,
      "overlay-opacity": 0,
    },
  },
  { selector: "edge.dashed", style: { "line-style": "dashed" } },
  { selector: "edge.sel", style: { width: 3, "z-compound-depth": "top" } },

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
      "background-color": "#f7f2e4",
      color: "#6b6353",
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
      "font-family": "Lora, Georgia, serif",
      color: "#6b6353",
      "text-background-color": "#fdfaf2",
      "text-background-opacity": 0.9,
      "text-background-padding": "2px",
      "text-background-shape": "round-rectangle",
      "text-rotation": "autorotate",
    },
  },

  { selector: ".dim", style: { opacity: 0.2 } },
];
