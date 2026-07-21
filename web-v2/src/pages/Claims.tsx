// Claims — Content group umbrella over the three claim types the team
// stands behind: Answers (served verbatim), Decisions (ratified), and Facts
// (verified). Each tab renders its existing page unchanged; this is a nav
// consolidation, not a data-model merge — see decisions/index.tsx, Facts.tsx,
// Answers.tsx for the real logic.

import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Tabs } from "../components/ui";
import AnswersPage from "./Answers";
import DecisionsPage from "./Decisions";
import FactsPage from "./Facts";

type ClaimsTab = "answers" | "decisions" | "facts";
const TABS: ClaimsTab[] = ["answers", "decisions", "facts"];
const isClaimsTab = (v: string | null): v is ClaimsTab => TABS.includes(v as ClaimsTab);

export default function ClaimsPage() {
  const [params, setParams] = useSearchParams();
  const initial = params.get("tab");
  const [tab, setTab] = useState<ClaimsTab>(isClaimsTab(initial) ? initial : "answers");

  const pick = (t: ClaimsTab) => {
    setTab(t);
    setParams({ tab: t }, { replace: true });
  };

  return (
    <>
      <span className="eyebrow" style={{ display: "block", marginBottom: 10 }}>Claims</span>
      <Tabs
        ariaLabel="Claims view"
        variant="seg"
        value={tab}
        onChange={pick}
        options={[
          { id: "answers", label: "Answers" },
          { id: "decisions", label: "Decisions" },
          { id: "facts", label: "Facts" },
        ]}
      />
      <div style={{ marginTop: 16 }}>
        {tab === "answers" && <AnswersPage />}
        {tab === "decisions" && <DecisionsPage />}
        {tab === "facts" && <FactsPage />}
      </div>
    </>
  );
}
