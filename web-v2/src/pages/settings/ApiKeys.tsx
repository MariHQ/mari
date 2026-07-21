// Settings → API keys.

import { useState } from "react";
import * as Ic from "../../components/icons";
import { TokenReveal } from "../../components/TokenReveal";
import { gql, useQuery } from "../../lib/api";
import { Button, Card, Chip, ConfirmButton, EmptyState, Field, Input, PageHeader, Spinner, Table } from "../../components/ui";
import { useSave } from "./shared";

type ApiKey = { id: number; name: string; prefix: string; scopes: string; created: string; lastUsed: string; revoked: boolean };

export default function ApiKeysPage() {
  const keysQ = useQuery<ApiKey[]>(
    `{ apiKeys { id name prefix scopes created lastUsed revoked } }`,
    { map: (d: any) => d.apiKeys ?? [] },
  );
  const keys = keysQ.data ?? [];
  const refetch = () => keysQ.refetch();

  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState("read");
  const create = useSave();
  const [token, setToken] = useState<string | null>(null);

  const createKey = () =>
    create.run(async () => {
      const d = await gql<{ createApiKey: string }>(
        `mutation($name: String!, $scopes: String!) { createApiKey(name: $name, scopes: $scopes) }`,
        { name: name.trim(), scopes: scopes.trim() || "read" },
      );
      setToken(d?.createApiKey ?? null);
      setName("");
      setScopes("read");
      setFormOpen(false);
      refetch();
    });


  const revoke = async (id: number) => {
    await gql(`mutation($id: Int!) { revokeApiKey(id: $id) }`, { id });
    refetch();
  };

  return (
    <>
      <PageHeader
        eyebrow="Settings"
        title="API keys"
        description="Programmatic access for CI, bots, and the MCP gateway"
        actions={(
          <Button variant="primary" onClick={() => setFormOpen((v) => !v)}>
            <Ic.Plus size={14} /> Create key
          </Button>
        )}
      />

      {formOpen && (
        <Card className="setcard" title="New API key">
          <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1.2fr auto", gap: 14, alignItems: "end" }}>
            <Field label="Name">
              <Input placeholder="Staging deploy" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Scopes">
              <Input className="mono" placeholder="search:read ingest:write" value={scopes} onChange={(e) => setScopes(e.target.value)} />
            </Field>
            <div className="row" style={{ gap: 8, paddingBottom: 1 }}>
              <Button variant="primary" onClick={createKey} disabled={create.saving || !name.trim()}>
                {create.saving ? "Creating…" : "Create key"}
              </Button>
              <Button onClick={() => setFormOpen(false)}>Cancel</Button>
            </div>
          </div>
          <div className="card__hint" style={{ marginTop: 10 }}>
            Scopes are space-separated, e.g. <span className="mono">search:read chat</span>.
          </div>
        </Card>
      )}

      {token && <TokenReveal token={token} title="Your new key" onDismiss={() => setToken(null)} />}

      <Card
        className="setcard"
        variant="flush"
        title="Keys"
        hint="Keys inherit the workspace's rate limits. Revoking takes effect immediately."
      >
        <Table columns={["Name", "Key", "Scopes", "Created", "Last used", "Status", { label: "", width: 110 }]}>
          {keysQ.loading && (
            <tr><td colSpan={7} style={{ textAlign: "center", padding: "18px 0" }}><Spinner size="sm" label="Loading keys" /></td></tr>
          )}
          {keysQ.error && !keysQ.data && (
            <tr><td colSpan={7}><EmptyState>API offline — keys unavailable.</EmptyState></td></tr>
          )}
          {keysQ.data && keys.length === 0 && (
            <tr><td colSpan={7}><span className="card__hint">No keys yet — create the first one above.</span></td></tr>
          )}
          {keys.map((k) => (
            <tr key={k.id} style={k.revoked ? { opacity: 0.45 } : undefined}>
              <td><b style={{ fontWeight: 600 }}>{k.name}</b></td>
              <td><span className="mono">{k.prefix}</span></td>
              <td><span className="card__hint">{k.scopes}</span></td>
              <td><span className="card__hint">{k.created}</span></td>
              <td><span className="card__hint">{k.lastUsed}</span></td>
              <td>
                {k.revoked
                  ? <Chip tone="red" dot>Revoked</Chip>
                  : <Chip tone="green" dot>Active</Chip>}
              </td>
              <td style={{ textAlign: "right" }}>
                {!k.revoked && (
                  <ConfirmButton
                    compact
                    confirmLabel="Revoke?"
                    aria-label={`Revoke ${k.name}`}
                    onConfirm={() => revoke(k.id)}
                  >
                    Revoke
                  </ConfirmButton>
                )}
              </td>
            </tr>
          ))}
        </Table>
      </Card>
    </>
  );
}
