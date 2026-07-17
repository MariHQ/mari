// Toast — the one transient-feedback primitive (Radix Toast, notebook skin).
// Mount <Toaster/> once in the shell; fire with useToast():
//   const toast = useToast();
//   toast("Workflow saved");                    // ink
//   toast("Run failed — see the panel", "error");
//   toast("Draft imported", "success");

import * as RT from "@radix-ui/react-toast";
import { createContext, ReactNode, useCallback, useContext, useState } from "react";

type Tone = "default" | "success" | "error";
type Item = { id: number; text: string; tone: Tone };

const ToastCtx = createContext<(text: string, tone?: Tone) => void>(() => {});

export function useToast() {
  return useContext(ToastCtx);
}

export function Toaster({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Item[]>([]);

  const push = useCallback((text: string, tone: Tone = "default") => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs.slice(-2), { id, text, tone }]);
  }, []);

  return (
    <ToastCtx.Provider value={push}>
      <RT.Provider swipeDirection="right" duration={3500}>
        {children}
        {items.map((t) => (
          <RT.Root
            key={t.id}
            className={`ds-toast ds-toast--${t.tone}`}
            onOpenChange={(open: boolean) => { if (!open) setItems((xs) => xs.filter((x) => x.id !== t.id)); }}
          >
            <RT.Description>{t.text}</RT.Description>
          </RT.Root>
        ))}
        <RT.Viewport className="ds-toast-viewport" />
      </RT.Provider>
    </ToastCtx.Provider>
  );
}
