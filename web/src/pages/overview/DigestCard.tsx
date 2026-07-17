// This week's digest — Mari's weekly summary of what changed, surfaced on the
// Overview dashboard. Self-contained: owns its query and manual refresh.

import { useState } from "react";
import * as Ic from "../../components/icons";
import { SourceIcon } from "../../components/shared";
import { Button, Card, Chip, EmptyState, Spinner } from "../../components/ui";
import { SourceKey } from "../../data/sources";
import { gql, useQuery } from "../../lib/api";

type DigestTopic = {
  title: string;
  summary: string;
  where: { source: string; label: string }[];
  impact: { name: string; tone: string }[];
};

const DIGEST_QUERY =
  "{ digest { title summary where { source label } impact { name tone } } }";

export default function DigestCard() {
  const [regenerating, setRegenerating] = useState(false);
  const digestQ = useQuery<DigestTopic[]>(DIGEST_QUERY, {
    map: (d) => (d.digest ?? []) as DigestTopic[],
  });

  async function refreshDigest() {
    if (regenerating) return;
    setRegenerating(true);
    await gql("mutation { regenerateDigest }");
    digestQ.refetch();
    setRegenerating(false);
  }

  return (
    <Card
      className="ov-digest"
      title="This week's digest"
      actions={(
        <Button compact onClick={() => void refreshDigest()} disabled={regenerating}>
          <Ic.Shuffle size={14} /> {regenerating ? "Refreshing…" : "Refresh digest"}
        </Button>
      )}
    >
      {regenerating && (
        <div className="digest-note">
          <Spinner size="sm" label="Regenerating digest" /> Mari is re-reading the week…
        </div>
      )}
      {digestQ.loading && (
        <div style={{ display: "grid", placeItems: "center", minHeight: 90 }}>
          <Spinner size="sm" label="Loading digest" />
        </div>
      )}
      {digestQ.error && !digestQ.data && (
        <EmptyState>API offline — the digest is unavailable.</EmptyState>
      )}
      {digestQ.data && digestQ.data.length === 0 && (
        <EmptyState>No digest yet — refresh to have Mari read the week.</EmptyState>
      )}
      {(digestQ.data ?? []).map((t) => (
        <div className="topic" key={t.title} style={{ opacity: regenerating ? 0.55 : 1 }}>
          <h4 className="topic__title">{t.title}</h4>
          <div className="topic__where">
            {t.where.map((w) => (
              <Chip key={w.label} icon={<SourceIcon source={w.source as SourceKey} size={14} />}>
                {w.label}
              </Chip>
            ))}
          </div>
          <p className="topic__sum">{t.summary}</p>
          <div className="topic__impact">
            Impact
            {t.impact.map((i) => (
              <Chip
                key={i.name}
                icon={<span style={{ width: 7, height: 7, borderRadius: "50%", background: i.tone }} />}
              >
                {i.name}
              </Chip>
            ))}
          </div>
        </div>
      ))}
    </Card>
  );
}
