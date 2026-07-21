import { ReactNode } from "react";

export type TableColumn = string | { label: string; width?: string | number };

/**
 * The one table — generic .table look with consistent gutters. Put it inside
 * <Card variant="flush"> so rows run edge to edge.
 *
 *   <Card variant="flush" title="Members">
 *     <Table columns={["Name", "Role", "Status"]}>
 *       <tr><td>…</td></tr>
 *     </Table>
 *   </Card>
 */
export function Table({ columns, children }: { columns: TableColumn[]; children: ReactNode }) {
  return (
    <div className="ds-tablewrap">
      <table className="table">
        <thead>
          <tr>
            {columns.map((col, i) => {
              const label = typeof col === "string" ? col : col.label;
              const width = typeof col === "string" ? undefined : col.width;
              return <th key={i} style={width ? { width } : undefined}>{label}</th>;
            })}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
