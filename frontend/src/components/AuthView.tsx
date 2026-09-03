import { useState, type FormEvent } from "react";

import { RequestError, login, register } from "../lib/api";
import type { Principal } from "../lib/types";

interface Props {
  onAuthenticated: (user: Principal) => void;
}

const DEMO_ACCOUNTS = [
  { email: "alice@acme.test", label: "Alice Chen", role: "employee, 4 years' service" },
  { email: "bob@acme.test", label: "Bob Ferreira", role: "employee, 1 year's service" },
  { email: "hr@acme.test", label: "Hana Rao", role: "HR, sees confidential documents" },
  { email: "admin@acme.test", label: "Acme Admin", role: "admin, rules and audit" },
  { email: "carol@globex.test", label: "Carol Novak", role: "a different tenant" },
];

const DEMO_PASSWORD = "Praxis-Demo-2026!";

export default function AuthView({ onAuthenticated }: Props) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("alice@acme.test");
  const [password, setPassword] = useState(DEMO_PASSWORD);
  const [displayName, setDisplayName] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const user =
        mode === "login"
          ? await login(email, password)
          : await register({
              email,
              password,
              display_name: displayName,
              tenant_name: workspace || undefined,
            });
      onAuthenticated(user);
    } catch (caught) {
      setError(
        caught instanceof RequestError
          ? caught.message
          : "Could not reach the server. Is the API running?",
      );
    } finally {
      setBusy(false);
    }
  }

  function useDemoAccount(demoEmail: string) {
    setMode("login");
    setEmail(demoEmail);
    setPassword(DEMO_PASSWORD);
    setError(null);
  }

  return (
    <div className="auth">
      <div className="auth-card">
        <div className="brand-mark" aria-hidden="true">P</div>
        <h1>{mode === "login" ? "Sign in to Praxis" : "Create a workspace"}</h1>
        <p className="subtitle">
          An assistant that answers from your documents and rules — and tells you when
          it cannot.
        </p>

        {error && <div className="form-error" role="alert">{error}</div>}

        <form onSubmit={submit}>
          <div className="field">
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </div>

          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
            {mode === "register" && (
              <p className="field-hint">
                At least 10 characters, mixing three of: lowercase, uppercase, digits,
                symbols.
              </p>
            )}
          </div>

          {mode === "register" && (
            <>
              <div className="field">
                <label htmlFor="display-name">Your name</label>
                <input
                  id="display-name"
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  autoComplete="name"
                />
              </div>
              <div className="field">
                <label htmlFor="workspace">Workspace name</label>
                <input
                  id="workspace"
                  value={workspace}
                  onChange={(event) => setWorkspace(event.target.value)}
                  placeholder="Acme Corporation"
                />
                <p className="field-hint">
                  A new workspace is created with its own isolated documents and rules.
                </p>
              </div>
            </>
          )}

          <button className="primary-button" type="submit" disabled={busy}>
            {busy ? <span className="spinner" /> : mode === "login" ? "Sign in" : "Create workspace"}
          </button>
        </form>

        <p className="auth-switch">
          {mode === "login" ? "No account?" : "Already have an account?"}
          <button
            type="button"
            onClick={() => {
              setMode(mode === "login" ? "register" : "login");
              setError(null);
            }}
          >
            {mode === "login" ? "Create a workspace" : "Sign in"}
          </button>
        </p>

        {mode === "login" && (
          <div className="demo-accounts">
            <h2>Demo accounts</h2>
            {DEMO_ACCOUNTS.map((account) => (
              <button
                key={account.email}
                type="button"
                className="demo-account"
                onClick={() => useDemoAccount(account.email)}
              >
                <span>{account.label}</span>
                <span className="demo-role">{account.role}</span>
              </button>
            ))}
            <p className="field-hint">
              All demo accounts use the password <code>{DEMO_PASSWORD}</code>.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
