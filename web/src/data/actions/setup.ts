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

type Refresh = () => Promise<void> | void;

/** `onDone` re-reads /auth/me: the setup POST creates the session cookie, so
 *  the gate has to re-evaluate or the claimed workspace stays behind the
 *  first-run screen it just left. */
export function setupActions({ refresh }: { refresh: () => Promise<void> | void }): SetupActions {
  return {
    claimWorkspace: async ({ token, name, email, password, workspace }: Claim) => {
      await authPost("/auth/setup", { token, name, email, password, workspace });
      await refresh();
    },
  };
}
