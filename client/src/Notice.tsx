import { useEffect, useState } from "react";
import { CircleAlert, CircleCheck, RotateCw, Undo2, X } from "lucide-react";
import {
  dismiss,
  folds,
  roleOf,
  runAction,
  stack,
  subscribe,
  type Notice,
} from "./notices";

/**
 * The page's one notice area (#250): every success, Undo and failure shows
 * here, newest last. It sits in the app shell, so a notice outlives the
 * page that raised it.
 */
export function NoticeArea() {
  const [all, setAll] = useState<Notice[]>([]);
  const [busy, setBusy] = useState<number | null>(null);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => subscribe(setAll), []);
  const { shown, more } = stack(all, expanded);
  const folded = stack(all, false).more;
  return (
    <div className={`notice-area${expanded ? " is-expanded" : ""}`}>
      {/* Outside the list, so it stays in view when the list scrolls. */}
      {folded > 0 && (
        <button
          type="button"
          className="notice-more"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "Show fewer" : `${more} more`}
        </button>
      )}
      {/* Retries and warnings: this part shrinks and scrolls. */}
      {shown.some(folds) && (
        <div className="notice-list">{shown.filter(folds).map(card)}</div>
      )}
      {/* Every live Undo and the newest routine notice: never scrolled
          away, whatever the screen's height (Codex round 7). */}
      {shown.some((n) => !folds(n)) && (
        <div className="notice-actions">
          {shown.filter((n) => !folds(n)).map(card)}
        </div>
      )}
    </div>
  );
  function card(notice: Notice) {
    return (
      <div
        key={notice.id}
        className={`notice-${notice.kind} notice`}
        role={roleOf(notice.kind)}
      >
        {notice.kind === "error" ? (
          <CircleAlert size={17} aria-hidden="true" />
        ) : (
          <CircleCheck size={17} aria-hidden="true" />
        )}
        <span className="notice-text">{notice.text}</span>
        {notice.action && (
          <button
            type="button"
            className="notice-action"
            disabled={busy !== null}
            onClick={async () => {
              setBusy(notice.id);
              try {
                await runAction(notice.id);
              } finally {
                setBusy(null);
              }
            }}
          >
            {notice.action.label === "Undo" ? (
              <Undo2 size={16} aria-hidden="true" />
            ) : (
              <RotateCw size={16} aria-hidden="true" />
            )}
            {notice.action.label}
          </button>
        )}
        <button
          type="button"
          className="notice-close"
          aria-label="Close"
          onClick={() => dismiss(notice.id)}
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>
    );
  }
}
