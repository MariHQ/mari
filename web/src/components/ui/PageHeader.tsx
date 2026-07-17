import { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Sprig } from "../art";
import * as Ic from "../icons";

export type PageHeaderProps = {
  title: string;
  eyebrow?: string;
  description?: string;
  /** Replaces the default sprig mark. */
  icon?: ReactNode;
  /** Right-aligned slot (buttons, chips). */
  actions?: ReactNode;
  /** "← Back" affordance rendered above the title. */
  backLink?: { to: string; label: string };
};

/**
 * The one page header — terracotta underline is its signature.
 * Consolidates the old .sethead, .kb__hero, .chat__title and bare .page-title idioms.
 */
export function PageHeader({ title, eyebrow, description, icon, actions, backLink }: PageHeaderProps) {
  return (
    <header className="pagehead">
      <span className="pagehead__sprig" aria-hidden="true">{icon ?? <Sprig size={50} />}</span>
      <div className="pagehead__copy">
        {backLink && (
          <Link className="pagehead__back" to={backLink.to}>
            <Ic.ChevL size={13} />
            {backLink.label}
          </Link>
        )}
        {eyebrow && <span className="pagehead__eyebrow">{eyebrow}</span>}
        <h2>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="pagehead__actions">{actions}</div>}
    </header>
  );
}
