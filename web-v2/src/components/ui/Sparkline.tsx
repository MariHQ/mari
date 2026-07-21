// Hand-drawn sparkline for small trend readouts (answers served/wk, source
// pulse). One implementation; tone follows the chip palette.

export function Sparkline({ values, width = 92, height = 26, tone = "green" }: {
  values: number[];
  width?: number;
  height?: number;
  tone?: "green" | "blue" | "gold" | "terra" | "ink";
}) {
  if (values.length < 2) return null;
  const max = Math.max(...values, 1);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * (width - 4) + 2},${height - 3 - (v / max) * (height - 8)}`);
  const colors = { green: "var(--green)", blue: "var(--blue)", gold: "var(--gold)", terra: "var(--terra)", ink: "var(--ink-soft)" };
  return (
    <svg width={width} height={height} aria-hidden style={{ display: "block" }}>
      <polyline points={pts.join(" ")} fill="none" stroke={colors[tone]} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
