// Settings → Members & roles.
// Reference: output/imagegen/mari-cloud-settings-admin.png

import { useState } from "react";
import * as Ic from "../../components/icons";
import { GitHubMark } from "../../components/icons";
import { gql, useQuery } from "../../lib/api";
import {
  Avatar, Button, Card, Chip, ConfirmButton, EmptyState, Field, Input, PageHeader, Select, Spinner, Table,
} from "../../components/ui";
import { SavedNote, cap, saveSetting, useSave, useSettings } from "./shared";

type Member = {
  id: number; name: string; initials: string; tint: number;
  email: string; role: string; status: string; joined: string;
};

const ROLES = ["admin", "manager", "user"];

const asTint = (t: number): 1 | 2 | 3 | 4 => ((((t - 1) % 4) + 4) % 4 + 1) as 1 | 2 | 3 | 4;

export default function MembersPage() {
  const [nonce, setNonce] = useState(0);
  const membersQ = useQuery<Member[]>(
    `{ members { id name initials tint email role status joined } }`,
    { map: (d: any) => d.members ?? [] },
  );
  const refetch = () => { setNonce((n) => n + 1); membersQ.refetch(); };

  const settings = useSettings(nonce);
  const members = membersQ.data ?? [];

  // ——— workspace name card ———
  const wsName: string = settings.workspace?.name ?? "";
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState(wsName);
  const nameSave = useSave();
  const saveName = () =>
    nameSave.run(async () => {
      await saveSetting("workspace", { ...settings.workspace, name: nameDraft });
      setEditingName(false);
      refetch();
    });

  // ——— invite panel ———
  const [inviteOpen, setInviteOpen] = useState(false);
  const [invName, setInvName] = useState("");
  const [invEmail, setInvEmail] = useState("");
  const [invRole, setInvRole] = useState("user");
  const invite = useSave();
  const sendInvite = () => {
    if (!invName.trim() || !invEmail.trim()) return;
    invite.run(async () => {
      await gql(
        `mutation($name: String!, $email: String!, $role: String!) { inviteMember(name: $name, email: $email, role: $role) }`,
        { name: invName.trim(), email: invEmail.trim(), role: invRole },
      );
      setInvName(""); setInvEmail(""); setInvRole("user");
      setInviteOpen(false);
      refetch();
    });
  };

  // ——— per-row actions ———
  const changeRole = async (id: number, role: string) => {
    await gql(`mutation($id: Int!, $role: String!) { setMemberRole(id: $id, role: $role) }`, { id, role });
    refetch();
  };
  const remove = async (id: number) => {
    await gql(`mutation($id: Int!) { removeMember(id: $id) }`, { id });
    refetch();
  };

  // ——— github team sync ———
  const [ghOpen, setGhOpen] = useState(false);
  const ghTeam: string = settings.github_team?.team ?? "";
  const ghConnected = !!settings.github_team?.connected && !!ghTeam;
  const [ghDraft, setGhDraft] = useState(ghTeam);
  // sync server → draft during render (previous-value pattern, no effect)
  const [prevGhTeam, setPrevGhTeam] = useState(ghTeam);
  if (ghTeam !== prevGhTeam) {
    setPrevGhTeam(ghTeam);
    setGhDraft(ghTeam);
  }
  const gh = useSave();
  const saveGh = () =>
    gh.run(async () => {
      await saveSetting("github_team", { ...(settings.github_team ?? {}), team: ghDraft, connected: true });
      refetch();
    });

  return (
    <>
      <PageHeader
        eyebrow="Settings"
        title="Members & roles"
        description={`Manage access to ${wsName || "your workspace"}`}
        actions={(
          <Button variant="primary" onClick={() => setInviteOpen((v) => !v)}>
            <Ic.Plus size={14} /> Invite member
          </Button>
        )}
      />

      {inviteOpen && (
        <Card className="setcard" title="Invite a teammate">
          <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1.4fr 0.7fr auto", gap: 14, alignItems: "end" }}>
            <Field label="Name">
              <Input placeholder="Jane Doe" value={invName} onChange={(e) => setInvName(e.target.value)} />
            </Field>
            <Field label="Email">
              <Input placeholder="you@example.com" value={invEmail} onChange={(e) => setInvEmail(e.target.value)} />
            </Field>
            <Field label="Role">
              <Select value={invRole} onChange={(e) => setInvRole(e.target.value)}>
                {ROLES.map((r) => <option key={r} value={r}>{cap(r)}</option>)}
              </Select>
            </Field>
            <div className="row" style={{ gap: 8, paddingBottom: 1 }}>
              <Button variant="primary" onClick={sendInvite} disabled={invite.saving || !invName.trim() || !invEmail.trim()}>
                {invite.saving ? "Sending…" : "Send invite"}
              </Button>
              <Button onClick={() => setInviteOpen(false)}>Cancel</Button>
            </div>
          </div>
          <div className="card__hint" style={{ marginTop: 10 }}>
            Invited members appear below with an amber dot until they sign in.
          </div>
        </Card>
      )}

      {/* workspace name */}
      <Card
        className="setcard"
        eyebrow="Workspace name"
        actions={(
          editingName ? (
            <span className="row" style={{ gap: 8 }}>
              {nameSave.saved && <SavedNote />}
              <Button variant="primary" onClick={saveName} disabled={nameSave.saving || !nameDraft.trim()}>
                {nameSave.saving ? "Saving…" : "Save"}
              </Button>
              <Button onClick={() => { setEditingName(false); setNameDraft(wsName); }}>Cancel</Button>
            </span>
          ) : (
            <span className="row" style={{ gap: 8 }}>
              {nameSave.saved && <SavedNote />}
              <Button onClick={() => { setNameDraft(wsName); setEditingName(true); }}>
                <Ic.Pencil size={13} /> Edit
              </Button>
            </span>
          )
        )}
      >
        {editingName ? (
          <Input
            style={{ maxWidth: 360, fontFamily: "var(--display)", fontSize: 17 }}
            value={nameDraft}
            onChange={(e) => setNameDraft(e.target.value)}
            autoFocus
          />
        ) : (
          <div style={{ fontFamily: "var(--display)", fontSize: 21 }}>{wsName}</div>
        )}
      </Card>

      {/* members table */}
      <Card className="setcard" variant="flush" title="Members">
        <Table columns={["Member", "Email", "Role", "Status", "Joined", { label: "", width: 140 }]}>
          {membersQ.loading && (
            <tr><td colSpan={6} style={{ textAlign: "center", padding: "18px 0" }}><Spinner size="sm" label="Loading members" /></td></tr>
          )}
          {membersQ.error && !membersQ.data && (
            <tr><td colSpan={6}><EmptyState>API offline — members unavailable.</EmptyState></td></tr>
          )}
          {membersQ.data && members.length === 0 && (
            <tr><td colSpan={6}><span className="card__hint">No members yet — send the first invite.</span></td></tr>
          )}
          {members.map((m) => (
            <tr key={m.id}>
              <td>
                <span className="row" style={{ gap: 10 }}>
                  <Avatar name={m.name} initials={m.initials} tint={asTint(m.tint)} size="sm" />
                  <b style={{ fontWeight: 600 }}>{m.name}</b>
                </span>
              </td>
              <td><span className="card__hint">{m.email}</span></td>
              <td>
                <Select
                  aria-label={`Role for ${m.name}`}
                  value={m.role}
                  onChange={(e) => changeRole(m.id, e.target.value)}
                  style={{ width: 118 }}
                >
                  {(ROLES.includes(m.role) ? ROLES : [m.role, ...ROLES]).map((r) => (
                    <option key={r} value={r}>{cap(r)}</option>
                  ))}
                </Select>
              </td>
              <td>
                <Chip tone={m.status === "invited" ? "gold" : "green"} dot>{cap(m.status)}</Chip>
              </td>
              <td><span className="card__hint">{m.joined}</span></td>
              <td style={{ textAlign: "right" }}>
                <ConfirmButton
                  compact
                  confirmLabel="Remove?"
                  aria-label={`Remove ${m.name}`}
                  onConfirm={() => remove(m.id)}
                >
                  <Ic.Trash size={13} /> Remove
                </ConfirmButton>
              </td>
            </tr>
          ))}
        </Table>
      </Card>

      {/* github team sync + provisioning */}
      <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: 16 }}>
        <Card
          className="setcard"
          icon={<GitHubMark size={24} />}
          title="GitHub team sync"
          hint={ghConnected
            ? <span className="row" style={{ gap: 5, color: "var(--green-deep)" }}><Ic.CheckCircle size={14} /> Connected</span>
            : <span className="row" style={{ gap: 5, color: "var(--ink-faint)" }}>Not connected</span>}
        >
          <p className="card__hint" style={{ margin: "0 0 12px" }}>
            Connect a GitHub team to automatically sync members and their access.
          </p>
          <div className="row" style={{ gap: 12, borderTop: "1px solid var(--line-soft)", paddingTop: 12 }}>
            <div style={{ flex: 1 }}>
              <span className="field__label">Connected team</span>
              <div style={{ fontFamily: "var(--serif)", fontSize: 16, marginTop: 2, color: ghTeam ? undefined : "var(--ink-faint)" }}>{ghTeam || "Not connected"}</div>
            </div>
            <Button onClick={() => setGhOpen((v) => !v)}>
              <Ic.Gear size={14} /> Configure
            </Button>
          </div>
          {ghOpen && (
            <div style={{ marginTop: 12, borderTop: "1px solid var(--line-soft)", paddingTop: 12 }}>
              <Field label="GitHub team (org/team)">
                <Input value={ghDraft} onChange={(e) => setGhDraft(e.target.value)} placeholder="org/team" style={{ maxWidth: 320 }} />
              </Field>
              <div className="row" style={{ gap: 10, marginTop: 12 }}>
                <Button variant="primary" onClick={saveGh} disabled={gh.saving || !ghDraft.trim()}>
                  {gh.saving ? "Saving…" : "Save team"}
                </Button>
                {gh.saved && <SavedNote />}
              </div>
            </div>
          )}
        </Card>

        <Card className="setcard" title="Provisioning">
          <p className="card__hint" style={{ margin: "0 0 10px" }}>
            How new members get their accounts.
          </p>
          {[
            { name: "Manual invites", note: "Invite by email from this page", state: <Chip tone="green" dot>Active</Chip> },
            { name: "GitHub team", note: ghConnected ? `Mirrors ${ghTeam}` : "Not configured", state: ghConnected ? <Chip tone="green" dot>Connected</Chip> : <Chip tone="faint" caps>Off</Chip> },
            { name: "SCIM", note: "Directory sync for Okta / Entra", state: <Chip tone="faint" caps>Enterprise</Chip> },
          ].map((p) => (
            <div key={p.name} className="row" style={{ gap: 10, padding: "8px 0", borderTop: "1px solid var(--line-soft)" }}>
              <div style={{ flex: 1 }}>
                <b style={{ fontFamily: "var(--serif)", fontSize: 14.5, fontWeight: 600 }}>{p.name}</b>
                <div className="card__hint">{p.note}</div>
              </div>
              {p.state}
            </div>
          ))}
        </Card>
      </div>
    </>
  );
}
