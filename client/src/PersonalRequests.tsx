import { notify } from "./notices";
import { useEffect, useRef, useState } from "react";
import { ChevronDown, CircleAlert, RotateCw } from "lucide-react";
import { ApiError, api } from "./api";
import { RETRY_REFUSED } from "./queueLimits";
import { Cover, catalogueCover } from "./Cover";
import { DownloadLog } from "./DownloadLog";
import { Empty, InlineError, Loading } from "./EmptyState";
import { SectionHeader } from "./PageHeader";
import { formatRelative } from "./format";
import { friendlyRequestError } from "./requestError";
import { sourceName } from "./sources";
import { StateBadge } from "./StateBadge";

type Item = {
  id: string;
  title: string;
  artist?: string;
  status: string;
  track_number?: number;
  request_progress: number | null;
  error?: string | null;
  source?: string | null;
  quality?: string | null;
  updated_at?: string | null;
};
type Phase = "active" | "waiting" | "paused" | "done" | "failed";
type Group = {
  id: string;
  kind: "album" | "song";
  title: string;
  artist: string;
  release_id?: string | null;
  state: Phase;
  songs: number;
  done: number;
  active: number;
  waiting: number;
  failed: number;
  paused?: number;
  progress: number;
  requested_at: string;
  updated_at: string;
  items: Item[];
};
type GroupPage = {
  groups: Group[];
  total: number;
  next_offset: number | null;
};

const PAGE = 50;
/** Rows a section shows before "Show all". */
const SHOWN = 5;
const WEEK_MS = 7 * 24 * 60 * 60 * 1000;

type Section = { id: string; title: string; groups: Group[] };

/** Adding first, then what couldn't be added, then what arrived. */
function sections(groups: Group[], now = Date.now()): Section[] {
  const recent = (g: Group) => now - Date.parse(g.updated_at) < WEEK_MS;
  const done = groups.filter((g) => g.state === "done");
  return [
    {
      id: "adding",
      title: "Adding",
      groups: groups.filter((g) =>
        ["active", "waiting", "paused"].includes(g.state),
      ),
    },
    {
      id: "failed",
      title: "Couldn't add",
      groups: groups.filter((g) => g.state === "failed"),
    },
    { id: "week", title: "Added this week", groups: done.filter(recent) },
    {
      id: "earlier",
      title: "Added earlier",
      groups: done.filter((g) => !recent(g)),
    },
  ].filter((s) => s.groups.length);
}

// Plain-language request states; the server keeps its technical names.
const labels: Record<string, [string, Phase]> = {
  waiting_for_sources: ["Adding", "waiting"],
  searching: ["Looking for a copy", "active"],
  downloading: ["Adding", "active"],
  validating: ["Checking the file", "active"],
  importing: ["Adding to the library", "active"],
  completed: ["In your library", "done"],
  downloaded: ["In your library", "done"],
  cancelled: ["Cancelled", "failed"],
  paused: ["Paused", "paused"],
  failed: ["Couldn't add", "failed"],
};

const badge = {
  waiting: "requested",
  paused: "requested",
  active: "active",
  done: "library",
  failed: "failed",
} as const;

/** One line for a request: how far it got, in words. */
function summary(group: Group) {
  if (group.songs === 1) {
    const item = group.items[0];
    return (labels[item.status] || [item.status.replaceAll("_", " ")])[0];
  }
  const parts = [`${group.done} of ${group.songs} songs in your library`];
  if (group.active) parts.push(`${group.active} adding`);
  if (group.waiting) parts.push(`${group.waiting} waiting`);
  if (group.failed) parts.push(`${group.failed} couldn't be added`);
  if (group.paused) parts.push(`${group.paused} paused`);
  return parts.join(" · ");
}

const groupLabel: Record<Phase, string> = {
  active: "Adding",
  waiting: "Waiting",
  paused: "Paused",
  done: "In your library",
  failed: "Needs attention",
};

/** Downloads, one card per request: an album request is one card with its
 * songs inside, a lone song its own card. Source and quality show when a
 * card is opened. */
