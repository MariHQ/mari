import { ButtonHTMLAttributes, MouseEventHandler, ReactNode } from "react";
import clsx from "clsx";
import * as Ic from "../icons";

export type ChipTone = "neutral" | "green" | "blue" | "gold" | "terra" | "red" | "ink" | "violet" | "faint";

export type ChipProps = {
  tone?: ChipTone;
  /** Leading status dot in the tone color. */
  dot?: boolean;
  /** Pulsing dot — running/live states. */
  pulse?: boolean;
  /** Small uppercase treatment (severity, badges). */
  caps?: boolean;
  icon?: ReactNode;
  /** Renders a <button> with hover affordance. */
  onClick?: MouseEventHandler<HTMLButtonElement>;
  selected?: boolean;
  /** Shows an × affordance (tag filters). */
  onRemove?: () => void;
  removeLabel?: string;
  className?: string;
  title?: string;
  children: ReactNode;
} & Pick<ButtonHTMLAttributes<HTMLButtonElement>, "disabled" | "aria-pressed">;

/**
 * The one chip. Replaces .pill, .tag-chip, .dec-sev, .rule-severity, answers
 * .chip and the facts inline chips. Prefer the semantic wrappers below.
 */
export function Chip({
  tone = "neutral",
  dot = false,
  pulse = false,
  caps = false,
  icon,
  onClick,
  selected = false,
  onRemove,
  removeLabel = "Remove",
  className,
  children,
  ...rest
}: ChipProps) {
  const cls = clsx(
    "ds-chip",
    tone !== "neutral" && `ds-chip--${tone}`,
    caps && "ds-chip--caps",
    pulse && "ds-chip--pulse",
    selected && "selected",
    className,
  );
  const body = (
    <>
      {(dot || pulse) && <span className="ds-chip__dot" aria-hidden="true" />}
      {icon}
      {children}
      {onRemove && (
        <button type="button" className="ds-chip__remove" aria-label={removeLabel} onClick={(e) => { e.stopPropagation(); onRemove(); }}>
          <Ic.Plus size={11} />
        </button>
      )}
    </>
  );
  if (onClick) {
    return (
      <button type="button" className={cls} onClick={onClick} {...rest}>
        {body}
      </button>
    );
  }
  return <span className={cls} title={rest.title}>{body}</span>;
}

/* ————— semantic wrappers ————— */

export type ChipStatus = "verified" | "canonical" | "approved" | "stale" | "draft" | "retired" | "running" | "failed" | "needs-review";

const STATUS: Record<ChipStatus, { tone: ChipTone; label: string; dot?: boolean; pulse?: boolean; icon?: ReactNode }> = {
  verified: { tone: "blue", label: "Verified", icon: <Ic.ShieldCheck size={12} /> },
  canonical: { tone: "green", label: "Canonical", icon: <Ic.CheckCircle size={12} /> },
  approved: { tone: "green", label: "Approved", icon: <Ic.Check size={12} /> },
  stale: { tone: "terra", label: "Stale", dot: true },
  draft: { tone: "ink", label: "Draft", dot: true },
  retired: { tone: "faint", label: "Retired", dot: true },
  running: { tone: "green", label: "Running", pulse: true },
  failed: { tone: "red", label: "Failed", dot: true },
  "needs-review": { tone: "gold", label: "Needs review", dot: true },
};

/** <StatusChip status="verified" /> — knowledge/doc/run lifecycle states. */
export function StatusChip({ status, label, className }: { status: ChipStatus; label?: string; className?: string }) {
  const s = STATUS[status];
  return (
    <Chip tone={s.tone} dot={s.dot} pulse={s.pulse} icon={s.icon} className={className}>
      {label ?? s.label}
    </Chip>
  );
}

export type ChipSeverity = "high" | "med" | "low";

const SEVERITY: Record<ChipSeverity, { tone: ChipTone; label: string }> = {
  high: { tone: "red", label: "High" },
  med: { tone: "gold", label: "Med" },
  low: { tone: "ink", label: "Low" },
};

/** <SeverityChip level="high" /> — uppercase severity badge. */
export function SeverityChip({ level, label, className }: { level: ChipSeverity; label?: string; className?: string }) {
  const s = SEVERITY[level];
  return <Chip tone={s.tone} caps className={className}>{label ?? s.label}</Chip>;
}

/** Small numeric bubble — replaces the countbubble offset hacks. */
export function CountChip({ value, tone = "neutral", className }: { value: number | string; tone?: ChipTone; className?: string }) {
  return <Chip tone={tone} className={clsx("ds-chip--count", className)}>{value}</Chip>;
}
