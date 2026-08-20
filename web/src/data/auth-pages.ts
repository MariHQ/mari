/* Adapters for the two unauthenticated pages.
 *
 * Neither reads GraphQL: everything they render comes from `/auth/me`, which
 * the auth context already fetches once for the whole app. What is left is
 * screen state (which step, which fields are prefilled), and that is local —
 * the pages take it as data so the design canvas can drive the same
 * rendering path from a fixture. */

import { useSearchParams } from "react-router-dom";
import type { LoginData, LoginProvider } from "@mari-design/components/pages/LoginPage";
import type { SetupData } from "@mari-design/components/pages/SetupPage";
import { useAuth } from "../lib/auth";
import type { PageData } from "./types";

/** Pure: which OAuth providers have credentials + local screen state →
 *  everything the login page renders. */
export function buildLogin(
  oauth: { github?: boolean; google?: boolean },
  screen: LoginData["screen"],
  register: boolean,
  /** Address a magic link was just sent to; only shown on that screen. */
  magicLinkTo = "",
  /** The server reports the sign-in bypass is on. */
  allowBypass = false,
): LoginData {
  const providers: LoginProvider[] = [
    ...(oauth.github ? (["github"] as const) : []),
    ...(oauth.google ? (["google"] as const) : []),
  ];
  return {
    screen,
    title: "Mari",
    sub: "Sign in to your workspace",
    register,
    // Credentials are typed by the user; the page renders whatever it is
    // given, so an app that prefills nothing passes empty strings.
    name: "", email: "", password: "",
    // The API has no notion of a named workspace on the login screen yet,
    // so the field stays hidden rather than showing an invented name.
    workspace: null,
    providers,
    allowRegister: true,
    // Straight from /auth/me. The button exists only where the endpoint behind
    // it will actually answer, so it can never be a control that 404s.
    allowBypass,
    handoff: null,
    magicLinkTo,
    // No resend timer yet: the endpoint has no cooldown to count down to, and
    // an invented countdown would be a number that means nothing.
    resendIn: "",
    codeDigits: [],
  };
}

export function useLogin(): PageData<LoginData> {
  const { oauth, loading, bypassEnabled } = useAuth();

  /* Which auth step is on screen is ROUTE state, not component state. It was
     a `useState` that nothing could ever set, so requesting a magic link
     posted successfully and left you staring at the sign-in form — the send
     worked and the app never said so.

     Putting it in the query string means the confirmation survives a reload,
     can be linked to, and gives "Back to sign in" something real to undo. */
  const [params] = useSearchParams();
  const register = params.get("register") === "1" || Boolean(params.get("invite"));
  const sentTo = params.get("sent") ?? "";
  const screen: LoginData["screen"] = sentTo ? "magic-link" : "credentials";

  /* Nothing can have failed before anyone types: the app no longer signs
     visitors in on its own, so the demo button is the first call this screen
     places, and its failure surfaces through the page's own action handling. */
  return { data: buildLogin(oauth, screen, register, sentTo, bypassEnabled), loading, error: null };
}

/** Pure: whether first-run claiming has happened → everything Setup renders. */
export function buildSetup(needsSetup: boolean): SetupData {
  return {
    step: needsSetup ? "admin" : "done",
    name: "", email: "", password: "", workspace: "",
  };
}

export function useSetup(): PageData<SetupData> {
  const { needsSetup, loading } = useAuth();
  return { data: buildSetup(needsSetup), loading, error: null };
}
