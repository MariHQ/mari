import { ReactNode } from "react";
import * as RT from "@radix-ui/react-tooltip";

/**
 * Hover/focus hint (Radix Tooltip), ink-on-paper skin.
 * The child must accept a ref (native element or forwardRef component).
 */
export function Tooltip({
  label,
  side = "top",
  children,
}: {
  label: ReactNode;
  side?: "top" | "bottom" | "left" | "right";
  children: ReactNode;
}) {
  return (
    <RT.Provider delayDuration={250}>
      <RT.Root>
        <RT.Trigger asChild>{children}</RT.Trigger>
        <RT.Portal>
          <RT.Content className="ds-tip" side={side} sideOffset={6}>
            {label}
            <RT.Arrow className="ds-tip__arrow" />
          </RT.Content>
        </RT.Portal>
      </RT.Root>
    </RT.Provider>
  );
}
