import { useEffect, useState } from "react";

/* Pages take `mobile` as a prop: components never breakpoint themselves
   (CONVENTIONS §10), so the app is what decides. 1024px is where the library's
   console grid stops fitting a sidebar beside the content. */
const QUERY = "(max-width: 1023px)";

export function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(() =>
    typeof window !== "undefined" && window.matchMedia(QUERY).matches);

  useEffect(() => {
    const mq = window.matchMedia(QUERY);
    const onChange = () => setMobile(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  return mobile;
}
