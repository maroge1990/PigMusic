// What the owner reads about the server's own health on System (#241).
import { formatCount, formatDateTime } from "./format";

/** GET /api/v1/admin/system/health; times are epoch seconds. */
export type SystemHealth = {
  started_at: number;
  previous_run: { at: number; kind: string; sentence: string } | null;
  restarts_last_day: number;
  workers: {
    name: string;
    running: boolean;
    restarts?: number | null;
    last_error?: string | null;
    last_error_at?: number | null;
  }[];
  recent_errors: {
    at: number;
    request_id: string;
    method: string;
    path: string;
    error: string;
    sentence: string;
  }[];
  busy: { count: number; last_at: number | null };
  startup_problems: { at: number | null; area: string; sentence: string }[];
};

const WORKER_NAMES: Record<string, string> = {
  downloads: "Downloads",
  exports: "Exports",
  artists: "Artist updates",
  "source engine": "Soulseek engine",
};

export const workerName = (name: string) =>
  WORKER_NAMES[name] ?? name.charAt(0).toUpperCase() + name.slice(1);

/** "Running", "Restarting" (it failed and waits to run again) or, for the
 * Soulseek engine, "Not connected". */
export function workerState(worker: SystemHealth["workers"][number]) {
  if (worker.running) return "Running";
  if (worker.name === "source engine") return "Not connected";
  return worker.last_error_at || worker.restarts ? "Restarting" : "Stopped";
}

export function workerNote(worker: SystemHealth["workers"][number]) {
  const restarts = worker.restarts ?? 0;
  const parts = [];
  if (restarts)
    parts.push(
      restarts === 1
        ? "restarted once since the server started"
        : `restarted ${formatCount(restarts)} times since the server started`,
    );
  if (worker.last_error_at)
    parts.push(`last went wrong ${formatDateTime(worker.last_error_at)}`);
  return parts.join(" · ");
}

export function restartsLine(health: SystemHealth) {
  const n = health.restarts_last_day;
  if (!n) return "";
  return n === 1
    ? "The server had to restart once in the last day."
    : `The server had to restart ${formatCount(n)} times in the last day.`;
}

export function busyLine(health: SystemHealth) {
  const n = health.busy.count;
  if (!n) return "";
  return n === 1
    ? "The database was too busy to answer once since the server started."
    : `The database was too busy to answer ${formatCount(n)} times since the server started.`;
}

/** True when nothing has gone wrong since the server started. */
export const noProblems = (health: SystemHealth) =>
  health.recent_errors.length === 0 &&
  health.startup_problems.length === 0 &&
  health.busy.count === 0;
