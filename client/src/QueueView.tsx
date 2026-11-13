import { ExternalLink, Music2 } from "lucide-react";
import type { Library } from "./types";
import { Empty, Loading } from "./EmptyState";
import { SectionHeader } from "./PageHeader";
import { sourceName } from "./sources";
type QueueItem = {
  id: string;
  title: string;
  artist: string;
  album: string;
  status: string;
  source?: string;
  error?: string;
  received_bytes?: number;
  total_bytes?: number;
  /** An upload credited only by its uploader's name, held for the owner. */
  source_review?: SourceReview | null;
};

type SourceReview = {
  source: string;
  provider_track_id: string;
  title: string;
  uploader: string;
  duration?: number;
  url?: string | null;
};

export type QueueAction =
  | "retry"
  | "cancel"
  | "source-review/approve"
  | "source-review/reject";

const clock = (seconds?: number) =>
  seconds
    ? `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, "0")}`
    : "";

/** Uploads waiting for a decision. The server only accepts one while the
 * request has stopped, so running searches are left alone. */
function SourceReviews({
  items,
  busy,
  queueAction,
}: {
  items: QueueItem[];
  busy: boolean;
  queueAction: (id: string, action: QueueAction) => void;
}) {
  const waiting = items.filter(
    (i) =>
      i.source_review && ["failed", "waiting_for_sources"].includes(i.status),
  );
  if (!waiting.length) return null;
  return (
    <section className="source-reviews" aria-labelledby="source-reviews">
      <h3 id="source-reviews">Needs review</h3>
      <p>
        These uploads are credited only by the uploader&apos;s name, which
        matches the artist exactly. Official accounts and fan accounts look the
        same, so check before approving.
      </p>
      {waiting.map((item) => {
        const review = item.source_review!;
        return (
          <div className="result-row" key={item.id}>
            <Music2 size={22} aria-hidden="true" />
            <div>
              <strong>{item.title}</strong>
              <span>
                Requested from {item.artist} · uploaded by {review.uploader}
                {review.duration ? ` · ${clock(review.duration)}` : ""}
              </span>
              <span>
                {sourceName(review.source)}: “{review.title}”
                {review.url && (
                  <>
                    {" "}
                    <a href={review.url} target="_blank" rel="noreferrer">
                      Open upload <ExternalLink size={12} aria-hidden="true" />
                    </a>
                  </>
                )}
              </span>
            </div>
            <div className="release-actions">
              <button
                className="small-button"
                disabled={busy}
                onClick={() => queueAction(item.id, "source-review/approve")}
              >
                Approve
              </button>
              <button
                className="danger-text-button"
                disabled={busy}
                onClick={() => queueAction(item.id, "source-review/reject")}
              >
                Reject
              </button>
            </div>
          </div>
        );
      })}
    </section>
  );
}

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
}: {
  queue: Queue | null;
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
              Cancel all…
            </button>
          </>
        }
      />
      {queue && (
        <SourceReviews
          items={queue.items}
          busy={busy}
          queueAction={queueAction}
        />
      )}
      {!queue ? (
        <Loading>Loading requests…</Loading>
      ) : !queue.items.length ? (
        <Empty
          title="Ready when you are"
          description="Submit music from your migration inventory to add requests here."
        />
      ) : (
        queue.items.map((item) => (
          <div className="result-row" key={item.id}>
            <Music2 size={22} />
            <div>
              <strong>{item.title}</strong>
              <span>
                {item.artist} · {item.album}
              </span>
              <span>
                {(
                  {
                    waiting_for_sources: "Waiting",
                    searching: "Searching sources",
                    downloading: "Downloading",
                    validating: "Checking audio and tags",
                    completed: "Downloaded and imported",
                    failed: "Failed",
                    cancelled: "Cancelled",
                  } as Record<string, string>
                )[item.status] || item.status}
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
              {item.error && item.status === "failed" && (
                <span className="inline-error">{item.error}</span>
              )}
            </div>
            {["failed", "cancelled"].includes(item.status) ? (
              <button
                className="small-button"
                disabled={busy}
                onClick={() => queueAction(item.id, "retry")}
              >
                Retry
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
        ))
      )}
    </>
  );
}
