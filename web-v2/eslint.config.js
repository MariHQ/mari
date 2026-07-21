import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  {
    files: ["src/**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    plugins: { "react-hooks": reactHooks, "react-refresh": reactRefresh },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": "off",
      "@typescript-eslint/no-explicit-any": "off", // GraphQL payloads land as any at the seam
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      // the design-system mandate: compose ui primitives, don't restyle inline.
      // (advisory during migration; tightened to "error" once pages are clean)
      "no-restricted-syntax": [
        "warn",
        {
          selector: "JSXAttribute[name.name='style'] ObjectExpression[properties.length>4]",
          message: "Large inline style object — use a ui/ primitive or a class in the design-system CSS.",
        },
      ],
    },
  },
);
