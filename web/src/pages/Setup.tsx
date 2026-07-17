import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CloudLogo, Sprig } from "../components/art";
import { authPost, useAuth } from "../lib/auth";
import "./auth.css";

function Steps({ step }: { step: 1 | 2 }) {
  return (
    <div className="auth-steps" aria-label={`Step ${step} of 2`}>
      <span className={`auth-step ${step === 1 ? "active" : "done"}`}>
        <span className="auth-step__dot">{step === 1 ? "1" : "✓"}</span>
        Token
      </span>
      <span className="auth-steps__joint" aria-hidden />
      <span className={`auth-step ${step === 2 ? "active" : ""}`}>
        <span className="auth-step__dot">2</span>
        Admin account
      </span>
    </div>
  );
}

export default function SetupPage() {
  const { refresh } = useAuth();
  const navigate = useNavigate();
  const [step, setStep] = useState<1 | 2>(1);
  const [token, setToken] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const toStep2 = (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setStep(2);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      await authPost("/auth/setup", { token: token.trim(), name, email, password, workspace });
      await refresh();
      navigate("/", { replace: true });
    } catch (err) {
      const detail = err instanceof Error ? err.message : "";
      setError(
        detail.toLowerCase().includes("token")
          ? "Invalid token — check the server logs."
          : detail || "Something went wrong. Please try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <span className="auth-sprig auth-sprig--tl"><Sprig size={72} color="#7d8a5c" /></span>
      <span className="auth-sprig auth-sprig--br"><Sprig size={84} color="#a89a76" flip /></span>

      <div className="auth-card auth-card--wide">
        <div className="auth-brand">
          <CloudLogo size={72} />
          <h1>Welcome to Mari Cloud</h1>
        </div>

        <Steps step={step} />

        {step === 1 ? (
          <form className="auth-form" onSubmit={toStep2}>
            <p className="auth-lede">
              Paste the one-time admin token from the server logs to claim this workspace.
            </p>

            <div className="auth-logbox" aria-label="Example server log excerpt">
              <pre>
{`┌──────────────────────────────────────────────┐
│        MARI CLOUD — FIRST-TIME SETUP         │
├──────────────────────────────────────────────┤
│  One-time admin token:                       │
│  `}<span className="tok">3f9c…a41b  ← yours will differ</span>{`               │
│  Paste it into the setup wizard at /setup.   │
└──────────────────────────────────────────────┘
`}<span className="dim">It prints once, when the server first starts.</span>
              </pre>
            </div>

            <div className="auth-field">
              <label htmlFor="setup-token">Admin token</label>
              <input
                id="setup-token"
                type="text"
                autoComplete="off"
                spellCheck={false}
                placeholder="Paste the token here"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                required
                autoFocus
              />
            </div>

            <button className="auth-submit" type="submit" disabled={!token.trim()}>
              Continue
            </button>
          </form>
        ) : (
          <form className="auth-form" onSubmit={submit}>
            <p className="auth-lede">
              Create the admin account and name your workspace — you can invite the team afterwards.
            </p>

            <div className="auth-field">
              <label htmlFor="setup-name">Your name</label>
              <input
                id="setup-name"
                type="text"
                autoComplete="name"
                placeholder="Maya Chen"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                autoFocus
              />
            </div>
            <div className="auth-field">
              <label htmlFor="setup-email">Email</label>
              <input
                id="setup-email"
                type="email"
                autoComplete="email"
                placeholder="you@team.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="auth-field">
              <label htmlFor="setup-password">Password</label>
              <input
                id="setup-password"
                type="password"
                autoComplete="new-password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            <div className="auth-field">
              <label htmlFor="setup-workspace">Workspace name</label>
              <input
                id="setup-workspace"
                type="text"
                autoComplete="organization"
                placeholder="Your team or company name"
                value={workspace}
                onChange={(e) => setWorkspace(e.target.value)}
                required
              />
              <span className="auth-hint">Shown in the sidebar and on published pages.</span>
            </div>

            {error && <p className="auth-error" role="alert">{error}</p>}

            <div className="auth-actions">
              <button className="auth-back" type="button" onClick={() => { setStep(1); setError(null); }}>
                ← Back to token
              </button>
              <button className="auth-submit" type="submit" disabled={busy}>
                {busy ? "Setting up…" : "Finish setup"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
