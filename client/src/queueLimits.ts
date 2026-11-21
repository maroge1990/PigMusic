/** What an Add to library says when a queue limit left songs out (D-128,
 * #242). The server queues the songs that fit; the rest are `excluded` in
 * `outcomes`, with their positions in `over_limit` and the limit named.
 * When every song was left out it answers 429 with the code `queue_limit`
 * and the same fields. The limit and `retry_after` drive what the client
 * does; the words are the same for every limit (D-131), and live here only. */

export type QueueLimit = "person_waiting" | "household_waiting" | "rate";

export type LeftOut = {
  left_out?: number;
  /** Positions, in the songs sent, of those a limit left out. */
  over_limit?: number[];
  limit?: QueueLimit | null;
  retry_after?: number | null;
};

const songs = (n: number) => `${n} ${n === 1 ? "song" : "songs"}`;

/** Songs a limit left out of an Add (D-131). */
export const LEFT_OUT = (count: string) =>
  `Couldn't add ${count}: too many are waiting. Try again later.`;

/** A Retry in Downloads or the queue a waiting limit refused (D-131). */
export const RETRY_REFUSED = "Too many songs are waiting. Try again later.";

/** The sentence for songs a limit left out, or "" when none were. */
export function leftOutMessage(result: LeftOut, count = result.left_out ?? 0) {
  return count ? LEFT_OUT(songs(count)) : "";
}

/** Of `sent`, those a limit left out (to send again), by position. */
export function leftOutOf<T>(sent: T[], result: LeftOut): T[] {
  const positions = new Set(result.over_limit ?? []);
  return sent.filter((_, i) => positions.has(i));
}
