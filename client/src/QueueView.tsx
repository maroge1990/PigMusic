import { Cover, catalogueCover } from "./Cover";
import { StateBadge } from "./StateBadge";
import type { Library } from "./types";
import { Empty, Loading } from "./EmptyState";
import { SectionHeader } from "./PageHeader";
import { DownloadLog } from "./DownloadLog";
import { sourceName } from "./sources";
type QueueItem = {
  id: string;
  title: string;
  artist: string;
  album: string;
  status: string;
  source?: string;
  error?: string;
  updated_at?: string;
  received_bytes?: number;
  total_bytes?: number;
  /** Names this very entry for Cancel all (#183). */
  incarnation?: string;
  musicbrainz_release_id?: string | null;
  /** Who asked, by person id; the first asked first. */
  requested_by?: string[];
};

const badges: Record<string, "requested" | "active" | "failed" | "paused"> = {
  waiting_for_sources: "requested",
  searching: "active",
  downloading: "active",
  validating: "active",
  failed: "failed",
  cancelled: "failed",
  paused: "paused",
};

export type QueueAction = "retry" | "cancel";

export type Queue = {
  library_revision: string;
  artwork_revision?: number;
  media?: Library["media"];
  downloads_enabled: boolean;
  items: QueueItem[];
};
export function QueueView({
  queue,
  busy,
  clearQueue,
  queueAction,
  people = {},
}: {
  queue: Queue | null;
  /** Display names by person id, for who asked. */
  people?: Record<string, string>;
  busy: boolean;
  /** `finished` clears done, failed and cancelled entries (the Clear
   * everyone has); otherwise every request is cancelled and removed. */
  clearQueue: (finished: boolean) => void;
  queueAction: (id: string, action: QueueAction) => void;
}) {
  return (
    <>
      <SectionHeader
        title="Download queue"
        subtitle={
          queue?.downloads_enabled
            ? "Downloads go directly to your SMB library."
            : "Downloads are paused. Enable them in Download settings below."
        }
        actions={
          <>
            <button
              className="secondary-button"
              disabled={
                busy ||
                !queue?.items.some((t) =>
                  ["completed", "downloaded", "cancelled", "failed"].includes(
                    t.status,
                  ),
                )
              }
              onClick={() => clearQueue(true)}
            >
              Clear finished
            </button>
            <button
              className="danger-text-button"
              disabled={busy || !queue?.items.length}
              onClick={() => clearQueue(false)}
            >
              Cancel all
            </button>
          </>
        }
      />
      {!queue ? (
        <Loading>Loading downloads…</Loading>
      ) : !queue.items.length ? (
        <Empty
          title="Ready when you are"
          description="Submit music from your migration inventory to add songs here."
        />
      ) : (
        queue.items.map((item) => {
          const label =
            (
              {
                waiting_for_sources: "Waiting",
                searching: "Searching sources",
                downloading: "Downloading",
                validating: "Checking audio and tags",
                completed: "Downloaded and imported",
                failed: "Couldn't add",
                cancelled: "Cancelled",
                paused: "Paused",
              } as Record<string, string>
            )[item.status] || item.status;
          const share =
            item.status === "downloading" && item.total_bytes
              ? Math.min(1, (item.received_bytes || 0) / item.total_bytes)
              : null;
          const askers = (item.requested_by || [])
            .map((id) => people[id])
            .filter(Boolean);
          return (
            <div className="result-row queue-row" key={item.id}>
              <Cover
                title={item.album || item.title}
                src={
                  item.musicbrainz_release_id
                    ? catalogueCover("release", item.musicbrainz_release_id)
                    : null
                }
                sizes="48px"
              />
              <div>
                <strong>{item.title}</strong>
                <span>
                  {item.artist} · {item.album}
                </span>
                <span className="queue-status">
                  {item.status !== "completed" && badges[item.status] && (
                    <StateBadge state={badges[item.status]} label={label} />
                  )}
                  {(item.status === "completed" || !badges[item.status]) &&
                    label}
                  {item.source && ` · ${sourceName(item.source)}`}
                </span>
                {item.status === "downloading" && (
                  <span className="pig-amount">
                    {Math.round((item.received_bytes || 0) / 1024)} KB
                    {item.total_bytes
                      ? ` / ${Math.round(item.total_bytes / 1024)} KB`
                      : ""}
                  </span>
                )}
                {share !== null && (
                  <progress
                    max={1}
                    value={share}
                    aria-label={`${item.title}: ${Math.round(share * 100)} percent`}
                  />
                )}
                {item.error && item.status === "failed" && (
                  <span className="inline-error">{item.error}</span>
                )}
                <DownloadLog
                  path={`/v1/admin/queue/${encodeURIComponent(item.id)}/log`}
                  title={item.title}
                  version={`${item.status}|${item.updated_at ?? ""}`}
                />
              </div>
              <span className="queue-asker">{askers.join(", ")}</span>
              {["failed", "cancelled"].includes(item.status) ? (
                <button
                  className="small-button"
                  disabled={busy}
                  onClick={() => queueAction(item.id, "retry")}
                >
                  Try again
                </button>
              ) : (
                item.status !== "completed" && (
                  <button
                    className="small-button"
                    disabled={busy}
                    onClick={() => queueAction(item.id, "cancel")}
                  >
                    Cancel
                  </button>
                )
              )}
            </div>
          );
        })
      )}
    </>
  );
}
