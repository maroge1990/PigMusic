import { useState } from "react";
import { api, ApiError } from "./api";
import {
  clearInvite,
  markInstallHint,
  pendingInvite,
  setupErrorMessage,
} from "./invite";
import { InlineError } from "./EmptyState";

export function SignIn({
  signedIn,
  initialError,
}: {
  signedIn: () => void;
  initialError: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(initialError);
  const [invite] = useState(pendingInvite);
  const joining = invite !== null || location.hash.startsWith("#/join");
  const [setup, setSetup] = useState(
    joining || location.hash.startsWith("#/setup"),
  );
  const [username, setUsername] = useState("");
  async function finishSetup(form: HTMLFormElement) {
    const fields = new FormData(form);
    const password = String(fields.get("password"));
    if (password !== fields.get("confirm")) {
      setError("The passwords do not match.");
      return;
    }
    let account: string;
    try {
      const result = await api<{ user: { username: string } }>("/v1/setup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: fields.get("code"), password }),
      });
      account = result.user.username;
    } catch (e) {
      const err = e as ApiError;
      setError(setupErrorMessage(err.code, err.message));
      return;
    }
    clearInvite();
    form.reset();
    try {
      await api("/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: account, password }),
      });
    } catch {
      setUsername(account);
      setSetup(false);
      setError(
        `Account ready. Sign in with your username ${account} and new password.`,
      );
      return;
    }
    markInstallHint();
    location.hash = "/home";
    signedIn();
  }
  return (
    <main className="account-shell">
      <section className="sign-in-card">
        <div className="account-halo" aria-hidden="true" />
        <img src="/brand/mark.png" alt="" className="account-pig" />
        <span className="account-wordmark" role="img" aria-label="PigMusic">
          <img
            src="/brand/wordmark-light.svg"
            className="light-wordmark"
            alt=""
          />
          <img
            src="/brand/wordmark-dark.svg"
            className="dark-wordmark"
            alt=""
          />
        </span>
        <h1>
          {joining && setup
            ? "Join PigMusic"
            : setup
              ? "Set up your account"
              : "Your music, together"}
        </h1>
        <p>
          {joining && setup
            ? `You've been invited to PigMusic on ${location.host}. Choose a password to finish setting up your account.`
            : setup
              ? "Enter the setup code from your administrator and choose a password."
              : "Sign in to your personal library."}
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const form = e.currentTarget;
            setError("");
            setBusy(true);
            if (setup) {
              void finishSetup(form).finally(() => setBusy(false));
              return;
            }
            const fields = new FormData(form);
            void api("/v1/auth/login", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                username: fields.get("username"),
                password: fields.get("password"),
              }),
            })
              .then(() => {
                form.reset();
                signedIn();
              })
              .catch((e) => setError(e.message))
              .finally(() => setBusy(false));
          }}
        >
          {setup ? (
            <label>
              Setup code
              <input
                name="code"
                defaultValue={invite ?? ""}
                required
                autoComplete="one-time-code"
                maxLength={64}
              />
            </label>
          ) : (
            <label>
              Username
              <input
                name="username"
                defaultValue={username}
                required
                autoComplete="username"
                maxLength={64}
              />
            </label>
          )}
          <label>
            Password
            <input
              name="password"
              type="password"
              required
              minLength={setup ? 12 : undefined}
              autoComplete={setup ? "new-password" : "current-password"}
              maxLength={256}
            />
          </label>
          {setup && (
            <label>
              Confirm password
              <input
                name="confirm"
                type="password"
                required
                minLength={12}
                autoComplete="new-password"
                maxLength={256}
              />
            </label>
          )}
          <button className="primary-button" type="submit" disabled={busy}>
            {setup ? "Create account" : "Sign in"}
          </button>
        </form>
        <button
          className="text-button"
          onClick={() => {
            setSetup(!setup);
            setError("");
          }}
        >
          {setup ? "Back to sign in" : "Have a setup code?"}
        </button>
        {error && <InlineError>{error}</InlineError>}
      </section>
    </main>
  );
}
