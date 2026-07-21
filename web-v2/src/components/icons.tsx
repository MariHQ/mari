// Icon set — thin wrappers around lucide-react (matches web/saas), kept under
// the original names so the ~60 call sites importing `* as Ic` don't change.

import { CSSProperties } from "react";
import {
  Home as LHome, MessageSquare, BookOpen, Network, ShieldCheck as LShieldCheck, Workflow,
  Send as LSend, Search as LSearch, ChevronDown, ChevronRight, ChevronLeft, ArrowRight,
  Check as LCheck, CheckCircle2, Clipboard as LClipboard, Play as LPlay, FileText,
  Megaphone as LMegaphone, Calendar as LCalendar, Settings, MoreVertical, Maximize2,
  ExternalLink, Bell as LBell, Layers as LLayers, Pencil as LPencil, GitFork, Sparkles,
  Globe as LGlobe, Shuffle as LShuffle, Bookmark as LBookmark, TrendingUp, Feather,
  Tag as LTag, Rocket, Key as LKey, Clock as LClock, Plus as LPlus, X, Trash2,
  RefreshCw, Eye as LEye, BarChart3, type LucideIcon,
} from "lucide-react";

type P = { size?: number; style?: CSSProperties; strokeWidth?: number };

const wrap = (Icon: LucideIcon) => (p: P) => (
  <Icon size={p.size ?? 18} strokeWidth={p.strokeWidth} style={p.style} aria-hidden />
);

export const Home = wrap(LHome);
export const Chat = wrap(MessageSquare);
export const Book = wrap(BookOpen);
export const LineageIcon = wrap(Network);
export const ShieldCheck = wrap(LShieldCheck);
export const Flow = wrap(Workflow);
export const Send = wrap(LSend);
export const Search = wrap(LSearch);
export const Chev = wrap(ChevronDown);
export const ChevR = wrap(ChevronRight);
export const ChevL = wrap(ChevronLeft);
export const ArrowR = wrap(ArrowRight);
export const Check = wrap(LCheck);
export const CheckCircle = wrap(CheckCircle2);
export const Clipboard = wrap(LClipboard);
export const Play = wrap(LPlay);
export const Doc = wrap(FileText);
export const Megaphone = wrap(LMegaphone);
export const Calendar = wrap(LCalendar);
export const Gear = wrap(Settings);
export const Kebab = wrap(MoreVertical);
export const Expand = wrap(Maximize2);
export const External = wrap(ExternalLink);
export const Bell = wrap(LBell);
export const Layers = wrap(LLayers);
export const Pencil = wrap(LPencil);
export const Fork = wrap(GitFork);
export const Sparkle = wrap(Sparkles);
export const Globe = wrap(LGlobe);
export const Shuffle = wrap(LShuffle);
export const Bookmark = wrap(LBookmark);
export const Leaf = wrap(TrendingUp);
export const Quill = wrap(Feather);
export const Tag = wrap(LTag);
export const Sprout = wrap(Rocket);
export const Key = wrap(LKey);
export const Clock = wrap(LClock);
export const Plus = wrap(LPlus);
export const Close = wrap(X);
export const Trash = wrap(Trash2);
export const Refresh = wrap(RefreshCw);
export const Eye = wrap(LEye);
export const Chart = wrap(BarChart3);

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
      <rect x="2" y="2" width="20" height="20" rx="3.5" fill="#fff" stroke="#10263b" strokeWidth="1.5" />
      <path d="M7.5 17.5 V 6.8 l1.8 -.2 5.6 8.6 V 6.5 H 17 v 10.8 l-2 .2 -5.7 -8.7 v 8.5 z" fill="#10263b" />
    </svg>
  );
}

export function GranolaMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <rect x="2" y="2" width="20" height="20" rx="5" fill="#10263b" />
      <path
        d="M16.6 8.6 a5.4 5.4 0 1 0 .9 4.6 h-5"
        fill="none" stroke="#ffffff" strokeWidth="2.1" strokeLinecap="round"
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
