// Line-art icon set (1.6px strokes, round caps) + simplified source marks.

import { CSSProperties } from "react";

type P = { size?: number; style?: CSSProperties; strokeWidth?: number };

function I({ size = 18, children, style, strokeWidth = 1.6 }: P & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={style}
      aria-hidden
    >
      {children}
    </svg>
  );
}

export const Home = (p: P) => (
  <I {...p}><path d="M4 11 L12 4 L20 11" /><path d="M6 10 V20 H18 V10" /></I>
);
export const Chat = (p: P) => (
  <I {...p}><path d="M4 6 h16 v10 h-9 l-4 4 v-4 h-3 z" /></I>
);
export const Book = (p: P) => (
  <I {...p}><path d="M12 6 C 10 4.5 7 4 4 4.5 V 19 C 7 18.5 10 19 12 20.5 C 14 19 17 18.5 20 19 V 4.5 C 17 4 14 4.5 12 6 Z" /><path d="M12 6 V 20.5" /></I>
);
export const LineageIcon = (p: P) => (
  <I {...p}><circle cx="6" cy="6" r="2.4" /><circle cx="18" cy="9" r="2.4" /><circle cx="10" cy="18" r="2.4" /><path d="M8 7.2 L 15.7 8.6 M16.6 11 L 11.5 16.2 M7 8.3 L 9.3 15.8" /></I>
);
export const ShieldCheck = (p: P) => (
  <I {...p}><path d="M12 3.5 L19 6 V11 C19 16 16 19.5 12 21 C8 19.5 5 16 5 11 V6 Z" /><path d="M9 11.5 l2 2 4 -4.5" /></I>
);
export const Flow = (p: P) => (
  <I {...p}><circle cx="5" cy="7" r="2.2" /><circle cx="19" cy="7" r="2.2" /><circle cx="12" cy="17" r="2.2" /><path d="M7 8 C 9 10 10 12 11 15 M17 8 C 15 10 14 12 13 15" /></I>
);
export const Send = (p: P) => (
  <I {...p}><path d="M4 12 L20 4 L14 20 L11.5 13.5 Z" /><path d="M11.5 13.5 L20 4" /></I>
);
export const Search = (p: P) => (
  <I {...p}><circle cx="10.5" cy="10.5" r="5.5" /><path d="M15 15 L20 20" /></I>
);
export const Chev = (p: P) => <I {...p}><path d="M8 10 l4 4 4 -4" /></I>;
export const ChevR = (p: P) => <I {...p}><path d="M10 7 l5 5 -5 5" /></I>;
export const ChevL = (p: P) => <I {...p}><path d="M14 7 l-5 5 5 5" /></I>;
export const ArrowR = (p: P) => <I {...p}><path d="M4 12 h15 M14 6.5 L 19.5 12 L 14 17.5" /></I>;
export const Check = (p: P) => <I {...p}><path d="M5 12.5 l4.5 4.5 L 19 7.5" /></I>;
export const CheckCircle = (p: P) => (
  <I {...p}><circle cx="12" cy="12" r="8.5" /><path d="M8.5 12.2 l2.5 2.5 4.5 -5" /></I>
);
export const Clipboard = (p: P) => (
  <I {...p}><rect x="6" y="5" width="12" height="16" rx="2" /><path d="M9.5 5 a2.5 2.5 0 0 1 5 0" /><path d="M9 11 h6 M9 15 h6" /></I>
);
export const Play = (p: P) => <I {...p}><path d="M9 7 L 17 12 L 9 17 Z" /></I>;
export const Doc = (p: P) => (
  <I {...p}><path d="M7 3.5 h7 l4 4 V 20.5 H 7 Z" /><path d="M14 3.5 V 8 h4" /><path d="M10 12.5 h5 M10 16 h5" /></I>
);
export const Megaphone = (p: P) => (
  <I {...p}><path d="M4 10 v4 h3 l7 4 V 6 l-7 4 Z" /><path d="M17.5 9.5 a 4 4 0 0 1 0 5" /></I>
);
export const Calendar = (p: P) => (
  <I {...p}><rect x="4.5" y="6" width="15" height="14" rx="2" /><path d="M4.5 10.5 h15 M8.5 4 v3.5 M15.5 4 v3.5" /></I>
);
export const Gear = (p: P) => (
  <I {...p}><circle cx="12" cy="12" r="3" /><path d="M12 4.5 v2.2 M12 17.3 v2.2 M4.5 12 h2.2 M17.3 12 h2.2 M6.7 6.7 l1.6 1.6 M15.7 15.7 l1.6 1.6 M17.3 6.7 l-1.6 1.6 M8.3 15.7 l-1.6 1.6" /></I>
);
export const Kebab = (p: P) => (
  <I {...p}><circle cx="12" cy="5.5" r="1.1" fill="currentColor" /><circle cx="12" cy="12" r="1.1" fill="currentColor" /><circle cx="12" cy="18.5" r="1.1" fill="currentColor" /></I>
);
export const Expand = (p: P) => (
  <I {...p}><path d="M14 5 h5 v5 M19 5 l-6.5 6.5" /><path d="M10 19 H5 v-5 M5 19 l6.5 -6.5" /></I>
);
export const External = (p: P) => (
  <I {...p}><path d="M10 6 H5.5 V18.5 H18 V14" /><path d="M13.5 5 H19 v5.5 M19 5 l-8 8" /></I>
);
export const Bell = (p: P) => (
  <I {...p}><path d="M12 4.5 a5.5 5.5 0 0 1 5.5 5.5 c0 4 1.5 5.5 1.5 5.5 H5 s1.5 -1.5 1.5 -5.5 A5.5 5.5 0 0 1 12 4.5 Z" /><path d="M10.3 18.7 a1.8 1.8 0 0 0 3.4 0" /></I>
);
export const Layers = (p: P) => (
  <I {...p}><path d="M12 4 L20 8.5 L12 13 L4 8.5 Z" /><path d="M4 13 L12 17.5 L20 13" /></I>
);
export const Pencil = (p: P) => (
  <I {...p}><path d="M5 19 l1 -4 L 16.5 4.5 a2 2 0 0 1 3 3 L 9 18 Z" /></I>
);
export const Fork = (p: P) => (
  <I {...p}><circle cx="7" cy="6" r="2" /><circle cx="17" cy="6" r="2" /><circle cx="12" cy="18" r="2" /><path d="M7 8 v2 a4 4 0 0 0 4 4 h0 M17 8 v2 a4 4 0 0 1 -4 4 h0 M12 14 v2" /></I>
);
export const Sparkle = (p: P) => (
  <I {...p}><path d="M12 4 C 12.7 8 14.5 10 18.5 11 C 14.5 12 12.7 14 12 18 C 11.3 14 9.5 12 5.5 11 C 9.5 10 11.3 8 12 4 Z" /></I>
);
export const Globe = (p: P) => (
  <I {...p}><circle cx="12" cy="12" r="8.5" /><path d="M3.5 12 h17 M12 3.5 c-5.5 5 -5.5 12 0 17 c5.5 -5 5.5 -12 0 -17" /></I>
);
export const Shuffle = (p: P) => (
  <I {...p}><path d="M4 7 h4 l8 10 h4 M16 17 l4 0 -0 0 M4 17 h4 l2.5 -3.1 M13.4 10.1 L16 7 h4" /><path d="M17.5 4.8 L20 7 l-2.5 2.2 M17.5 14.8 L20 17 l-2.5 2.2" /></I>
);
export const Bookmark = (p: P) => <I {...p}><path d="M7 4.5 h10 V 20 l-5 -3.5 L7 20 Z" /></I>;
export const Leaf = (p: P) => (
  <I {...p}><path d="M6 18 C 5 10 10 5 19 5 C 19 14 14 19 6 18 Z" /><path d="M6 18 C 9 13 12 10 16 8" /></I>
);
export const Quill = (p: P) => (
  <I {...p}><path d="M5 19 C 6 12 11 6 20 4 C 18 12 13 17 7 18" /><path d="M5 19 L 12 11" /></I>
);
export const Tag = (p: P) => (
  <I {...p}><path d="M4 5 h7 l9 9 -6 6 -10 -10 z" /><circle cx="8" cy="9" r="1.4" /></I>
);
export const Sprout = (p: P) => (
  <I {...p}><path d="M12 20 V10" /><path d="M12 13 C8 13 5 10 5 6 c4 0 7 2 7 6" /><path d="M12 10 c0 -4 3 -6 7 -6 c0 4 -3 7 -7 7" /></I>
);


