/* First-run setup: claiming a fresh workspace.
 *
 * One call, because the server validates the one-time token and creates the
 * admin account in the same request (`POST /auth/setup`). Its rejections are
 * the whole value of this screen: "Invalid setup token, check the server
 * logs.", "A user with that name or email already exists.", "Setup is not
 * pending." Each says exactly what to do next, so each is re-thrown verbatim
 * and rendered by the page.
 */

import type { Claim, SetupActions } from "@mari-design/components/pages/SetupPage";
import { authPost } from "../../lib/auth";
import { gqlResult } from "../../lib/api";
import type { ActionContext } from "./index";

/** `onDone` re-reads /auth/me: the setup POST creates the session cookie, so
 *  the gate has to re-evaluate or the claimed workspace stays behind the
 *  first-run screen it just left. */
/* `checkToken` stays unwired, so step 1 has no pre-flight check and the token
   is validated where it always was: on the finish button. `POST /auth/setup`
   is the only endpoint that knows whether a setup token is good, and it claims
   the workspace in the same call — there is no way to ask without also
   spending it. A check that always answered "looks fine" would be worse than
   none, because the one thing it exists to catch is a token that is not. */

export function setupActions({ refresh, navigate }: ActionContext): SetupActions {
  return {
    finish: (target) => navigate(target === "sources" ? "/sources" : "/"),
    claimWorkspace: async ({ token, name, email, password, workspace }: Claim) => {
      await authPost("/auth/setup", { token, name, email, password, workspace });
      await refresh();
      const sources = await gqlResult<{ sourcePulse: { id: number }[] }>(
        "{ sourcePulse { id } }",
      );
      if (sources.ok && (sources.data.sourcePulse ?? []).length === 0) {
        navigate("/welcome");
      }
    },
  };
}
