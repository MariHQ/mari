// Small presentational glyphs shared by the lineage page and its drawers.

import { ReactNode } from "react";
import * as Ic from "../../components/icons";
import { GitHubMark, NotionMark, SlackMark } from "../../components/icons";
import { SourceIcon } from "../../components/shared";
import { SourceKey } from "../../data/sources";
import { SOURCE_ICON_KEYS } from "./model";

export function nodeIcon(icon: string, source: string, size = 19): ReactNode {
  switch (icon) {
    case "doc": return <Ic.Doc size={size} />;
    case "book": return <Ic.Book size={size} />;
    case "megaphone": return <Ic.Megaphone size={size} />;
    case "github": return <GitHubMark size={size} />;
    case "slack": return <SlackMark size={size} />;
    case "notion": return <NotionMark size={size} />;
  }
  if (SOURCE_ICON_KEYS.includes(source as SourceKey)) return <SourceIcon source={source as SourceKey} size={size} />;
  return <Ic.Doc size={size} />;
}

export const PinGlyph = ({ size = 10 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden>
    <path d="M9 4 h6 M12 4 v7 M7 11 h10 l-2 4 h-6 z M12 15 v5" />
  </svg>
);
