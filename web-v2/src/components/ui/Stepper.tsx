/**
 * The one stepper — the 33px-dot .wizard-steps style. The auth-steps and
 * welcome-local variants die in migration.
 */
export function Stepper({
  labels,
  current,
  onSelect,
  ariaLabel = "Progress",
}: {
  labels: string[];
  current: number;
  /** Omit to render a read-only stepper. */
  onSelect?: (step: number) => void;
  ariaLabel?: string;
}) {
  return (
    <ol className="wizard-steps" aria-label={ariaLabel} style={{ gridTemplateColumns: `repeat(${labels.length}, 1fr)` }}>
      {labels.map((label, index) => (
        <li key={label} className={index === current ? "active" : index < current ? "complete" : ""}>
          <button
            type="button"
            onClick={onSelect ? () => onSelect(index) : undefined}
            style={onSelect ? undefined : { cursor: "default" }}
            aria-current={index === current ? "step" : undefined}
          >
            <span className="wizard-steps__number">{index < current ? "✓" : index + 1}</span>
            <span>{label}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}
