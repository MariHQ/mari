// The block editor card: toolbar, contentEditable blocks, and the margin
// annotation column (live findings aligned to their match in the prose).

import { createElement, CSSProperties, ReactNode, RefObject } from "react";
import * as Ic from "../../components/icons";
import { Button, Card, Menu, MenuRadioGroup, MenuRadioItem } from "../../components/ui";
import { BLOCK_LABEL, BLOCK_TAG, type Block, type BlockType } from "./data";

/* toolbar glyph icons (line-art, matching components/icons.tsx) */
type IcP = { size?: number };
const svg = (size: number, children: ReactNode) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    {children}
  </svg>
);
const BulletListIc = ({ size = 15 }: IcP) => svg(size, <>
  <circle cx="5" cy="6.5" r="1" fill="currentColor" /><path d="M10 6.5 h9" />
  <circle cx="5" cy="12" r="1" fill="currentColor" /><path d="M10 12 h9" />
  <circle cx="5" cy="17.5" r="1" fill="currentColor" /><path d="M10 17.5 h9" />
</>);
const JustifyIc = ({ size = 15 }: IcP) => svg(size, <>
  <path d="M4 6.5 h16" /><path d="M4 12 h16" /><path d="M4 17.5 h16" />
</>);
const LineHeightIc = ({ size = 15 }: IcP) => svg(size, <>
  <path d="M10 6.5 h10" /><path d="M10 12 h10" /><path d="M10 17.5 h10" />
  <path d="M5.5 5.5 v13 M3.5 7.5 l2 -2 2 2 M3.5 16.5 l2 2 2 -2" />
</>);

export type Annot = { fid: number; rule: string; red: boolean; quote: string; top?: number };

type EditorProps = {
  blocks: Block[];
  rendered: string[];
  focusedBlock: Block | null;
  fmt: { bold: boolean; italic: boolean; underline: boolean };
  justify: boolean;
  airy: boolean;
  onToggleJustify: () => void;
  onToggleAiry: () => void;
  onExec: (cmd: "bold" | "italic" | "underline") => void;
  onSetBlockType: (type: BlockType) => void;
  bindRef: (id: number) => (el: HTMLElement | null) => void;
  editorRef: RefObject<HTMLDivElement>;
  onBlockInput: (b: Block) => void;
  onBlockKeyDown: (e: React.KeyboardEvent, b: Block) => void;
  onBlockBlur: (id: number) => void;
  onBlockFocus: (id: number) => void;
  annots: Annot[];
  onJumpToFinding: (fid: number) => void;
};

const TYPE_LABELS: Record<string, string> = { h1: "H1", h2: "H2", h3: "H3", p: "Paragraph" };

export function Editor({
  blocks, rendered, focusedBlock, fmt, justify, airy,
  onToggleJustify, onToggleAiry, onExec, onSetBlockType,
  bindRef, editorRef, onBlockInput, onBlockKeyDown, onBlockBlur, onBlockFocus,
  annots, onJumpToFinding,
}: EditorProps) {
  const typeValue = focusedBlock && focusedBlock.type in TYPE_LABELS ? focusedBlock.type : "";
  return (
    <Card variant="flush" className="dr-editor-card">
      <div className="row dr-toolbar">
        <Menu
          align="start"
          trigger={
            <Button compact aria-label="Block type">
              {focusedBlock ? BLOCK_LABEL[focusedBlock.type] : "¶"} <Ic.Chev size={11} />
            </Button>
          }
        >
          <MenuRadioGroup value={typeValue} onValueChange={(v) => onSetBlockType(v as BlockType)}>
            {(["h1", "h2", "h3", "p"] as const).map((t) => (
              <MenuRadioItem key={t} value={t}>{TYPE_LABELS[t]}</MenuRadioItem>
            ))}
          </MenuRadioGroup>
        </Menu>
        {([["bold", "B"], ["italic", "I"], ["underline", "U"]] as const).map(([cmd, glyph]) => (
          <Button
            key={cmd}
            icon
            className={`dr-tool dr-tool--${cmd}`}
            aria-pressed={fmt[cmd]}
            aria-label={`${glyph === "B" ? "Bold" : glyph === "I" ? "Italic" : "Underline"} selection`}
            title={`${glyph === "B" ? "Bold" : glyph === "I" ? "Italic" : "Underline"} selection`}
            onMouseDown={(e) => { e.preventDefault(); onExec(cmd); }}
          >{glyph}</Button>
        ))}
        <Button
          icon
          className="dr-tool"
          aria-pressed={focusedBlock?.type === "li"}
          aria-label="Toggle bullet list"
          title="Toggle bullet list"
          onMouseDown={(e) => { e.preventDefault(); onSetBlockType(focusedBlock?.type === "li" ? "p" : "li"); }}
        ><BulletListIc /></Button>
        <span className="topbar__divider dr-toolbar__divider" />
        <Button icon className="dr-tool" aria-pressed={justify} aria-label="Justify text" title="Justify text" onClick={onToggleJustify}><JustifyIc /></Button>
        <Button icon className="dr-tool" aria-pressed={airy} aria-label="Relaxed line height" title="Relaxed line height" onClick={onToggleAiry}><LineHeightIc /></Button>
        <Button icon className="dr-tool" aria-label="Open the knowledge library" title="Open the knowledge library" onClick={() => window.open("/knowledge", "_blank")}><Ic.External size={13} /></Button>
      </div>

      <div className="dr-editor-grid">
        <div
          ref={editorRef}
          className="dr-prose"
          style={{
            textAlign: justify ? "justify" : undefined,
            ["--dr-lh" as string]: airy ? "2" : "1.65",
          } as CSSProperties}
        >
          {blocks.map((b, i) =>
            createElement(BLOCK_TAG[b.type] as "p", {
              key: b.id,
              ref: bindRef(b.id),
              className: `dr-block dr-${b.type}`,
              contentEditable: true,
              spellCheck: false,
              dangerouslySetInnerHTML: { __html: rendered[i] },
              onInput: () => onBlockInput(b),
              onBlur: () => onBlockBlur(b.id),
              onFocus: () => onBlockFocus(b.id),
              onKeyDown: (e: React.KeyboardEvent) => onBlockKeyDown(e, b),
            }),
          )}
          {blocks.length === 0 && <div className="card__hint">Loading document…</div>}
        </div>

        {/* margin annotations (live findings, aligned to their match) */}
        <div className="dr-margin">
          {annots.map((a) => (
            <div key={a.fid} className="dr-annot" style={{ top: a.top ?? 6 }} onClick={() => onJumpToFinding(a.fid)}>
              <div className="row dr-annot__head">
                <span className={`dr-annot__dot${a.red ? " dr-annot__dot--red" : ""}`} />
                <span className={`dr-annot__rule${a.red ? " dr-annot__rule--red" : ""}`}>{a.rule}</span>
              </div>
              <div className="dr-annot-quote">{a.quote}</div>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}
