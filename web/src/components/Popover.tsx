import { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";
import { useDismissibleLayer } from "../lib/useDismissibleLayer";

type PopoverProps = {
  open: boolean;
  onDismiss: () => void;
  trigger: ReactNode;
  children: ReactNode;
  align?: "start" | "end" | "stretch";
  className?: string;
  panelClassName?: string;
  role?: "menu" | "dialog";
  ariaLabel?: string;
};

export function Popover({
  open,
  onDismiss,
  trigger,
  children,
  align = "end",
  className = "",
  panelClassName = "menu",
  role = "menu",
  ariaLabel,
}: PopoverProps) {
  const ref = useDismissibleLayer<HTMLDivElement>(open, onDismiss);
  return (
    <div ref={ref} className={`popover popover--${align}${className ? ` ${className}` : ""}`}>
      {trigger}
      {open && (
        <div className={`${panelClassName} popover__panel`} role={role} aria-label={ariaLabel}>
          {children}
        </div>
      )}
    </div>
  );
}

type MenuItemProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  icon?: ReactNode;
  end?: ReactNode;
  active?: boolean;
};

export function MenuItem({ icon, end, active = false, className = "", children, type = "button", ...props }: MenuItemProps) {
  return (
    <button type={type} className={`menu-item${active ? " is-active" : ""}${className ? ` ${className}` : ""}`} {...props}>
      <span className="menu-item__icon" aria-hidden="true">{icon}</span>
      <span className="menu-item__label">{children}</span>
      <span className="menu-item__end" aria-hidden="true">{end}</span>
    </button>
  );
}

type MenuChoiceProps = {
  type?: "checkbox" | "radio";
  checked: boolean;
  onChange: InputHTMLAttributes<HTMLInputElement>["onChange"];
  children: ReactNode;
  visual?: ReactNode;
  name?: string;
};

export function MenuChoice({ type = "checkbox", checked, onChange, children, visual, name }: MenuChoiceProps) {
  return (
    <label className={`menu-choice${visual ? " menu-choice--visual" : ""}`}>
      <input type={type} checked={checked} onChange={onChange} name={name} />
      {visual && <span className="menu-choice__visual" aria-hidden="true">{visual}</span>}
      <span>{children}</span>
    </label>
  );
}
