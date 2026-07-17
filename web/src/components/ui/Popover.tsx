import { ReactNode } from "react";
import * as RPop from "@radix-ui/react-popover";
import clsx from "clsx";

export type PopoverProps = {
  /** The element that opens the popover (Radix asChild — must accept a ref). */
  trigger: ReactNode;
  align?: "start" | "center" | "end";
  side?: "top" | "bottom" | "left" | "right";
  className?: string;
  children: ReactNode;
};

/**
 * Click-open card popover (Radix), notebook-skinned. For hover hints use
 * <Tooltip>; for action lists use <Menu>.
 */
export function Popover({ trigger, align = "end", side = "bottom", className, children }: PopoverProps) {
  return (
    <RPop.Root>
      <RPop.Trigger asChild>{trigger}</RPop.Trigger>
      <RPop.Portal>
        <RPop.Content className={clsx("ds-pop", className)} align={align} side={side} sideOffset={7}>
          {children}
        </RPop.Content>
      </RPop.Portal>
    </RPop.Root>
  );
}
