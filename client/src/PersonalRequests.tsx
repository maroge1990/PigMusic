import { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { api } from "./api";
import { Cover, catalogueCover } from "./Cover";
import { DownloadLog } from "./DownloadLog";
import { Empty, InlineError } from "./EmptyState";
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
type Phase = "active" | "waiting" | "done" | "failed";
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

// Plain-language request states; the server keeps its technical names.
const labels: Record<string, [string, Phase]> = {
  waiting_for_sources: ["Waiting for a source", "waiting"],
  searching: ["Looking for a copy", "active"],
  downloading: ["Downloading to the server", "active"],
  validating: ["Checking the file", "active"],
  importing: ["Adding to the library", "active"],
  completed: ["In your library", "done"],
  downloaded: ["In your library", "done"],
  cancelled: ["Cancelled", "failed"],
  failed: ["Couldn't get this song", "failed"],
};

const badge = {
  waiting: "requested",
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
  if (group.active) parts.push(`${group.active} downloading`);
  if (group.waiting) parts.push(`${group.waiting} waiting`);
  if (group.failed) parts.push(`${group.failed} failed`);
  return parts.join(" · ");
}

const groupLabel: Record<Phase, string> = {
  active: "Downloading",
  waiting: "Waiting",
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
  const [notice, setNotice] = useState("");
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
      setNotice(
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
    setNotice("");
    try {
      for (const item of items)
        await api("/v1/requests/" + encodeURIComponent(item.id) + "/retry", {
          method: "POST",
        });
      window.dispatchEvent(new Event("music-requests-changed"));
      await load();
    } catch (e) {
      setError((e as Error).message);
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
  return (
    <section className="request-groups">
      {error && <InlineError>{error}</InlineError>}
      {notice && <p role="status">{notice}</p>}
      {page && !page.total && (
        <Empty
          title="No downloads"
          description="Songs and albums you download from Search or an artist page appear here until they reach your library."
        />
      )}
      {page && page.total > 0 && (
        <>
          {(offset > 0 || page.next_offset !== null) && (
            <nav className="collection-pages" aria-label="Request pages">
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
          {page.groups.map((group) => {
            const failed = group.items.filter((i) =>
              ["failed", "cancelled"].includes(i.status),
            );
            return (
              <details className="request-group" key={group.id}>
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
                          {item.request_progress !== null &&
                            tone === "active" && (
                              <progress
                                max={1}
                                value={item.request_progress}
                                aria-label={`Downloading ${item.title} to the server, ${Math.round(item.request_progress * 100)} percent`}
                              />
                            )}
                          {tone === "failed" && item.error && (
                            <p className="inline-error">
                              {friendlyRequestError(item.error)}
                            </p>
                          )}
                          <DownloadLog
                            path={
                              "/v1/requests/" +
                              encodeURIComponent(item.id) +
                              "/log"
                            }
                            title={item.title}
                          />
                        </div>
                        <StateBadge
                          state={badge[tone]}
                          label={label}
                          iconOnly
                        />
                      </li>
                    );
                  })}
                </ol>
                {failed.length > 0 && (
                  <button
                    className="small-button"
                    disabled={busy}
                    aria-label={`Retry ${failed.length === 1 ? failed[0].title : `${failed.length} songs from ${group.title}`}`}
                    onClick={() => void retry(failed)}
                  >
                    {failed.length === 1
                      ? "Retry"
                      : `Retry ${failed.length} songs`}
                  </button>
                )}
              </details>
            );
          })}
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
              <p className="muted">
                Removes finished, failed and cancelled entries from this list.
                Active downloads and music files are kept.
              </p>
            </div>
          )}
        </>
      )}
    </section>
  );
}
