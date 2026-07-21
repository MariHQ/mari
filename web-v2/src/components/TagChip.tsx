import * as Ic from "./icons";

const TAG_COLORS: Record<string, string> = {
  canonical: "green",
  draft: "gold",
  stale: "terra",
  deprecated: "red",
  internal: "ink",
  "customer-facing": "blue",
  "needs-review": "violet",
  verified: "blue",
};

const TAG_LABELS: Record<string, string> = {
  canonical: "Canonical",
  draft: "Draft",
  stale: "Stale",
  deprecated: "Deprecated",
  internal: "Internal",
  "customer-facing": "Customer-facing",
  "needs-review": "Needs review",
  verified: "Verified",
};

export function TagChip({
  tag,
  label,
  tone,
  removable = false,
  onRemove,
  selected = false,
  onClick,
}: {
  tag: string;
  label?: string;
  tone?: string;
  removable?: boolean;
  onRemove?: () => void;
  selected?: boolean;
  onClick?: () => void;
}) {
  const content = (
    <>
      <span className="tag-chip__dot" aria-hidden="true" />
      <span>{label ?? TAG_LABELS[tag] ?? tag}</span>
      {removable && (
        <span
          className="tag-chip__remove"
          role="button"
          tabIndex={0}
          aria-label={`Remove ${label ?? tag}`}
          onClick={(event) => { event.stopPropagation(); onRemove?.(); }}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onRemove?.(); }
          }}
        ><Ic.Plus size={11} /></span>
      )}
    </>
  );

  const className = `tag-chip tag-chip--${tone ?? TAG_COLORS[tag] ?? "ink"}${selected ? " selected" : ""}`;
  if (onClick) {
    return <button className={className} type="button" aria-pressed={selected} onClick={onClick}>{content}</button>;
  }
  return <span className={className}>{content}</span>;
}
