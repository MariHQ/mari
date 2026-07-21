// Avatar — initials disc with the four canonical tints. Tint derives from the
// name when not given, so the same person is always the same color.

export function Avatar({ name, initials, tint, size = "md" }: {
  name: string;
  initials?: string;
  tint?: 1 | 2 | 3 | 4;
  size?: "sm" | "md";
}) {
  const init = initials ?? name.split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() ?? "").join("");
  const t = tint ?? ((Array.from(name).reduce((a, c) => a + c.charCodeAt(0), 0) % 4) + 1);
  return (
    <span className={`avatar avatar--tint${t}${size === "sm" ? " avatar--sm" : ""}`} title={name}>
      {init || "?"}
    </span>
  );
}
