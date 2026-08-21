import type { GithubStatus, SlackStatus } from "@mari-design/components/features/SourcesBots";

/** The server's intentionally secret-free status payload. Shared by Sources
 * during the navigation transition and Destinations, which owns bot setup. */
export type BotsStatusResponse = {
  botsStatus: {
    slack: { configured: boolean; teamName: string; lastEventAt: string | null; lastError: string | null };
    github: { webhookConfigured: boolean; lastDeliveryAt: string | null; sources: { id: number; repo: string }[] };
  } | null;
};

export const EMPTY_BOTS: { slack: SlackStatus; github: GithubStatus } = {
  slack: { configured: false },
  github: { webhookConfigured: false, repos: [] },
};

export function mapBots(res: BotsStatusResponse): { slack: SlackStatus; github: GithubStatus } {
  const b = res.botsStatus;
  return {
    slack: {
      configured: Boolean(b?.slack?.configured),
      teamName: b?.slack?.teamName || undefined,
      lastEventAt: b?.slack?.lastEventAt || undefined,
      lastError: b?.slack?.lastError || undefined,
    },
    github: {
      webhookConfigured: Boolean(b?.github?.webhookConfigured),
      lastDeliveryAt: b?.github?.lastDeliveryAt || undefined,
      repos: (b?.github?.sources ?? []).map((s) => s.repo).filter(Boolean),
    },
  };
}