export function PersonalRequests() {
  const readSerial = useRef(0);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<GroupPage | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // What is open survives a request moving to another group (#197).
  const [openRows, setOpenRows] = useState<Set<string>>(new Set());
  const toggle = (id: string, open: boolean) =>
    setOpenRows((now) => {
      if (now.has(id) === open) return now;
      const next = new Set(now);
      if (open) next.add(id);
      else next.delete(id);
      return next;
    });
  const load = (at = offset) => {
    const serial = ++readSerial.current;
    return api<GroupPage>(`/v1/requests/groups?offset=${at}&limit=${PAGE}`)
      .then((data) => {
        if (serial === readSerial.current) setPage(data);
      })
      .catch((e) => {
        if (serial === readSerial.current) setError(e.message);
      });
  };
  async function clear() {
    ++readSerial.current;
    setBusy(true);
    setError("");
    try {
      const result = await api<{ cleared: number }>("/v1/requests", {
        method: "DELETE",
      });
      setOffset(0);
      await load(0);
      notify(
        `Cleared ${result.cleared} finished ${result.cleared === 1 ? "song" : "songs"}. Your music files are unchanged.`,
      );
      window.dispatchEvent(new Event("music-requests-changed"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function retry(items: Item[]) {
    ++readSerial.current;
    setBusy(true);
    setError("");
    try {
      for (const item of items)
        await api("/v1/requests/" + encodeURIComponent(item.id) + "/retry", {
          method: "POST",
        });
      window.dispatchEvent(new Event("music-requests-changed"));
      await load();
    } catch (e) {
      // A waiting limit refused it (D-128): the song stays as it was.
      if (e instanceof ApiError && e.code === "queue_limit") {
        setError(RETRY_REFUSED);
        window.dispatchEvent(new Event("music-requests-changed"));
      } else setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    const changed = () => void load();
    void load();
    window.addEventListener("music-requests-changed", changed);
    return () => {
      ++readSerial.current;
      window.removeEventListener("music-requests-changed", changed);
    };
  }, [offset]);
  const finished = page?.groups.some((g) => g.done || g.failed);
  function renderGroup(group: Group, retryOnRow: boolean) {
    const failed = group.items.filter((i) =>
      ["failed", "cancelled"].includes(i.status),
    );
    return (
      <div className="download-row" key={group.id}>
        <details
          className="request-group"
          open={openRows.has(group.id)}
          onToggle={(event) => toggle(group.id, event.currentTarget.open)}
        >
          <summary>
            <Cover
              title={group.title}
              src={
                group.release_id
                  ? catalogueCover("release", group.release_id)
                  : null
              }
            />
            <div className="request-group-text">
              <strong>{group.title}</strong>
              <p>
                {[group.artist, formatRelative(group.updated_at)]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
              <p>{summary(group)}</p>
              {group.state !== "done" && (
                <progress
                  max={1}
                  value={group.progress}
                  aria-label={`${group.title}: ${Math.round(group.progress * 100)} percent`}
                />
              )}
            </div>
            <StateBadge
              state={badge[group.state]}
              label={groupLabel[group.state]}
              iconOnly
            />
            <ChevronDown
              size={18}
              className="request-group-chevron"
              aria-hidden="true"
            />
          </summary>
          <ol className="request-group-songs">
            {group.items.map((item) => {
              const [label, tone] = labels[item.status] || [
                item.status.replaceAll("_", " "),
                "waiting",
              ];
              return (
                <li key={item.id}>
                  <div>
                    <span>{item.title}</span>
                    <small>
                      {[
                        label,
                        item.source ? sourceName(item.source) : "",
                        item.quality || "",
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </small>
                    {item.request_progress !== null && tone === "active" && (
                      <progress
                        max={1}
                        value={item.request_progress}
                        aria-label={`Adding ${item.title}, ${Math.round(item.request_progress * 100)} percent`}
                      />
                    )}
                    {tone === "failed" && item.error && (
                      <p className="inline-error">
                        {friendlyRequestError(item.error)}
                      </p>
                    )}
                    <DownloadLog
                      path={
                        "/v1/requests/" + encodeURIComponent(item.id) + "/log"
                      }
                      title={item.title}
                      version={`${item.status}|${item.updated_at ?? ""}`}
                      startOpen={openRows.has("log:" + item.id)}
                      onOpenChange={(open) => toggle("log:" + item.id, open)}
                    />
                  </div>
                  <StateBadge state={badge[tone]} label={label} iconOnly />
                </li>
              );
            })}
          </ol>
          {failed.length > 0 && !retryOnRow && (
            <button
              className="small-button"
              disabled={busy}
              aria-label={`Try again: ${failed.length === 1 ? failed[0].title : `${failed.length} songs from ${group.title}`}`}
              onClick={() => void retry(failed)}
            >
              <RotateCw size={14} aria-hidden="true" />
              {failed.length === 1
                ? "Try again"
                : `Try again (${failed.length} songs)`}
            </button>
          )}
        </details>
        {failed.length > 0 && retryOnRow && (
          <button
            className="secondary-button download-retry"
            disabled={busy}
            aria-label={`Try again: ${group.title}`}
            onClick={() => void retry(failed)}
          >
            <RotateCw size={16} aria-hidden="true" />
            Try again
          </button>
        )}
      </div>
    );
  }
  return (
    <section className="request-groups">
      {error && <InlineError>{error}</InlineError>}
      {!page && !error && <Loading />}
      {page && !page.total && (
        <Empty
          title="No downloads"
          description="Songs and albums you add from Search or an artist page appear here until they reach your library."
        />
      )}
      {page && page.total > 0 && (
        <>
          {(offset > 0 || page.next_offset !== null) && (
            <nav className="collection-pages" aria-label="Download pages">
              <button
                className="secondary-button"
                disabled={busy || offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE))}
              >
                Previous
              </button>
              <span>
                {offset + 1}–{Math.min(offset + PAGE, page.total)} of{" "}
                {page.total}
              </span>
              <button
                className="secondary-button"
                disabled={busy || page.next_offset === null}
                onClick={() => setOffset(page.next_offset!)}
              >
                Next
              </button>
            </nav>
          )}
          {sections(page.groups).map((section) => (
            <section
              className="download-section"
              data-section={section.id}
              key={section.id}
              aria-labelledby={"downloads-" + section.id}
            >
              <SectionHeader
                id={"downloads-" + section.id}
                title={
                  <>
                    {section.id === "failed" && (
                      <CircleAlert size={18} aria-hidden="true" />
                    )}
                    {section.title}
                    <span className="download-count pig-amount">
                      {section.groups.length}
                    </span>
                  </>
                }
              />
              {(expanded.has(section.id)
                ? section.groups
                : section.groups.slice(0, SHOWN)
              ).map((group) => renderGroup(group, section.id === "failed"))}
              {section.groups.length > SHOWN && !expanded.has(section.id) && (
                <button
                  className="text-button"
                  onClick={() =>
                    setExpanded((now) => new Set(now).add(section.id))
                  }
                >
                  Show all {section.groups.length}
                </button>
              )}
            </section>
          ))}
          {/* The one Clear: finished entries only, the same for everyone. */}
          {finished && (
            <div className="release-actions request-clear">
              <button
                className="secondary-button"
                disabled={busy}
                onClick={() => void clear()}
              >
                Clear finished
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
