/* First-run setup: claiming a fresh workspace.
 *
 * One call creates the first owner and workspace (`POST /auth/setup`). The
 * database serializes competing claims and the server's rejection is rendered
 * verbatim if another browser completed setup first.
 */

import type { Claim, SetupActions } from "@mari-design/components/pages/SetupPage";
import { authPost } from "../../lib/auth";
import { gqlResult } from "../../lib/api";
import type { ActionContext } from "./index";

/** `onDone` re-reads /auth/me: the setup POST creates the session cookie, so
 *  the gate has to re-evaluate or the claimed workspace stays behind the
 *  first-run screen it just left. */
export function setupActions({ refresh, navigate }: ActionContext): SetupActions {
  return {
    finish: (target) => navigate(target === "sources" ? "/sources" : "/"),
    claimWorkspace: async ({ name, email, password, workspace }: Claim) => {
      await authPost("/auth/setup", { name, email, password, workspace });
      // Decide the destination BEFORE re-reading the session. The setup POST
      // already created the cookie, so this query is authenticated — and once
      // `refresh()` lands, the setup screen redirects any signed-in visitor
      // away. An await between that redirect and `navigate("/welcome")` is a
      // race the redirect sometimes wins, stranding the new owner on the
      // overview instead of onboarding.
      const sources = await gqlResult<{ sourcePulse: { id: number }[] }>(
        "{ sourcePulse { id } }",
      );
      await refresh();
      if (sources.ok && (sources.data.sourcePulse ?? []).length === 0) {
        navigate("/welcome");
      }
    },
  };
}
