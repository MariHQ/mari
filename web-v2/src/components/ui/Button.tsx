import { ButtonHTMLAttributes, forwardRef } from "react";
import clsx from "clsx";

type ButtonVariant = "default" | "primary" | "success" | "danger" | "link";

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  /**
   * primary  — terracotta. The one main action per view.
   * success  — deep green; confirm / verified contexts only.
   * danger   — brick red; destructive actions.
   * link     — linklike text button (blue, wavy hover underline).
   */
  variant?: ButtonVariant;
  /** Tighter padding for toolbars and card heads. */
  compact?: boolean;
  /** 32×32 square icon-only button. Pair with aria-label. */
  icon?: boolean;
  /** Full-width, centered label. */
  block?: boolean;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "default", compact = false, icon = false, block = false, className, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={clsx(
        "btn",
        variant !== "default" && `btn--${variant}`,
        compact && "btn--compact",
        icon && "btn--icon",
        block && "btn--block",
        className,
      )}
      {...rest}
    />
  );
});
