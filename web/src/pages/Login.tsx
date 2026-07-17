import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CloudLogo, Sprig } from "../components/art";
import { GitHubMark } from "../components/icons";
import { authPost, useAuth } from "../lib/auth";
import "./auth.css";

function GoogleMark({ size = 17 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden>
      <path fill="#4285F4" d="M23.5 12.27c0-.85-.08-1.66-.22-2.45H12v4.64h6.45a5.52 5.52 0 0 1-2.4 3.62v3h3.87c2.27-2.09 3.58-5.17 3.58-8.81z" />
      <path fill="#34A853" d="M12 24c3.24 0 5.96-1.07 7.94-2.91l-3.87-3c-1.08.72-2.45 1.15-4.07 1.15-3.13 0-5.78-2.11-6.73-4.96H1.29v3.1A12 12 0 0 0 12 24z" />
      <path fill="#FBBC05" d="M5.27 14.28A7.2 7.2 0 0 1 4.9 12c0-.79.14-1.56.37-2.28v-3.1H1.29a12 12 0 0 0 0 10.76l3.98-3.1z" />
      <path fill="#EA4335" d="M12 4.77c1.77 0 3.35.61 4.6 1.8l3.44-3.44A11.53 11.53 0 0 0 12 0 12 12 0 0 0 1.29 6.62l3.98 3.1C6.22 6.88 8.87 4.77 12 4.77z" />
    </svg>
  );
}

export default function LoginPage() {
  const { bypassEnabled, oauth, refresh } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"signin" | "register">("signin");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      if (mode === "signin") {
        await authPost("/auth/login", { email, password });
      } else {
        await authPost("/auth/register", { name, email, password });
      }
      await refresh();
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const switchMode = (m: "signin" | "register") => {
    setMode(m);
    setError(null);
  };

  const bypass = async () => {
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      await authPost("/auth/bypass", {});
      await refresh();
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login bypass failed. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <span className="auth-sprig auth-sprig--tl"><Sprig size={72} color="#7d8a5c" /></span>
      <span className="auth-sprig auth-sprig--br"><Sprig size={84} color="#a89a76" flip /></span>

      <div className="auth-card">
        <div className="auth-brand">
          <CloudLogo size={72} />
          <h1>Mari Cloud</h1>
          <p className="auth-tagline">Your product knowledge, curated.</p>
        </div>

        <form className="auth-form" onSubmit={submit}>
          {mode === "register" && (
            <div className="auth-field">
              <label htmlFor="auth-name">Name</label>
              <input
                id="auth-name"
                type="text"
                autoComplete="name"
                placeholder="Maya Chen"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </div>
          )}
          <div className="auth-field">
            <label htmlFor="auth-email">Email</label>
            <input
              id="auth-email"
              type="email"
              autoComplete="email"
              placeholder="you@team.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
            />
          </div>
          <div className="auth-field">
            <label htmlFor="auth-password">Password</label>
            <input
              id="auth-password"
              type="password"
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>

          {error && <p className="auth-error" role="alert">{error}</p>}

          <button className="auth-submit" type="submit" disabled={busy}>
            {busy ? "One moment…" : mode === "signin" ? "Sign in" : "Create account"}
          </button>
        </form>

        {bypassEnabled && (
          <>
            <div className="auth-divider">or use the escape hatch</div>
            <button className="auth-bypass" type="button" disabled={busy} onClick={bypass}>
              Bypass
            </button>
          </>
        )}

        <div className="auth-divider">or continue with</div>

        <div className="auth-oauth">
          {oauth.github ? (
            <a className="auth-oauth-btn" href="/auth/oauth/github">
              <GitHubMark size={17} />
              GitHub
            </a>
          ) : (
            <button className="auth-oauth-btn" type="button" disabled title="Configure auth.github_client_id in mari.toml">
              <GitHubMark size={17} />
              GitHub
            </button>
          )}
          {oauth.google ? (
            <a className="auth-oauth-btn" href="/auth/oauth/google">
              <GoogleMark />
              Google
            </a>
          ) : (
            <button className="auth-oauth-btn" type="button" disabled title="Configure auth.google_client_id in mari.toml">
              <GoogleMark />
              Google
            </button>
          )}
        </div>

        <p className="auth-toggle">
          {mode === "signin" ? (
            <>New here? <button type="button" onClick={() => switchMode("register")}>Create an account</button></>
          ) : (
            <>Already have an account? <button type="button" onClick={() => switchMode("signin")}>Sign in</button></>
          )}
        </p>
      </div>
    </div>
  );
}
