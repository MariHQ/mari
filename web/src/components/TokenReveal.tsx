// One-time secret reveal (API keys, MCP tokens): gold dashed card, copy
// button, "you won't see this again" warning. The single implementation —
// replaces the hand-rolled boxes in ApiKeys and Publish's MCP tab.

import { useState } from "react";
import * as Ic from "./icons";
import { Button, Card } from "./ui";

export function TokenReveal({ token, title = "Your new token", onDismiss }: {
  token: string;
  title?: string;
  onDismiss: () => void;
}) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard unavailable — the code stays selectable */
    }
  };

  return (
    <Card
      icon={<Ic.Key size={16} />}
      title={title}
      actions={(
        <Button icon aria-label="Dismiss" onClick={onDismiss} title="Dismiss">
          <Ic.Close size={13} />
        </Button>
      )}
      style={{ borderColor: "var(--gold)", background: "#f9f3e0" }}
    >
      <div className="row" style={{ gap: 10 }}>
        <code className="token-reveal__code">{token}</code>
        <Button onClick={copy}>
          {copied ? <Ic.Check size={14} /> : <Ic.Clipboard size={14} />} {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <div className="token-reveal__warn">
        You won't see this again — store it somewhere safe before dismissing.
      </div>
    </Card>
  );
}
