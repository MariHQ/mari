import { CSSProperties, ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import * as Ic from "../icons";

export type DrawerProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  /** Small muted text next to the title (e.g. run id, timestamps). */
  meta?: ReactNode;
  /** Sticky footer slot (actions). */
  footer?: ReactNode;
  /** Override the default min(400px, 92vw). */
  width?: number;
  /**
   * false = no dimmed overlay and the page stays interactive behind the
   * drawer (inspector-style panels).
   */
  backdrop?: boolean;
  children: ReactNode;
};

/**
 * The one drawer — Radix Dialog, slides in from the right, paper texture,
 * dashed head border. Generalizes the flows .fl-drawer.
 */
export function Drawer({ open, onOpenChange, title, meta, footer, width, backdrop = true, children }: DrawerProps) {
  const style: CSSProperties | undefined = width ? { width: `min(${width}px, 92vw)` } : undefined;
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange} modal={backdrop}>
      <Dialog.Portal>
        {backdrop && <Dialog.Overlay className="ds-drawer-backdrop" />}
        <Dialog.Content className="ds-drawer" style={style} aria-describedby={undefined}>
          <header className="ds-drawer__head">
            <Dialog.Title className="ds-drawer__title">{title}</Dialog.Title>
            {meta && <span className="ds-drawer__meta">{meta}</span>}
            <Dialog.Close asChild>
              <button className="iconbtn ds-drawer__close" aria-label="Close">
                <span style={{ display: "inline-flex", transform: "rotate(45deg)" }}><Ic.Plus size={15} /></span>
              </button>
            </Dialog.Close>
          </header>
          <div className="ds-drawer__body">{children}</div>
          {footer && <footer className="ds-drawer__foot">{footer}</footer>}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