export const Key = (p: P) => (
  <I {...p}><circle cx="8" cy="12" r="3.5" /><path d="M11.5 12 H20 M17 12 v3 M20 12 v2.4" /></I>
);
export const Clock = (p: P) => (
  <I {...p}><circle cx="12" cy="12" r="8" /><path d="M12 7.5 V12 l3 2.4" /></I>
);
export const Plus = (p: P) => (
  <I {...p}><path d="M12 5 v14 M5 12 h14" /></I>
);
export const Close = (p: P) => (
  <I {...p}><path d="M6.5 6.5 L 17.5 17.5 M17.5 6.5 L 6.5 17.5" /></I>
);
export const Trash = (p: P) => (
  <I {...p}><path d="M5 7 h14 M10 7 V5 h4 v2 M7 7 l1 13 h8 l1 -13 M10 11 v6 M14 11 v6" /></I>
);
export const Refresh = (p: P) => (
  <I {...p}><path d="M19 12 a7 7 0 1 1 -2.2 -5.1 M17.5 3.5 v3.6 h-3.6" /></I>
);
export const Eye = (p: P) => (
  <I {...p}><path d="M2.5 12 C5 7.5 8.5 5.5 12 5.5 s7 2 9.5 6.5 c-2.5 4.5 -6 6.5 -9.5 6.5 s-7 -2 -9.5 -6.5 z" /><circle cx="12" cy="12" r="2.6" /></I>
);

