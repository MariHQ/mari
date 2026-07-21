// Settings → Audit log.

import { useState } from "react";
import * as Ic from "../../components/icons";
import { useQuery } from "../../lib/api";
import { Avatar, Button, Card, EmptyState, Input, PageHeader, Spinner, Table } from "../../components/ui";

type AuditEvent = { id: number; actor: string; verb: string; target: string; at: string };

export default function AuditLogPage() {
  const [filter, setFilter] = useState("");

  const eventsQ = useQuery<AuditEvent[]>(
    `{ auditLog(limit: 50) { id actor verb target at } }`,
    { map: (d: any) => d.auditLog ?? [] },
  );
  const events = eventsQ.data ?? [];

  const needle = filter.trim().toLowerCase();
  const shown = needle
    ? events.filter((e) => `${e.actor} ${e.verb} ${e.target} ${e.at}`.toLowerCase().includes(needle))
    : events;

  return (
    <>
      <PageHeader
        eyebrow="Settings"
        title="Audit log"
        description="Every change in the workspace, who made it, and when"
        actions={(
          <Button onClick={() => eventsQ.refetch()}>
            <Ic.Refresh size={14} /> Refresh
          </Button>
        )}
      />

      <Card
        className="setcard"
        variant="flush"
        title="Events"
        hint={`${shown.length} of ${events.length} events (last 50)`}
        actions={(
          <Input
            style={{ maxWidth: 340 }}
            placeholder="Filter by actor, action, or target…"
            aria-label="Filter events"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        )}
      >
        <Table columns={["Actor", "Action", "Target", { label: "When", width: 140 }]}>
          {eventsQ.loading && (
            <tr><td colSpan={4} style={{ textAlign: "center", padding: "18px 0" }}><Spinner size="sm" label="Loading events" /></td></tr>
          )}
          {eventsQ.error && !eventsQ.data && (
            <tr><td colSpan={4}><EmptyState>API offline — audit log unavailable.</EmptyState></td></tr>
          )}
          {shown.map((e) => (
            <tr key={e.id}>
              <td>
                <span className="row" style={{ gap: 9 }}>
                  <Avatar name={e.actor} size="sm" />
                  <b style={{ fontWeight: 600 }}>{e.actor}</b>
                </span>
              </td>
              <td><span className="card__hint">{e.verb}</span></td>
              <td>{e.target}</td>
              <td><span className="card__hint" style={{ whiteSpace: "nowrap" }}>{e.at}</span></td>
            </tr>
          ))}
          {eventsQ.data && shown.length === 0 && (
            <tr>
              <td colSpan={4} style={{ textAlign: "center" }}>
                <span className="card__hint">{events.length === 0 ? "No events yet." : "No events match that filter."}</span>
              </td>
            </tr>
          )}
        </Table>
      </Card>
    </>
  );
}
