import { useState } from "react";
import { api, clearSessionReads } from "./api";
import { InlineError } from "./EmptyState";

export function FirstRun({ complete }: { complete: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <main className="account-shell">
      <section className="sign-in-card first-run-card">
        <img src="/brand/mark.png" alt="" className="account-pig" />
        <h1>Welcome to PigMusic</h1>
        <p>Create your administrator account to get started.</p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const form = event.currentTarget;
            const fields = new FormData(form);
            if (fields.get("password") !== fields.get("confirm")) {
              setError("The passwords do not match.");
              return;
            }
            setError("");
            setBusy(true);
            void api("/v1/setup/owner", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                code: String(fields.get("code") || "").trim(),
                display_name: String(fields.get("display_name") || "").trim(),
                username: String(fields.get("username") || "").trim(),
                password: fields.get("password"),
              }),
            })
              .then(() => {
                form.reset();
                clearSessionReads();
                complete();
              })
              .catch((failure) => setError(failure.message))
              .finally(() => setBusy(false));
          }}
        >
          <label>
            Installation code
            <input
              name="code"
              required
              minLength={16}
              maxLength={64}
              autoComplete="off"
            />
          </label>
          <details>
            <summary>Where do I find the installation code?</summary>
            <p>
              In Unraid, open Docker, select PigMusic and choose Logs. Copy the
              code under “PigMusic first-run setup”. It confirms you control
              this installation and stops working after setup.
            </p>
          </details>
          <label>
            Your name
            <input
              name="display_name"
              required
              maxLength={128}
              autoComplete="name"
            />
          </label>
          <label>
            Username
            <input
              name="username"
              required
              minLength={2}
              maxLength={64}
              autoComplete="username"
              pattern="[a-zA-Z0-9][a-zA-Z0-9._-]{1,63}"
            />
          </label>
          <label>
            Password
            <input
              name="password"
              type="password"
              required
              minLength={12}
              maxLength={256}
              autoComplete="new-password"
              aria-describedby="owner-password-help"
            />
          </label>
          <p id="owner-password-help">
            Use at least 12 characters. Avoid common passwords.
          </p>
          <label>
            Confirm password
            <input
              name="confirm"
              type="password"
              required
              minLength={12}
              maxLength={256}
              autoComplete="new-password"
            />
          </label>
          <button className="primary-button" type="submit" disabled={busy}>
            {busy ? "Creating your account…" : "Create administrator account"}
          </button>
        </form>
        {error && <InlineError>{error}</InlineError>}
        <p>
          Existing server music and playlists will belong to this account. You
          can invite other people afterwards.
        </p>
      </section>
    </main>
  );
}
