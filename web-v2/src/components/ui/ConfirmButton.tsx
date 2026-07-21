// Two-step destructive confirm — the one pattern for deletes/revokes.
// First click arms ("Really delete?"), second within 4s fires, blur/timeout
// disarms. Replaces window.confirm, inline confirm rows, and unguarded deletes.

import { useEffect, useRef, useState } from "react";
import { Button, ButtonProps } from "./Button";

export function ConfirmButton({ confirmLabel = "Really?", onConfirm, children, ...rest }: Omit<ButtonProps, "onClick" | "variant"> & {
  confirmLabel?: string;
  onConfirm: () => void;
}) {
  const [armed, setArmed] = useState(false);
  const timer = useRef<number>();

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const click = () => {
    if (armed) {
      window.clearTimeout(timer.current);
      setArmed(false);
      onConfirm();
    } else {
      setArmed(true);
      timer.current = window.setTimeout(() => setArmed(false), 4000);
    }
  };

  return (
    <Button {...rest} variant={armed ? "danger" : "default"} onClick={click} onBlur={() => setArmed(false)}>
      {armed ? confirmLabel : children}
    </Button>
  );
}
