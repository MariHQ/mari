/* Adapters for the two unauthenticated pages.
 *
 * Neither reads GraphQL: everything they render comes from `/auth/me`, which
 * the auth context already fetches once for the whole app. What is left is
 * screen state (which step, which fields are prefilled), and that is local —
 * the pages take it as data so the design canvas can drive the same
 * rendering path from a fixture. */

import { useState } from "react";
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
    handoff: null,
    magicLinkTo: "",
    resendIn: "",
    codeDigits: [],
  };
}

export function useLogin(): PageData<LoginData> {
  const { oauth, loading } = useAuth();
  // Which auth step is on screen is route/local state, never inferred from
  // the data (see LoginScreen's doc comment).
  const [screen] = useState<LoginData["screen"]>("credentials");
  const [register] = useState(false);

  return { data: buildLogin(oauth, screen, register), loading, error: null };
}

/** Pure: whether first-run claiming has happened → everything Setup renders. */
export function buildSetup(needsSetup: boolean): SetupData {
  return {
    // needsSetup false means first-run claiming already happened.
    step: needsSetup ? "token" : "done",
    // The one-time admin token is printed to the server log and never
    // exposed over HTTP, so the page shows the operator where to look
    // rather than the token itself.
    logSample: "docker compose logs api | grep 'admin token'",
    token: "", name: "", email: "", password: "", workspace: "",
  };
}

export function useSetup(): PageData<SetupData> {
  const { needsSetup, loading } = useAuth();
  return { data: buildSetup(needsSetup), loading, error: null };
}
