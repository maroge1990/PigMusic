import { askToConfirm } from "./confirmDialog";
import { sessionHeader } from "./sessionId";
import { notify } from "./notices";
import { useEffect, useState } from "react";
import {
  QualitySelect,
  qualityLabel,
  type QualityOption,
} from "./QualitySelect";
import { api, cachedLibrary, isUnconfirmed } from "./api";
import {
  AudioLines,
  Laptop,
  Lock,
  MonitorSmartphone,
  SlidersHorizontal,
  Smartphone,
  Watch,
} from "lucide-react";
import type { Library } from "./types";
import type { Person } from "./AppShell";
import { PageHeader } from "./PageHeader";
import { formatCount, formatRelative, initials } from "./format";
import { InlineError, Loading } from "./EmptyState";
import { keepPlaying, setKeepPlaying } from "./keepPlaying";

type Session = {
  id: string;
  device_name: string;
  platform: string;
  last_seen_at: number;
  revoked_at: number | null;
  current?: boolean;
};
const platforms: Record<string, string> = {
  web: "Web",
  ios: "iPhone or iPad app",
  watchos: "Apple Watch app",
};
export function AccountSettings({
  user,
  signOut,
}: {
  user: Person;
  signOut: () => Promise<void>;
}) {
  const [quality, setQuality] = useState("");
  const [qualityOptions, setQualityOptions] = useState<QualityOption[]>();
  const [household, setHousehold] = useState("");
  // Undefined until the first answer: the list shows Loading, not "none".
  const [sessions, setSessions] = useState<Session[]>();
  const [error, setError] = useState("");
  const [similar, setSimilar] = useState(keepPlaying);
  const [changing, setChanging] = useState(false);
  const [songCount, setSongCount] = useState<number | null>(null);
  const [appearance, setAppearance] = useState(() => {
    try {
      return localStorage.getItem("pig-music-appearance") || "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    let active = true;
    void cachedLibrary<Library>()
      .then((library) => {
        if (active) setSongCount(library.stats.tracks);
      })
      .catch(() => {
        /* The count is left out. */
      });
    return () => {
      active = false;
    };
  }, []);
  function changeAppearance(value: string) {
    setAppearance(value);
    try {
      localStorage.setItem("pig-music-appearance", value);
    } catch {
      // Private browsing: the server keeps the choice.
    }
    document.documentElement.dataset.theme =
      value === "system"
        ? matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light"
        : value;
    void api<{ default_tab: string }>("/v1/preferences")
      .then((p) =>
        api("/v1/preferences", {
          ...json({ ...p, appearance: value }),
          method: "PUT",
        }),
      )
      .catch((e) => setError(e.message));
  }
  useEffect(() => {
    let active = true;
    void api<{ keep_playing?: boolean }>("/v1/preferences")
      .then(({ keep_playing }) => {
        if (!active || typeof keep_playing !== "boolean") return;
        setKeepPlaying(keep_playing);
        setSimilar(keep_playing);
      })
      .catch(() => {
        /* The last known choice stays. */
      });
    return () => {
      active = false;
    };
  }, []);
  function changeKeepPlaying(keep_playing: boolean) {
    setSimilar(keep_playing);
    setKeepPlaying(keep_playing);
    setError("");
    void api<{ default_tab: string }>("/v1/preferences")
      .then((p) =>
        api<{ keep_playing?: boolean }>("/v1/preferences", {
          ...json({ ...p, keep_playing }),
          method: "PUT",
        }),
      )
      .then((saved) => {
        if (typeof saved?.keep_playing !== "boolean") return;
        setKeepPlaying(saved.keep_playing);
        setSimilar(saved.keep_playing);
      })
      .catch((e) => {
        setKeepPlaying(!keep_playing);
        setSimilar(!keep_playing);
        setError(e.message);
      });
  }
  async function load() {
    const preferences = await api<{
      preferences: { scope: string; item_id: string; preferred: string }[];
      options: QualityOption[];
      household_default: string;
    }>("/v1/quality");
    setQualityOptions(preferences.options);
    setHousehold(preferences.household_default);
    setQuality(
      preferences.preferences.find((p) => p.scope === "system")?.preferred ||
        "",
    );
    setSessions(
      (await api<{ sessions: Session[] }>("/v1/me/sessions")).sessions,
    );
  }
  useEffect(() => {
    void load().catch((e) => setError(e.message));
  }, []);
  async function action(fn: () => Promise<unknown>, text = "Saved") {
    setError("");
    try {
      await fn();
      if (text) notify(text);
      await load();
    } catch (e) {
      setError((e as Error).message);
      // It may have been saved: show what the server holds now (#239).
      if (isUnconfirmed(e)) void load().catch(() => {});
    }
  }
  function json(body: unknown) {
    return {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    };
  }
  const shown = (sessions ?? [])
    .filter((s) => !s.revoked_at)
    .sort((a, b) => Number(!!b.current) - Number(!!a.current));
  return (
    <main className="personal-main account-page">
      <PageHeader title="Your account" />
      {error && <InlineError>{error}</InlineError>}
      <div className="account-layout">
        <section
          className="account-section account-profile"
          aria-labelledby="account-name"
        >
          <span className="account-avatar" aria-hidden="true">
            {initials(user.display_name || user.username)}
          </span>
          <h2 id="account-name">{user.display_name || user.username}</h2>
          {songCount !== null && (
            <span className="account-count pig-amount">
              {formatCount(songCount)} {songCount === 1 ? "song" : "songs"} in
              your library
            </span>
          )}
          <button
            className="text-button account-sign-out"
            aria-label="Sign out of PigMusic"
            onClick={() => void signOut()}
          >
            Sign out
          </button>
        </section>
        <div className="account-grid">
          <section className="account-section" aria-labelledby="account-prefs">
            <h2 id="account-prefs">
              <SlidersHorizontal size={18} aria-hidden="true" />
              Preferences
            </h2>
            <fieldset className="account-appearance">
              <legend>Appearance</legend>
              <span className="account-segments">
                {(["system", "light", "dark"] as const).map((value) => (
                  <label key={value}>
                    <input
                      type="radio"
                      name="appearance"
                      value={value}
                      checked={appearance === value}
                      onChange={() => changeAppearance(value)}
                    />
                    {value[0].toUpperCase() + value.slice(1)}
                  </label>
                ))}
              </span>
            </fieldset>
            <label className="check-row">
              <input
                type="checkbox"
                checked={similar}
                onChange={(e) => changeKeepPlaying(e.target.checked)}
              />
              Keep playing similar songs when the music ends
            </label>
          </section>
          <section
            className="account-section"
            aria-labelledby="account-quality"
          >
            <h2 id="account-quality">
              <AudioLines size={18} aria-hidden="true" />
              Your music quality
            </h2>
            <QualitySelect
              label="Preferred quality"
              value={quality}
              options={qualityOptions}
              inheritLabel={`Use household default${household ? ` (${qualityLabel(household, qualityOptions)})` : ""}`}
              onChange={(value) =>
                void action(() =>
                  api("/v1/quality", {
                    ...json({ scope: "system", preferred: value || null }),
                    method: "PUT",
                  }),
                )
              }
            />
          </section>
          <section
            className="account-section"
            aria-labelledby="account-password"
          >
            <h2 id="account-password">
              <Lock size={18} aria-hidden="true" />
              Password
            </h2>
            {changing ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const form = e.currentTarget;
                  const d = new FormData(form);
                  void action(
                    () =>
                      api(
                        "/v1/me/password",
                        json({
                          current_password: d.get("current"),
                          password: d.get("password"),
                        }),
                      ),
                    "Password changed; other sessions were signed out",
                  ).then(() => form.reset());
                }}
              >
                <label>
                  Current password
                  <input
                    name="current"
                    type="password"
                    required
                    autoFocus
                    autoComplete="current-password"
                    maxLength={256}
                  />
                </label>
                <label>
                  New password
                  <input
                    name="password"
                    type="password"
                    required
                    minLength={12}
                    maxLength={256}
                    autoComplete="new-password"
                  />
                </label>
                <span className="release-actions">
                  <button type="submit">Change password</button>
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => setChanging(false)}
                  >
                    Cancel
                  </button>
                </span>
              </form>
            ) : (
              <button
                className="secondary-button account-start"
                onClick={() => setChanging(true)}
              >
                Change password
              </button>
            )}
          </section>
          <section
            className="account-section account-devices"
            aria-labelledby="account-devices"
          >
            <h2 id="account-devices">
              <MonitorSmartphone size={18} aria-hidden="true" />
              Devices and sessions
            </h2>
            {!sessions && !error && <Loading>Loading your devices…</Loading>}
            <ul className="session-list">
              {shown.map((s) => (
                <li key={s.id}>
                  <span className="session-icon" aria-hidden="true">
                    {deviceIcon(s.platform)}
                  </span>
                  <div>
                    <strong>
                      {s.device_name}
                      {s.current && (
                        <span className="session-current"> · This device</span>
                      )}
                    </strong>
                    <span>
                      {[
                        platforms[s.platform] || s.platform,
                        s.current
                          ? "In use now"
                          : `Last used ${formatRelative(s.last_seen_at)}`,
                      ].join(" · ")}
                    </span>
                  </div>
                  {!s.current && (
                    <button
                      className="text-button session-sign-out"
                      aria-label={`Sign out ${s.device_name}`}
                      onClick={() =>
                        void action(
                          () =>
                            api("/v1/me/sessions/" + s.id, {
                              method: "DELETE",
                            }),
                          "Device signed out",
                        )
                      }
                    >
                      Sign out
                    </button>
                  )}
                </li>
              ))}
            </ul>
            {sessions?.some((s) => !s.revoked_at && !s.current) && (
              <button
                className="secondary-button"
                onClick={async () => {
                  const session = await askToConfirm({
                    title: "Sign out everywhere else?",
                    body: "Every other browser and app signed in to your account is signed out at once. This device stays signed in.",
                    action: "Sign out everywhere else",
                    destructive: true,
                  });
                  if (session)
                    void action(async () => {
                      const result = await api<{ revoked: number }>(
                        "/v1/me/sessions",
                        { method: "DELETE", headers: sessionHeader(session) },
                      );
                      notify(
                        `Signed out ${result.revoked} other ${result.revoked === 1 ? "device" : "devices"}.`,
                      );
                    }, "");
                }}
              >
                Sign out other devices
              </button>
            )}
          </section>
        </div>
      </div>
      {user.role === "owner" && (
        <section className="owner-links" aria-label="Admin shortcuts">
          <h2>Admin</h2>
          <p>
            <a href="#/admin/people">People and account activity</a>
            <a href="#/admin/people">Unassigned music</a>
          </p>
        </section>
      )}
    </main>
  );
}

function deviceIcon(platform: string) {
  if (platform === "ios") return <Smartphone size={20} />;
  if (platform === "watchos") return <Watch size={20} />;
  return <Laptop size={20} />;
}
