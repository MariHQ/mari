import { ReactNode } from "react";
import { Link } from "react-router-dom";
import * as Ic from "../icons";

export type PageHeaderProps = {
  title: string;
  eyebrow?: string;
  description?: string;
  /** Unused — kept so existing call sites that still pass a custom icon don't break; renders nothing. */
  icon?: ReactNode;
  /** Right-aligned slot (buttons, chips). */
  actions?: ReactNode;
  /** "← Back" affordance rendered above the title. */
  backLink?: { to: string; label: string };
};

/**
 * The one page header — kicker (small square + uppercase mono label) above a
 * bold title and muted subtitle, matching the saas console's Page() header.
 */
export function PageHeader({ title, eyebrow, description, actions, backLink }: PageHeaderProps) {
  return (
    <header className="pagehead">
      <div className="pagehead__copy">
        {backLink && (
          <Link className="pagehead__back" to={backLink.to}>
            <Ic.ChevL size={13} />
            {backLink.label}
          </Link>
        )}
        {eyebrow && (
          <span className="pagehead__eyebrow">
            <span className="pagehead__eyebrow-dot" aria-hidden="true" />
            {eyebrow}
          </span>
        )}
        <h2>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="pagehead__actions">{actions}</div>}
    </header>
  );
}
