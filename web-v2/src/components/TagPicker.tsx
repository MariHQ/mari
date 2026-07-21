import { useState } from "react";
import { Link } from "react-router-dom";
import * as Ic from "./icons";
import { MenuChoice, Popover } from "./Popover";
import { TagChip } from "./TagChip";

const OPTIONS = ["canonical", "draft", "stale", "deprecated", "internal", "customer-facing", "needs-review"];

export function TagPicker({
  tags,
  onChange,
  compact = false,
}: {
  tags: string[];
  onChange: (tags: string[]) => void;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const toggle = (tag: string) => onChange(tags.includes(tag) ? tags.filter((item) => item !== tag) : [...tags, tag]);
  return (
    <Popover
      open={open}
      onDismiss={() => setOpen(false)}
      align={compact ? "end" : "start"}
      className="tag-picker"
      panelClassName="menu tag-picker__menu"
      trigger={(
        <button className={`btn ${compact ? "btn--icon" : "btn--compact"}`} type="button" aria-label={compact ? "Edit tags" : undefined} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
          <Ic.Tag size={14} />{!compact && "Edit tags"}
        </button>
      )}
    >
      <div className="tag-picker__head"><span>Document tags</span><small>{tags.length} applied</small></div>
      {OPTIONS.map((tag) => (
        <MenuChoice key={tag} checked={tags.includes(tag)} onChange={() => toggle(tag)} visual={<TagChip tag={tag} />}>
          <span className="sr-only">Toggle {tag}</span>
        </MenuChoice>
      ))}
      <div className="tag-picker__foot"><Link to="/library?tab=tags" onClick={() => setOpen(false)}>Manage definitions</Link><button type="button" onClick={() => setOpen(false)}>Done</button></div>
    </Popover>
  );
}
