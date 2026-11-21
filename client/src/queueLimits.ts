/** What an Add to library says when a queue limit left songs out (D-128,
 * #242). The server queues the songs that fit; the rest are `excluded` in
 * `outcomes`, with their positions in `over_limit` and the limit named.
 * When every song was left out it answers 429 with the code `queue_limit`
 * and the same fields. The sentences live here only, so a change of
 * wording is one edit. */

export type QueueLimit = "person_waiting" | "household_waiting" | "rate";

export type LeftOut = {
  left_out?: number;
  /** Positions, in the songs sent, of those a limit left out. */
  over_limit?: number[];
  limit?: QueueLimit | null;
  retry_after?: number | null;
};

const songs = (n: number) => `${n} ${n === 1 ? "song" : "songs"}`;

/** Minutes, rounded up: a wait under a minute reads as one. */
function minutes(seconds: number) {
  const n = Math.max(1, Math.ceil(seconds / 60));
  return `${n} ${n === 1 ? "minute" : "minutes"}`;
}

export const LEFT_OUT_WORDING: Record<
  QueueLimit,
  (count: string, wait: string) => string
> = {
  person_waiting: (count) =>
    `Couldn't add ${count}: you already have 2,000 songs waiting. Try again once some are added.`,
  household_waiting: (count) =>
    `Couldn't add ${count}: the server already has 10,000 songs waiting. Try again once some are added.`,
  rate: (count, wait) =>
    `Couldn't add ${count}: that's over 1,000 songs in 10 minutes. Try again in ${wait}.`,
};

/** Retry in Downloads or the queue: the job waits for everyone who asked
 * for it, so the person's limit may be another person's. */
export const RETRY_WORDING: Record<QueueLimit, string> = {
  person_waiting:
    "Couldn't try again yet: too many songs are already waiting for the people who asked for this. Try again once some are added.",
  household_waiting:
    "Couldn't try again yet: the server already has 10,000 songs waiting. Try again once some are added.",
  rate: "Couldn't try again yet. Try again in a few minutes.",
};

/** A quality choice whose replacements didn't all fit, or "". */
export function qualityDeferredMessage(deferred = 0) {
  return deferred
    ? `${songs(deferred)} will change quality later: too many songs are waiting. Choose this quality again once some are added.`
    : "";
}

/** The owner's import or an artist's albums: songs a waiting limit left out. */
export function bulkLeftOutMessage(leftOut = 0, again: string) {
  return leftOut
    ? `${songs(leftOut)} left out: too many songs are waiting. ${again} once some are added.`
    : "";
}

/** The sentence for songs a limit left out, or "" when none were. */
export function leftOutMessage(result: LeftOut, count = result.left_out ?? 0) {
  if (!count) return "";
  const wording =
    (result.limit && LEFT_OUT_WORDING[result.limit]) ||
    LEFT_OUT_WORDING.person_waiting;
  return wording(songs(count), minutes(result.retry_after ?? 60));
}

/** The sentence for a Retry a waiting limit refused. */
export function retryLimitMessage(result: LeftOut) {
  return (
    (result.limit && RETRY_WORDING[result.limit]) ||
    RETRY_WORDING.person_waiting
  );
}

/** Of `sent`, those a limit left out (to send again), by position. */
export function leftOutOf<T>(sent: T[], result: LeftOut): T[] {
  const positions = new Set(result.over_limit ?? []);
  return sent.filter((_, i) => positions.has(i));
}
