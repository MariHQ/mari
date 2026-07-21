import { ReactNode } from "react";

/**
 * The one empty state — centered italic serif, optional icon and action.
 * Replaces .dec-empty, .tasks-empty, .rules-empty, .check-clean variants.
 */
export function EmptyState({
  icon,
  title,
  action,
  children,
}: {
  icon?: ReactNode;
  /** Bold first line; children is the muted italic message. */
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="ds-empty">
      {icon && <span className="ds-empty__icon" aria-hidden="true">{icon}</span>}
      {title && <b>{title}</b>}
      <span>{children}</span>
      {action && <span className="ds-empty__action">{action}</span>}
    </div>
  );
}
