import { lazy, Suspense, useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  ChevronDown,
  CircleCheck,
  Disc3,
  Heart,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";
import { api, updateArtworkRevision } from "./api";
import { formatCount } from "./format";
import { PageHeader, SectionHeader } from "./PageHeader";
import { Cover } from "./Cover";
import { Empty } from "./EmptyState";
import { MediaSettings } from "./MediaSettings";
import { AdminGlance } from "./AdminGlance";
import { QueueView, type Queue, type QueueAction } from "./QueueView";
import {
  DownloadSettingsPanel,
  ServerLibraryTools,
  SystemStatus,
  type DownloadSettings,
} from "./SystemView";
import { go, type Route } from "./routes";
import type { Album, Library, Migration } from "./types";
import "./styles.css";

const AdminPeople = lazy(() =>
  import("./AdminPeople").then((m) => ({ default: m.AdminPeople })),
);
const UnassignedMusic = lazy(() =>
  import("./AdminPeople").then((m) => ({ default: m.UnassignedMusic })),
);

const adminSections = [
  { id: "overview", label: "Overview" },
  { id: "downloads", label: "Downloads" },
  { id: "people", label: "People" },
  { id: "library", label: "Server library" },
  { id: "integrations", label: "Integrations" },
  { id: "migration", label: "Library migration" },
] as const;
type AdminSection = (typeof adminSections)[number]["id"];

/** The owner-only `admin` event of /api/v1/events, relayed by the shell. */
export type AdminState = {
  queue?: Queue;
  scan?: Record<string, unknown>;
  soulseek?: { configured: boolean; connected: boolean } | null;
};

const formatTime = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;

const plural = (count: number, noun: string) =>
  `${formatCount(count)} ${noun}${count === 1 ? "" : "s"}`;

/** Owner-only server administration. Browsing, search and personal settings
 * stay in the shared sections; this view never renders its own navigation shell.
 */
export function AdminTools({ route }: { route: Route }) {
  const section: AdminSection = adminSections.some(
    (s) => s.id === route.parts[0],
  )
    ? (route.parts[0] as AdminSection)
    : "overview";
  const downloadsView = route.parts[1] === "settings" ? "settings" : "queue";
  const [library, setLibrary] = useState<Library | null>(null);
  // Set when Library › Playlists hands over a fresh export; a plain visit
  // starts from that single import entry point instead of a stale inventory.
  const [importLoaded, setImportLoaded] = useState(false);
  const handedOver = route.params.get("imported") === "1";
  useEffect(() => {
    if (handedOver) setImportLoaded(true);
  }, [handedOver]);
  const [migration, setMigration] = useState<Migration | null>(null);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [busy, setBusy] = useState(false);
  const [migrationTab, setMigrationTab] = useState("artists");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [reviewOpen, setReviewOpen] = useState(false);
  const [queue, setQueue] = useState<Queue | null>(null);
  const [downloadSettings, setDownloadSettings] =
    useState<DownloadSettings | null>(null);
  const [soulseekHealth, setSoulseekHealth] = useState<{
    configured: boolean;
    connected: boolean;
  } | null>(null);
  const [publicOrigin, setPublicOrigin] = useState<string | null | undefined>();
  const queueSignature = useRef("");

  const loadLibrary = () =>
    api<Library>("/v1/admin/library")
      .then(setLibrary)
      .catch((e) => setError(e.message));
  // The whole household library is large: read it only for the pages that
  // show it, each time one opens (#97).
  const needsLibrary = section === "overview" || section === "library";
  useEffect(() => {
    if (needsLibrary) void loadLibrary();
  }, [needsLibrary]);

  useEffect(() => {
    api<Migration>("/v1/admin/migration")
      .then(setMigration)
      .catch((e) => setError(e.message));
    api<DownloadSettings>("/v1/admin/settings/downloads")
      .then(setDownloadSettings)
      .catch((e) => setError(e.message));
    api<{ configured: boolean; connected: boolean }>(
      "/v1/admin/settings/soulseek",
    )
      .then(setSoulseekHealth)
      .catch(() => {});
    api<{ public_origin: string | null }>("/v1/admin/access")
      .then((v) => setPublicOrigin(v.public_origin))
      .catch(() => setPublicOrigin(null));
  }, []);

  useEffect(() => {
    if (!reviewOpen) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialog = document.querySelector<HTMLElement>(".review-modal");
    const controls = () =>
      Array.from(
        dialog?.querySelectorAll<HTMLElement>(
          "button:not(:disabled),select:not(:disabled),a[href]",
        ) || [],
      );
    controls()[0]?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setReviewOpen(false);
      if (event.key !== "Tab") return;
      const items = controls();
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [reviewOpen]);

  // Live state comes from the shell's /api/v1/events stream (owner-only
  // `admin` event); Admin keeps no timers of its own.
  useEffect(() => {
    const controller = new AbortController();
    let refreshing = false;
    let pending: Queue | undefined;
    const refresh = async (incoming?: Queue) => {
      // Pushed state costs nothing to apply; only fetches wait for visibility.
      if (!incoming && document.hidden) return;
      if (refreshing) {
        pending = incoming;
        return;
      }
      refreshing = true;
      try {
        const q =
          incoming ||
          (await api<Queue>("/v1/admin/queue", { signal: controller.signal }));
        if (controller.signal.aborted) return;
        setQueue(q);
        updateArtworkRevision(q.artwork_revision);
        const signature = `${q.library_revision}:${JSON.stringify(q.media || {})}`;
        if (
          needsLibrary &&
          queueSignature.current &&
          signature !== queueSignature.current
        )
          void loadLibrary();
        queueSignature.current = signature;
      } catch (e) {
        if ((e as Error).name !== "AbortError" && section === "downloads")
          setError((e as Error).message);
      } finally {
        refreshing = false;
        const latest = pending;
        pending = undefined;
        if (latest && !controller.signal.aborted) void refresh(latest);
      }
    };
    const state = (event: Event) => {
      const detail = (event as CustomEvent<AdminState>).detail;
      if (detail?.queue) void refresh(detail.queue);
      if (detail?.soulseek) setSoulseekHealth(detail.soulseek);
    };
    const poll = () => void refresh();
    window.addEventListener("music-admin-state", state);
    window.addEventListener("music-admin-poll", poll);
    void refresh();
    return () => {
      controller.abort();
      window.removeEventListener("music-admin-state", state);
      window.removeEventListener("music-admin-poll", poll);
    };
  }, [section]);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 3500);
    return () => clearTimeout(timer);
  }, [toast]);

  const toggleExpanded = (id: string) =>
    setExpanded((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const selectTracks = async (ids: string[], selected: boolean) => {
    if (busy || !ids.length) return;

    setBusy(true);

    try {
      setMigration(
        await api<Migration>("/v1/admin/migration/selection", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ track_ids: ids, selected }),
        }),
      );

      return true;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const decideTrack = async (track_id: string, decision: string) => {
    setBusy(true);

    try {
      setMigration(
        await api<Migration>("/v1/admin/migration/decision", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ track_id, decision }),
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const submitImport = async () => {
    setBusy(true);

    try {
      const result = await api<{
        created: number;
        existing: number;
        skipped: number;
      }>("/v1/admin/migration/submit", { method: "POST" });

      setReviewOpen(false);
      setToast(
        `${result.created} added to queue · ${result.existing} already queued · ${result.skipped} skipped`,
      );
      go("/admin/downloads");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const importAll = async () => {
    if (!migration || busy) return;

    const ids = migration.albums.flatMap((a) =>
      (a.tracks || [])
        .filter((t) => t.status !== "downloaded")
        .map((t) => t.id),
    );

    if (await selectTracks(ids, true)) {
      if (
        migration.albums.some((a) =>
          (a.tracks || []).some((t) => t.status === "review" && !t.decision),
        )
      )
        setReviewOpen(true);
      else await submitImport();
    }
  };

  function migrationPage() {
    if (!migration || !importLoaded)
      return (
        <>
          <SectionHeader title="Whole-library import" />
          <Empty
            title="Import an Apple Music export"
            description="Start from Library › Playlists › Import from Apple Music, then choose Admin: review the whole library."
          />
          <a className="primary-button" href="#/library/playlists">
            <Upload size={16} aria-hidden="true" />
            Go to Import
          </a>
        </>
      );

    const visible = migration.albums.filter((a) =>
      migrationTab === "compilations" ? a.compilation : !a.compilation,
    );

    const groups = new Map<string, Album[]>();

    for (const album of visible) {
      const group = migrationTab === "compilations" ? album.id : album.artist;
      groups.set(group, [...(groups.get(group) || []), album]);
    }

    return (
      <>
        <SectionHeader
          title="Whole-library import"
          actions={
            <a className="secondary-button" href="#/library/playlists">
              <Upload size={16} aria-hidden="true" /> New import
            </a>
          }
        />

        <div className="migration-summary">
          <div className="inventory-name">
            <span className="inventory-icon">
              <Heart size={20} />
            </span>
            <div>
              <strong>{migration.source_name}</strong>
              <span>
                Apple Music inventory ·{" "}
                {plural(migration.stats.tracks, "track")}
              </span>
            </div>
          </div>
          <div className="inventory-counts">
            <span>
              <i className="green-dot" />
              {migration.stats.saved} already saved
            </span>
            <span>
              <i className="peach-dot" />
              {migration.stats.missing} missing
            </span>
            {migration.stats.review > 0 && (
              <span>
                <i className="amber-dot" />
                {migration.stats.review} to check
              </span>
            )}
          </div>
        </div>

        <div className="review-note">
          <ShieldCheck size={19} />
          <p>
            Import every song in this export, or choose a smaller selection.
            Already saved music is skipped. Requests use your download settings
            and save into your SMB library.
          </p>
        </div>

        <div className="import-actions">
          <button
            className="primary-button"
            disabled={
              busy || (!migration.stats.missing && !migration.stats.review)
            }
            onClick={importAll}
          >
            Import all <ArrowRight size={16} />
          </button>
          <button
            className="secondary-button"
            disabled={busy || !migration.stats.selected}
            onClick={() =>
              selectTracks(
                migration.albums.flatMap((a) =>
                  (a.tracks || []).filter((t) => t.selected).map((t) => t.id),
                ),
                false,
              )
            }
          >
            Clear selection
          </button>
        </div>
        <div className="library-controls">
          <div
            className="tab-list"
            role="tablist"
            aria-label="Migration groups"
          >
            <button
              role="tab"
              aria-selected={migrationTab === "artists"}
              className={migrationTab === "artists" ? "active" : ""}
              onClick={() => setMigrationTab("artists")}
            >
              Artists & albums
            </button>
            <button
              role="tab"
              aria-selected={migrationTab === "compilations"}
              className={migrationTab === "compilations" ? "active" : ""}
              onClick={() => setMigrationTab("compilations")}
            >
              Compilations
              <span>
                {migration.albums.filter((a) => a.compilation).length}
              </span>
            </button>
          </div>
          <span className="muted small">
            {plural(
              groups.size,
              migrationTab === "compilations" ? "compilation" : "artist",
            )}
          </span>
        </div>

        <div className="migration-groups">
          {[...groups].map(([id, albums]) => {
            const tracks = albums.flatMap((a) => a.tracks || []);
            const saved = tracks.filter(
              (t) => t.status === "downloaded",
            ).length;

            const selected = tracks.filter((t) => t.selected).length;
            const missing = tracks.filter((t) => t.status === "missing");

            const allMissingSelected =
              missing.length > 0 && missing.every((t) => t.selected);

            return (
              <section
                className={`migration-group ${expanded.has(id) ? "expanded" : ""}`}
                key={id}
              >
                <div className="group-head">
                  <button
                    className="group-expand"
                    aria-expanded={expanded.has(id)}
                    onClick={() => toggleExpanded(id)}
                  >
                    <Cover
                      title={
                        migrationTab === "compilations" ? albums[0].title : id
                      }
                      src={albums[0].cover_url}
                      artist={migrationTab !== "compilations"}
                    />
                    <div>
                      <strong>
                        {migrationTab === "compilations" ? albums[0].title : id}
                      </strong>
                      <span>
                        {plural(albums.length, "release")} ·{" "}
                        {plural(tracks.length, "track")}
                        {saved > 0 && ` · ${saved} saved`}
                      </span>
                    </div>
                    <ChevronDown size={17} />
                  </button>

                  <div className="group-actions">
                    {selected > 0 && (
                      <span className="selection-count">
                        {selected} selected
                      </span>
                    )}
                    <button
                      className={`small-button ${allMissingSelected ? "selected-button" : ""}`}
                      disabled={busy || saved === tracks.length}
                      onClick={() =>
                        missing.length
                          ? selectTracks(
                              missing.map((t) => t.id),
                              !allMissingSelected,
                            )
                          : toggleExpanded(id)
                      }
                    >
                      <Heart size={13} />{" "}
                      {allMissingSelected
                        ? "Selected"
                        : missing.length
                          ? "Select missing"
                          : saved === tracks.length
                            ? "Already saved"
                            : "Check versions"}
                    </button>
                  </div>
                </div>

                {expanded.has(id) && (
                  <div className="group-content">
                    {albums.map((album) => {
                      const eligible = (album.tracks || []).filter(
                        (t) => t.status === "missing",
                      );

                      const allSelected =
                        !!eligible.length && eligible.every((t) => t.selected);

                      return (
                        <div className="migration-album" key={album.id}>
                          <div className="migration-album-head">
                            <Disc3 size={16} />
                            <div>
                              <strong>{album.title}</strong>
                              <span>
                                {album.year || "Release"} ·{" "}
                                {plural(album.track_count, "track")}
                              </span>
                            </div>
                            <button
                              className="text-button"
                              disabled={busy || !eligible.length}
                              onClick={() =>
                                selectTracks(
                                  eligible.map((t) => t.id),
                                  !allSelected,
                                )
                              }
                            >
                              {allSelected
                                ? "Deselect missing"
                                : eligible.length
                                  ? "Select missing"
                                  : album.saved_count === album.track_count
                                    ? "Already saved"
                                    : "Check versions"}
                            </button>
                          </div>

                          {(album.tracks || []).map((track) => (
                            <label
                              className={`migration-track ${track.selected ? "chosen" : ""}`}
                              key={track.id}
                            >
                              <input
                                type="checkbox"
                                aria-label={`Keep ${track.title} by ${track.artist}`}
                                checked={!!track.selected}
                                disabled={busy || track.status === "downloaded"}
                                onChange={(e) =>
                                  selectTracks([track.id], e.target.checked)
                                }
                              />
                              <span className="track-position">
                                {track.track_number || "–"}
                              </span>
                              <div className="track-name">
                                <strong>{track.title}</strong>
                                <span>{track.artist}</span>
                                {track.status === "review" && (
                                  <small>{track.reason}</small>
                                )}
                              </div>
                              <span className="track-time">
                                {formatTime(track.duration)}
                              </span>
                              <span
                                className={`migration-status ${track.status}`}
                              >
                                {track.status === "downloaded" ? (
                                  <>
                                    <CircleCheck size={13} />
                                    Saved
                                  </>
                                ) : track.status === "review" ? (
                                  "Check version"
                                ) : (
                                  "Missing"
                                )}
                              </span>
                            </label>
                          ))}
                        </div>
                      );
                    })}
                  </div>
                )}
              </section>
            );
          })}
        </div>

        {!groups.size && (
          <Empty
            title={
              migration.stats.tracks
                ? "No compilations in this inventory"
                : "Start with an export"
            }
            description={
              migration.stats.tracks
                ? "Multi-artist releases will appear here when they are in your export."
                : "Import an Apple Music XML export to review what you want to retain."
            }
          />
        )}

        <div className="migration-footer">
          <div>
            <span className="footer-icon">
              <Heart size={18} />
            </span>
            <div>
              <strong>
                {migration.stats.selected
                  ? plural(migration.stats.selected, "track") + " selected"
                  : "No songs selected"}
              </strong>
              <span>
                {migration.stats.selected
                  ? "Ready to submit to the download queue"
                  : "Select songs to import."}
              </span>
            </div>
          </div>
          <button
            className="primary-button"
            disabled={busy || !migration.stats.selected}
            onClick={() =>
              unresolved.length ? setReviewOpen(true) : submitImport()
            }
          >
            Import now <ArrowRight size={16} />
          </button>
        </div>
      </>
    );
  }

  const queueAction = async (id: string, action: QueueAction) => {
    setBusy(true);
    try {
      setQueue(
        await api<Queue>(`/v1/admin/queue/${id}/${action}`, { method: "POST" }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const saveDownloads = async () => {
    if (!downloadSettings) return;
    setBusy(true);
    try {
      setDownloadSettings(
        await api<DownloadSettings>("/v1/admin/settings/downloads", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(downloadSettings),
        }),
      );
      setToast("Download settings saved");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  async function clearQueue(finished: boolean) {
    if (!queue?.items.length) return;
    const unfinished = queue.items.some(
      (t) =>
        !["completed", "downloaded", "cancelled", "failed"].includes(t.status),
    );
    if (
      !finished &&
      unfinished &&
      !window.confirm(
        "Cancel every unfinished download and clear the queue? Downloaded music will be kept.",
      )
    )
      return;
    setBusy(true);
    try {
      setQueue(
        await api<Queue>(
          `/v1/admin/queue/clear${finished ? "?finished=true" : ""}`,
          {
            method: "POST",
          },
        ),
      );
      setToast(
        finished
          ? "Finished entries cleared. Downloaded music kept."
          : "Queue cleared. Downloaded music kept.",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const selectedTracks =
    migration?.albums.flatMap((a) =>
      (a.tracks || []).filter((t) => t.selected),
    ) || [];

  const conflicts = selectedTracks.filter((t) => t.status === "review");

  const unresolved = conflicts.filter((t) => !t.decision);

  const activeQueue =
    queue?.items.filter((t) => !["completed", "cancelled"].includes(t.status))
      .length || 0;

  return (
    <main className="personal-main admin-main">
      <PageHeader
        title="Admin"
        subtitle="Manage people, music and the server."
      />
      <nav className="personal-tabs" aria-label="Admin sections">
        {adminSections.map((s) => (
          <a
            key={s.id}
            href={"#/admin/" + s.id}
            aria-current={section === s.id ? "page" : undefined}
          >
            {s.label}
            {s.id === "downloads" && activeQueue > 0 && (
              <span className="nav-count">{activeQueue}</span>
            )}
          </a>
        ))}
      </nav>
      {error && (
        <div className="error-banner" role="alert">
          <span>{error}</span>
          <button
            className="icon-button"
            aria-label="Dismiss error"
            onClick={() => setError("")}
          >
            <X size={16} />
          </button>
        </div>
      )}
      {section === "overview" && (
        <AdminGlance queue={queue} library={library} />
      )}
      {section === "overview" && (
        <SystemStatus
          library={library}
          downloadSettings={downloadSettings}
          soulseekHealth={soulseekHealth}
          publicOrigin={publicOrigin}
        />
      )}
      {section === "downloads" && (
        <>
          <nav className="personal-tabs segmented" aria-label="Downloads views">
            <a
              href="#/admin/downloads"
              aria-current={downloadsView === "queue" ? "page" : undefined}
            >
              Queue
            </a>
            <a
              href="#/admin/downloads/settings"
              aria-current={downloadsView === "settings" ? "page" : undefined}
            >
              Settings
            </a>
          </nav>
          {downloadsView === "queue" && (
            <QueueView
              queue={queue}
              busy={busy}
              clearQueue={clearQueue}
              queueAction={queueAction}
            />
          )}
          {downloadsView === "settings" && downloadSettings && (
            <DownloadSettingsPanel
              downloadSettings={downloadSettings}
              setDownloadSettings={setDownloadSettings}
              soulseekHealth={soulseekHealth}
              busy={busy}
              save={() => void saveDownloads()}
            />
          )}
        </>
      )}
      {(section === "people" || section === "library") && (
        <Suspense fallback={<p role="status">Loading…</p>}>
          {section === "people" ? (
            <AdminPeople />
          ) : (
            <>
              <UnassignedMusic />
              <ServerLibraryTools
                library={library}
                refresh={() => void loadLibrary()}
                setToast={setToast}
              />
            </>
          )}
        </Suspense>
      )}
      {section === "integrations" && <MediaSettings />}
      {section === "migration" && migrationPage()}
      {toast && (
        <div className="toast" role="status">
          <CircleCheck size={17} />
          {toast}
        </div>
      )}

      {reviewOpen && (
        <div
          className="modal-backdrop"
          onClick={(e) => {
            if (e.target === e.currentTarget) setReviewOpen(false);
          }}
        >
          <section
            className="review-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="review-title"
          >
            <div className="modal-heading">
              <div>
                <div className="eyebrow">ONLY THE UNCERTAIN ONES</div>
                <h2 id="review-title">Check these versions.</h2>
              </div>
              <button
                className="icon-button"
                aria-label="Close review"
                onClick={() => setReviewOpen(false)}
              >
                <X size={20} />
              </button>
            </div>
            <p>
              {plural(unresolved.length, "decision")} remaining. Other selected
              songs are ready. Keep the recording and release requested in your
              export, or skip this song.
            </p>
            <div className="review-tracks">
              {conflicts.map((t) => (
                <div className="conflict-row" key={t.id}>
                  <div>
                    <strong>{t.title}</strong>
                    <span>
                      {t.artist} · {t.album}
                    </span>
                    <span>{t.reason}</span>
                    <label>
                      Version choice
                      <select
                        aria-label={`Version choice for ${t.title} by ${t.artist}`}
                        disabled={busy}
                        value={t.decision || ""}
                        onChange={(e) => decideTrack(t.id, e.target.value)}
                      >
                        <option value="" disabled>
                          Choose…
                        </option>
                        <option value="requested">
                          Keep requested release
                        </option>
                        <option value="skip">Skip this song</option>
                      </select>
                    </label>
                  </div>
                </div>
              ))}
            </div>
            <div className="modal-actions">
              <button
                className="secondary-button"
                onClick={() => setReviewOpen(false)}
              >
                Back to selection
              </button>
              <button
                className="primary-button"
                disabled={busy || unresolved.length > 0}
                onClick={submitImport}
              >
                Import now <ArrowRight size={16} />
              </button>
            </div>
            <p>
              Requests are saved in the queue and downloaded when enabled in
              Admin › Downloads.
            </p>
          </section>
        </div>
      )}
    </main>
  );
}
