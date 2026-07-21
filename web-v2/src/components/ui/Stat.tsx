import { MouseEventHandler, ReactNode } from "react";
import clsx from "clsx";

export type StatTone = "green" | "red" | "blue" | "gold";

/**
 * The one stat card — big display number + label + sub note.
 * Pass onClick for the click-to-filter affordance (Answers-style stat strips).
 */
export function Stat({
  value,
  label,
  sub,
  tone,
  swatch,
  ring,
  onClick,
  className,
}: {
  value: ReactNode;
  label: ReactNode;
  sub?: ReactNode;
  /** Colors the sub note. */
  tone?: StatTone;
  /** Optional left texture swatch (e.g. <TexturePlaceholder/>). */
  swatch?: ReactNode;
  /** Optional top-right icon ring. */
  ring?: ReactNode;
  onClick?: MouseEventHandler<HTMLButtonElement>;
  className?: string;
}) {
  const body = (
    <>
      {swatch && <span className="stat__swatch">{swatch}</span>}
      <span className="stat__body">
        <span className="stat__num">{value}</span>
        <span className="stat__label">{label}</span>
        {sub && <span className={clsx("stat__sub", tone && `stat__sub--${tone}`)}>{sub}</span>}
      </span>
      {ring && <span className="stat__ring">{ring}</span>}
    </>
  );
  if (onClick) {
    return (
      <button type="button" className={clsx("card", "stat", "stat--click", className)} onClick={onClick}>
        {body}
      </button>
    );
  }
  return <div className={clsx("card", "stat", className)}>{body}</div>;
}
