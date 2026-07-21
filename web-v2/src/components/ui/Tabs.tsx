import { ReactNode } from "react";
import * as RTabs from "@radix-ui/react-tabs";
import clsx from "clsx";

export type TabOption<T extends string> = {
  id: T;
  label: string;
  count?: number;
  icon?: ReactNode;
};

export type TabsVariant = "seg" | "filter" | "underline";

/**
 * The one tab strip (Radix Tabs).
 * - seg (default): segmented paper tabs — page-level view switching.
 * - filter: chip row — filtering a list in place (replaces .ans-filters,
 *   .template-filters, .rule-view-tabs idioms).
 * - underline: editorial underline tabs — dense header contexts.
 */
export function Tabs<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
  variant = "seg",
}: {
  value: T;
  options: TabOption<T>[];
  onChange: (value: T) => void;
  ariaLabel: string;
  variant?: TabsVariant;
}) {
  const listCls = variant === "seg" ? "seg-tabs" : `ds-tabs--${variant}`;
  const tabCls = variant === "seg" ? "seg-tabs__tab" : "ds-tab";
  const countCls = variant === "seg" ? "seg-tabs__count" : "ds-tab__count";
  return (
    <RTabs.Root value={value} onValueChange={(v) => onChange(v as T)} activationMode="manual">
      <RTabs.List className={listCls} aria-label={ariaLabel}>
        {options.map((option) => (
          <RTabs.Trigger
            key={option.id}
            value={option.id}
            className={clsx(tabCls, value === option.id && "active")}
          >
            {option.icon}
            {option.label}
            {option.count !== undefined && <span className={countCls}>{option.count}</span>}
          </RTabs.Trigger>
        ))}
      </RTabs.List>
    </RTabs.Root>
  );
}
