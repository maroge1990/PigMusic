import { useEffect, useState } from "react";
import { api } from "./api";
import {
  formatCount,
  formatDate,
  formatDateTime,
  formatRelative,
} from "./format";
import { QualitySelect, defaultQualityOptions } from "./QualitySelect";
import { EmptyLine, Loading } from "./EmptyState";

type Settings = {
  budget_bytes: number;
  reserve_bytes: number;
  profile: string;
  timezone: string;
  files: number;
  used_bytes: number;
  ready: number;
  preparing: number;
  retrying: number;
  failed?: number;
  reclaimable_bytes?: number;
  issues?: { mix: string; person?: string; message: string }[];
  people?: { id: string; name: string; ready: number; preparing: number }[];
};
type SongStatus =
  | "library"
  | "cached"
  | "next"
  | "waiting"
  | "failed"
  | "dropped"
  | "hidden";
type Edition = {
  id: string;
  kind: string;
  day: string;
  title: string;
  reason?: string | null;
  state: "ready" | "preparing";
  active: boolean;
  attempts: number;
  retry_at?: number | null;
  worked_at?: number | null;
  blocked?: string | null;
  published?: number | null;
  songs: {
    title: string;
    artist: string;
    album?: string | null;
    reason?: string | null;
    status: SongStatus;
    detail?: string | null;
  }[];
};
type PersonMixes = { person: string; editions: Edition[] };

const songLabels: Record<SongStatus, string> = {
  library: "In library",
  cached: "Cached",
  next: "Up next",
  waiting: "Waiting",
  failed: "Unavailable",
  dropped: "Left out",
  hidden: "Hidden by listener",
};
function MixPersonDetail({ detail }: { detail: PersonMixes }) {
  if (!detail.editions.length)
    return <EmptyLine>No mixes are planned for {detail.person} yet.</EmptyLine>;
  return (
    <div className="mix-editions">
      {detail.editions.map((edition) => {
        const ready = edition.songs.filter(
          (s) => s.status === "library" || s.status === "cached",
        ).length;
        const failed = edition.songs.filter(
          (s) => s.status === "failed",
        ).length;
        return (
          <details key={edition.id} open={edition.state === "preparing"}>
            <summary>
              <strong>{edition.title}</strong> · {formatDate(edition.day)} ·{" "}
              {edition.state === "ready"
                ? edition.active
                  ? "Live"
                  : "Ready for its day"
                : "Preparing"}{" "}
              · {ready}/{edition.songs.length} songs ready
              {failed > 0 && ` · ${failed} unavailable`}
            </summary>
            <p>
              {edition.reason && <>{edition.reason}. </>}
              {edition.state === "ready" &&
                `Published with ${edition.published ?? ready} songs. `}
              {edition.attempts > 0 && `Attempt ${edition.attempts + 1}. `}
              {edition.blocked && `Waiting: ${edition.blocked}. `}
              {edition.retry_at &&
                `Next try ${formatDateTime(edition.retry_at)}. `}
              {edition.worked_at &&
                `Last worked on ${formatRelative(edition.worked_at)}.`}
            </p>
            <ol>
              {edition.songs.map((song, index) => (
                <li key={index} data-status={song.status}>
                  <span>
                    <strong>{song.title}</strong> {song.artist}
                    {song.album && ` · ${song.album}`}
                    {song.detail && <small>{song.detail}</small>}
                  </span>
                  <em>{songLabels[song.status]}</em>
                </li>
              ))}
            </ol>
          </details>
        );
      })}
    </div>
  );
}

