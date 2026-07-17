import { ReactNode } from "react";
import * as RS from "@radix-ui/react-switch";

/** Notebook-skinned Radix switch. Pass label for a labelled row. */
export function Switch({
  checked,
  onCheckedChange,
  label,
  disabled = false,
  "aria-label": ariaLabel,
}: {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  label?: ReactNode;
  disabled?: boolean;
  "aria-label"?: string;
}) {
  const control = (
    <RS.Root className="ds-switch" checked={checked} onCheckedChange={onCheckedChange} disabled={disabled} aria-label={ariaLabel}>
      <RS.Thumb className="ds-switch__thumb" />
    </RS.Root>
  );
  if (!label) return control;
  return (
    <label className="ds-switchrow">
      {control}
      <span>{label}</span>
    </label>
  );
}
