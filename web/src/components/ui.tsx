/* Back-compat shim. The design system now lives in ./ui/ — new code should
 * import from there directly; this file keeps old `components/ui` imports
 * (PageHeader, Tabs, Field, Stepper) working until migration lands. */
export * from "./ui/index";