export function MixCacheSettings() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [person, setPerson] = useState<string>();
  const [detail, setDetail] = useState<PersonMixes | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void api<Settings>("/v1/admin/mixes", { signal: controller.signal })
      .then((value) => {
        setSettings(value);
        setPerson((current) => current ?? value.people?.[0]?.id);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setNotice(e.message);
      });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!person) return;
    const controller = new AbortController();
    void api<PersonMixes>(
      `/v1/admin/mixes/people/${encodeURIComponent(person)}`,
      { signal: controller.signal },
    )
      .then(setDetail)
      .catch((e) => {
        if (!controller.signal.aborted) setNotice(e.message);
      });
    return () => controller.abort();
  }, [person, revision]);
  return (
    <section className="system-panel">
      <h2>Mix preparation</h2>
      {settings && (
        <>
          <p>
            {formatCount(settings.files)} cached songs ·{" "}
            {formatCount(Math.round(settings.used_bytes / 1_000_000))} MB used
          </p>
          <p>
            {formatCount(settings.ready)} ready mixes ·{" "}
            {formatCount(settings.preparing)} preparing ·{" "}
            {formatCount(settings.retrying)} retrying ·{" "}
            {formatCount(settings.failed ?? 0)} unavailable candidates
          </p>
          <p>
            {formatCount(
              Math.round((settings.reclaimable_bytes ?? 0) / 1_000_000),
            )}{" "}
            MB can be reclaimed. Active editions and playing queues stay
            protected.
          </p>
          <button
            disabled={busy}
            onClick={() => {
              setBusy(true);
              void api<Settings>("/v1/admin/mixes")
                .then((value) =>
                  setSettings((current) =>
                    current
                      ? {
                          ...current,
                          files: value.files,
                          used_bytes: value.used_bytes,
                          ready: value.ready,
                          preparing: value.preparing,
                          retrying: value.retrying,
                          failed: value.failed,
                          reclaimable_bytes: value.reclaimable_bytes,
                          issues: value.issues,
                          people: value.people,
                        }
                      : value,
                  ),
                )
                .then(() => setRevision((n) => n + 1))
                .catch((e) => setNotice(e.message))
                .finally(() => setBusy(false));
            }}
          >
            Refresh mix status
          </button>
          {!!settings.issues?.length && (
            <details>
              <summary>Preparation issues</summary>
              <ul>
                {settings.issues.map((issue, index) => (
                  <li key={index}>
                    <strong>
                      {issue.person && `${issue.person} · `}
                      {issue.mix}:
                    </strong>{" "}
                    {issue.message}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {!!settings.people?.length && (
            <>
              <nav
                className="personal-tabs mix-people"
                aria-label="Mixes by person"
              >
                {settings.people.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    aria-pressed={person === p.id}
                    onClick={() => {
                      setDetail(null);
                      setPerson(p.id);
                    }}
                  >
                    {p.name}
                    <small>
                      {p.ready} ready · {p.preparing} preparing
                    </small>
                  </button>
                ))}
              </nav>
              {detail ? (
                <MixPersonDetail detail={detail} />
              ) : (
                <Loading>Loading mixes…</Loading>
              )}
            </>
          )}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setBusy(true);
              void api<Settings>("/v1/admin/mixes", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  budget_bytes: settings.budget_bytes,
                  reserve_bytes: settings.reserve_bytes,
                  profile: settings.profile,
                  timezone: settings.timezone,
                }),
              })
                .then((value) => {
                  setSettings(value);
                  setNotice("Mix preparation settings saved.");
                })
                .catch((e) => setNotice(e.message))
                .finally(() => setBusy(false));
            }}
          >
            <label>
              Space for prepared songs (GB)
              <input
                type="number"
                min="1"
                max="2000"
                required
                value={settings.budget_bytes / 1_000_000_000}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    budget_bytes: Number(e.target.value) * 1_000_000_000,
                  })
                }
              />
            </label>
            <label>
              Keep disk space free (GB)
              <input
                type="number"
                min="1"
                max="100"
                required
                value={settings.reserve_bytes / 1_000_000_000}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    reserve_bytes: Number(e.target.value) * 1_000_000_000,
                  })
                }
              />
            </label>
            <QualitySelect
              label="Quality of prepared songs"
              value={settings.profile}
              onChange={(profile) => setSettings({ ...settings, profile })}
              options={defaultQualityOptions.filter((o) =>
                ["mp3_320", "native_standard", "lossless"].includes(o.value),
              )}
            />
            <label>
              Daily rotation timezone
              <input
                required
                value={settings.timezone}
                onChange={(e) =>
                  setSettings({ ...settings, timezone: e.target.value })
                }
              />
            </label>
            <p>
              Uses enabled download sources and pauses when server downloads are
              paused. Active mixes stay protected when the budget is full.
              Preferred lossless may fall back to MP3, then AAC when enabled.
            </p>
            <button disabled={busy}>Save preparation settings</button>
          </form>
        </>
      )}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
