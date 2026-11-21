/** What an Add to library says when a queue limit left songs out (D-128,
 * #242). The server queues the songs that fit and answers the rest
 * `over_limit`, naming the limit; when every song was left out it answers
 * 429 with the code `queue_limit` and the same fields. The sentences live
 * here only, so a change of wording is one edit. */

export type QueueLimit = "person_waiting" | "household_waiting" | "rate";

export type LeftOut = {
  left_out?: number;
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

/** The sentence for songs a limit left out, or "" when none were. */
export function leftOutMessage(result: LeftOut, count = result.left_out ?? 0) {
  if (!count) return "";
  const wording =
    (result.limit && LEFT_OUT_WORDING[result.limit]) ||
    LEFT_OUT_WORDING.person_waiting;
  return wording(songs(count), minutes(result.retry_after ?? 60));
}
