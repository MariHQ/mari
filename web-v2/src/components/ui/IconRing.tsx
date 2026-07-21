import { CSSProperties, ReactNode } from "react";
import clsx from "clsx";

export type IconRingTone = "ink" | "green" | "red" | "blue" | "gold";

/** Hand-drawn circle around an icon — card heads, notifications, feed rows. */
export function IconRing({
  tone = "ink",
  size,
  children,
  className,
}: {
  tone?: IconRingTone;
  /** Diameter in px; defaults to the canonical 31. */
  size?: number;
  children: ReactNode;
  className?: string;
}) {
  const style: CSSProperties | undefined = size ? { width: size, height: size } : undefined;
  return (
    <span className={clsx("iconring", tone !== "ink" && `iconring--${tone}`, className)} style={style} aria-hidden="true">
      {children}
    </span>
  );
}
