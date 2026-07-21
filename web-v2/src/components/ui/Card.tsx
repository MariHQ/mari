import { HTMLAttributes, ReactNode } from "react";
import clsx from "clsx";

type CardVariant = "default" | "plain" | "flush";

export type CardProps = Omit<HTMLAttributes<HTMLElement>, "title"> & {
  /** default: 16/18 padding · plain: roomy 20/22 · flush: 0 (tables/lists manage their own inset) */
  variant?: CardVariant;
  /** Card head (canonical .card__head spacing). Any of these switches the head on. */
  title?: ReactNode;
  eyebrow?: string;
  /** Sits inside the title row — typically an <IconRing>. */
  icon?: ReactNode;
  /** Right-aligned slot in the head (buttons, chips, kebab menus). */
  actions?: ReactNode;
  /** Small muted note, right-aligned before actions. */
  hint?: ReactNode;
  children?: ReactNode;
};

/**
 * The one card. Owns its padding — never pad card content from page CSS.
 *
 *   <Card title="Source pulse" icon={<IconRing tone="green"><Ic.Leaf/></IconRing>} hint="last 7 days">…</Card>
 *   <Card variant="flush" title="Members">…table…</Card>
 */
export function Card({
  variant = "default",
  title,
  eyebrow,
  icon,
  actions,
  hint,
  className,
  children,
  ...rest
}: CardProps) {
  const headed = Boolean(title || eyebrow || actions || icon || hint);
  return (
    <section
      className={clsx("card", variant !== "default" && `card--${variant}`, headed && "card--headed", className)}
      {...rest}
    >
      {headed && (
        <header className="card__head">
          {icon}
          <div className="card__heading">
            {eyebrow && <span className="eyebrow">{eyebrow}</span>}
            {title && <h3 className="card__title">{title}</h3>}
          </div>
          <span className="card__spacer" />
          {hint && <span className="card__hint">{hint}</span>}
          {actions}
        </header>
      )}
      <div className="card__body">{children}</div>
    </section>
  );
}
