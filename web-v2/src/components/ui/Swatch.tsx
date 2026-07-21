import { ReactNode } from "react";
import clsx from "clsx";

/**
 * A small color swatch — token displays and harvested brand-color candidates.
 * Pass onClick to make it a pickable button (Branding import evidence strip).
 */
export function Swatch({
  color,
  label,
  selected = false,
  onClick,
  title,
  className,
}: {
  /** Any CSS color (hex or var()). */
  color: string;
  label?: ReactNode;
  selected?: boolean;
  onClick?: () => void;
  title?: string;
  className?: string;
}) {
  const cls = clsx("ds-swatch", selected && "selected", className);
  const body = (
    <>
      <span className="ds-swatch__chip" style={{ background: color }} aria-hidden="true" />
      {label && <span className="ds-swatch__label">{label}</span>}
    </>
  );
  if (onClick) {
    return (
      <button type="button" className={cls} onClick={onClick} title={title ?? String(color)} aria-pressed={selected}>
        {body}
      </button>
    );
  }
  return <span className={cls} title={title ?? String(color)}>{body}</span>;
}
