import { ReactNode } from "react";
import * as DM from "@radix-ui/react-dropdown-menu";
import clsx from "clsx";
import * as Ic from "../icons";

export type MenuProps = {
  /** The element that opens the menu (rendered via Radix asChild — must accept a ref, e.g. <Button>). */
  trigger: ReactNode;
  align?: "start" | "center" | "end";
  side?: "top" | "bottom" | "left" | "right";
  minWidth?: number;
  children: ReactNode;
};

/**
 * The one dropdown menu — Radix DropdownMenu skinned as the notebook .menu.
 * Replaces bespoke menus (DocReview Drop, Library .template-menu); outside
 * click, Escape, and keyboard nav come from Radix.
 *
 *   <Menu trigger={<Button icon aria-label="More"><Ic.Kebab/></Button>}>
 *     <MenuItem icon={<Ic.Pencil size={15}/>} onSelect={rename}>Rename</MenuItem>
 *     <MenuSeparator />
 *     <MenuItem danger onSelect={remove}>Delete</MenuItem>
 *   </Menu>
 */
export function Menu({ trigger, align = "end", side = "bottom", minWidth, children }: MenuProps) {
  return (
    <DM.Root>
      <DM.Trigger asChild>{trigger}</DM.Trigger>
      <DM.Portal>
        <DM.Content className="ds-menu" align={align} side={side} sideOffset={7} style={minWidth ? { minWidth } : undefined}>
          {children}
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  );
}

export function MenuItem({
  icon,
  end,
  danger = false,
  disabled = false,
  onSelect,
  children,
}: {
  icon?: ReactNode;
  end?: ReactNode;
  danger?: boolean;
  disabled?: boolean;
  onSelect?: () => void;
  children: ReactNode;
}) {
  return (
    <DM.Item
      className={clsx("ds-menu__item", danger && "ds-menu__item--danger")}
      disabled={disabled}
      onSelect={onSelect}
    >
      <span className="ds-menu__icon" aria-hidden="true">{icon}</span>
      <span className="ds-menu__label">{children}</span>
      <span className="ds-menu__end" aria-hidden="true">{end}</span>
    </DM.Item>
  );
}

/** Checkbox row — replaces MenuChoice type="checkbox". */
export function MenuCheckboxItem({
  checked,
  onCheckedChange,
  children,
}: {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  children: ReactNode;
}) {
  return (
    <DM.CheckboxItem
      className="ds-menu__item ds-menu__item--check"
      checked={checked}
      onCheckedChange={onCheckedChange}
      onSelect={(e) => e.preventDefault()}
    >
      <span className="ds-menu__indicator" aria-hidden="true">
        <DM.ItemIndicator><Ic.Check size={13} strokeWidth={2.4} /></DM.ItemIndicator>
      </span>
      <span className="ds-menu__label">{children}</span>
    </DM.CheckboxItem>
  );
}

/** Radio group — replaces MenuChoice type="radio". */
export function MenuRadioGroup({
  value,
  onValueChange,
  children,
}: {
  value: string;
  onValueChange: (value: string) => void;
  children: ReactNode;
}) {
  return <DM.RadioGroup value={value} onValueChange={onValueChange}>{children}</DM.RadioGroup>;
}

export function MenuRadioItem({ value, children }: { value: string; children: ReactNode }) {
  return (
    <DM.RadioItem className="ds-menu__item ds-menu__item--check" value={value}>
      <span className="ds-menu__indicator" aria-hidden="true">
        <DM.ItemIndicator><Ic.Check size={13} strokeWidth={2.4} /></DM.ItemIndicator>
      </span>
      <span className="ds-menu__label">{children}</span>
    </DM.RadioItem>
  );
}

export function MenuLabel({ children }: { children: ReactNode }) {
  return <DM.Label className="ds-menu__heading">{children}</DM.Label>;
}

export function MenuSeparator() {
  return <DM.Separator className="ds-menu__sep" />;
}