/* ————— source marks (simplified, recognizable) ————— */



export function GitHubMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden>
      <path
        fill="#24292f"
        d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z"
      />
    </svg>
  );
}

export function SlackMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 54 54" aria-hidden>
      <g>
        <path fill="#36C5F0" d="M19.7 32.7a5.4 5.4 0 1 1-5.4-5.4h5.4zm2.7 0a5.4 5.4 0 0 1 10.8 0v13.5a5.4 5.4 0 0 1-10.8 0z" />
        <path fill="#2EB67D" d="M21.3 19.7a5.4 5.4 0 1 1 5.4-5.4v5.4zm0 2.7a5.4 5.4 0 0 1 0 10.8H7.8a5.4 5.4 0 0 1 0-10.8z" />
        <path fill="#ECB22E" d="M34.3 21.3a5.4 5.4 0 1 1 5.4 5.4h-5.4zm-2.7 0a5.4 5.4 0 0 1-10.8 0V7.8a5.4 5.4 0 0 1 10.8 0z" transform="rotate(180 32.95 21.3)" />
        <path fill="#E01E5A" d="M32.7 34.3a5.4 5.4 0 1 1-5.4 5.4v-5.4zm0-2.7a5.4 5.4 0 0 1 0-10.8h13.5a5.4 5.4 0 0 1 0 10.8z" transform="rotate(180 39.45 32.95)" />
      </g>
    </svg>
  );
}

export function DriveMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 87.3 78" aria-hidden>
      <path fill="#0066da" d="m6.6 66.85 3.85 6.65c.8 1.4 1.95 2.5 3.3 3.3l13.75-23.8h-27.5c0 1.55.4 3.1 1.2 4.5z" />
      <path fill="#00ac47" d="m43.65 25-13.75-23.8c-1.35.8-2.5 1.9-3.3 3.3l-25.4 44a9.06 9.06 0 0 0-1.2 4.5h27.5z" />
      <path fill="#ea4335" d="m73.55 76.8c1.35-.8 2.5-1.9 3.3-3.3l1.6-2.75 7.65-13.25c.8-1.4 1.2-2.95 1.2-4.5h-27.502l5.852 11.5z" />
      <path fill="#00832d" d="m43.65 25 13.75-23.8c-1.35-.8-2.9-1.2-4.5-1.2h-18.5c-1.6 0-3.15.45-4.5 1.2z" />
      <path fill="#2684fc" d="m59.8 53h-32.3l-13.75 23.8c1.35.8 2.9 1.2 4.5 1.2h50.8c1.6 0 3.15-.45 4.5-1.2z" />
      <path fill="#ffba00" d="m73.4 26.5-12.7-22c-.8-1.4-1.95-2.5-3.3-3.3l-13.75 23.8 16.15 28h27.45c0-1.55-.4-3.1-1.2-4.5z" />
    </svg>
  );
}

export function NotionMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <rect x="2" y="2" width="20" height="20" rx="3.5" fill="#fff" stroke="#2d2a22" strokeWidth="1.5" />
      <path d="M7.5 17.5 V 6.8 l1.8 -.2 5.6 8.6 V 6.5 H 17 v 10.8 l-2 .2 -5.7 -8.7 v 8.5 z" fill="#2d2a22" />
    </svg>
  );
}

export function GranolaMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <rect x="2" y="2" width="20" height="20" rx="5" fill="#2d2a22" />
      <path
        d="M16.6 8.6 a5.4 5.4 0 1 0 .9 4.6 h-5"
        fill="none" stroke="#f6f0e3" strokeWidth="2.1" strokeLinecap="round"
      />
    </svg>
  );
}

export function DocsMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <rect x="4" y="2.5" width="16" height="19" rx="2.5" fill="#3086f6" />
      <path d="M8 8.5h8M8 12h8M8 15.5h5.5" stroke="#fff" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  );
}
