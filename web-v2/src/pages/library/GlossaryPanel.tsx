// Library → Glossary: shared term definitions Mari uses when writing and reviewing.

import { useState } from "react";
import * as Ic from "../../components/icons";
import { Button, Card, ConfirmButton, EmptyState, Input, Spinner, Table } from "../../components/ui";
import { QueryResult, gql } from "../../lib/api";
import { useSave } from "../settings/shared";

export type GlossaryTerm = { id: number; term: string; definition: string; owner: string; updated: string };

export const GLOSSARY_QUERY = `{ glossary { id term definition owner updated } }`;
export const mapGlossary = (d: any): GlossaryTerm[] => d.glossary ?? [];

export default function GlossaryPanel({ glossaryQ }: { glossaryQ: QueryResult<GlossaryTerm[]> }) {
  const glossary = glossaryQ.data ?? [];
  const refetch = () => glossaryQ.refetch();
  const [editId, setEditId] = useState<number | null>(null);
  const [editTerm, setEditTerm] = useState("");
  const [editDef, setEditDef] = useState("");
  const [newTerm, setNewTerm] = useState("");
  const [newDef, setNewDef] = useState("");
  const glossSave = useSave();
  const addSave = useSave();
  const startEdit = (g: GlossaryTerm) => { setEditId(g.id); setEditTerm(g.term); setEditDef(g.definition); };
  const saveEdit = () =>
    glossSave.run(async () => {
      await gql(
        `mutation($term: String!, $definition: String!, $id: Int) { upsertGlossary(term: $term, definition: $definition, id: $id) }`,
        { term: editTerm.trim(), definition: editDef.trim(), id: editId },
      );
      setEditId(null);
      refetch();
    });
  const deleteTerm = async (id: number) => {
    await gql(`mutation($id: Int!) { deleteGlossary(id: $id) }`, { id });
    refetch();
  };
  const addTerm = () =>
    addSave.run(async () => {
      await gql(
        `mutation($term: String!, $definition: String!) { upsertGlossary(term: $term, definition: $definition) }`,
        { term: newTerm.trim(), definition: newDef.trim() },
      );
      setNewTerm(""); setNewDef("");
      refetch();
    });

  return (
    <div className="library-panel" role="tabpanel">
      <Card
        variant="flush"
        title="Glossary"
        hint="Shared definitions Mari uses when writing and reviewing"
      >
        <Table columns={["Term", "Definition", "Owner", { label: "", width: 96 }]}>
          {glossaryQ.loading && (
            <tr><td colSpan={4} style={{ textAlign: "center", padding: "18px 0" }}><Spinner size="sm" label="Loading glossary" /></td></tr>
          )}
          {glossaryQ.error && !glossaryQ.data && (
            <tr><td colSpan={4}><EmptyState>API offline — glossary unavailable.</EmptyState></td></tr>
          )}
          {glossary.map((g) =>
            editId === g.id ? (
              <tr key={g.id}>
                <td><Input value={editTerm} onChange={(e) => setEditTerm(e.target.value)} /></td>
                <td colSpan={2}><Input value={editDef} onChange={(e) => setEditDef(e.target.value)} /></td>
                <td>
                  <span className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
                    <Button variant="primary" compact onClick={saveEdit} disabled={glossSave.saving || !editTerm.trim() || !editDef.trim()}>
                      {glossSave.saving ? "Saving…" : "Save"}
                    </Button>
                    <Button compact onClick={() => setEditId(null)}>Cancel</Button>
                  </span>
                </td>
              </tr>
            ) : (
              <tr key={g.id}>
                <td><b style={{ fontWeight: 600 }}>{g.term}</b></td>
                <td>{g.definition}</td>
                <td><span className="card__hint">{g.owner} · updated {g.updated}</span></td>
                <td>
                  <span className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
                    <Button icon compact aria-label={`Edit ${g.term}`} title="Edit term" onClick={() => startEdit(g)}>
                      <Ic.Pencil size={13} />
                    </Button>
                    <ConfirmButton
                      compact
                      aria-label={`Delete ${g.term}`}
                      title="Delete term"
                      confirmLabel="Delete?"
                      onConfirm={() => deleteTerm(g.id)}
                    >
                      <Ic.Trash size={13} />
                    </ConfirmButton>
                  </span>
                </td>
              </tr>
            ),
          )}
          {glossaryQ.data && glossary.length === 0 && (
            <tr><td colSpan={4}><span className="card__hint">No terms yet — add the first one below.</span></td></tr>
          )}
          <tr>
            <td>
              <Input placeholder="Semantic link" value={newTerm} onChange={(e) => setNewTerm(e.target.value)} />
            </td>
            <td colSpan={2}>
              <Input placeholder="What it means, in one sentence." value={newDef} onChange={(e) => setNewDef(e.target.value)} />
            </td>
            <td style={{ textAlign: "right" }}>
              <Button variant="primary" compact onClick={addTerm} disabled={addSave.saving || !newTerm.trim() || !newDef.trim()}>
                <Ic.Plus size={13} /> {addSave.saving ? "Adding…" : "Add"}
              </Button>
            </td>
          </tr>
        </Table>
      </Card>
    </div>
  );
}
