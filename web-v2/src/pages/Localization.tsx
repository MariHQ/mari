// Localization — Delivery & Insights placeholder. No translation-coverage feature
// exists in the data model yet (server tracks audit.languages for the repo
// audit only); this is an honest empty state, not fabricated coverage data.

import * as Ic from "../components/icons";
import { Card, EmptyState, PageHeader } from "../components/ui";

export default function LocalizationPage() {
  return (
    <>
      <PageHeader
        eyebrow="Delivery & Insights"
        title="Localization"
        description="Translation coverage and locale variants across your knowledge base."
      />
      <Card variant="plain">
        <EmptyState icon={<Ic.Globe size={28} />} title="Not built yet">
          Localization coverage isn't tracked yet — this page is a placeholder for a future release.
        </EmptyState>
      </Card>
    </>
  );
}
