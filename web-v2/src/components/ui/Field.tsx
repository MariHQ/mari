import { forwardRef, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import clsx from "clsx";

/** Labelled form row: uppercase 10.5px/0.12em label + control + optional hint. */
export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field__label">{label}</span>
      {children}
      {hint && <span className="field__hint">{hint}</span>}
    </label>
  );
}

/** The uppercase section label on its own (list headers, filter groups). */
export function SectionLabel({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={clsx("field__label", className)}>{children}</span>;
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...rest },
  ref,
) {
  return <input ref={ref} className={clsx("input", className)} {...rest} />;
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, ...rest },
  ref,
) {
  return <select ref={ref} className={clsx("select", className)} {...rest} />;
});

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement> & { short?: boolean }
>(function Textarea({ className, short = false, ...rest }, ref) {
  return <textarea ref={ref} className={clsx("input", "textarea", short && "textarea--short", className)} {...rest} />;
});
