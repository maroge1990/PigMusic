/**
 * Which choice to replace the queue is the newest, app-wide (Codex rounds 1
 * and 2 on #267).
 *
 * Replacing the queue (Play, Shuffle, a row tap, Start radio, Play library,
 * a mix's or a card's Play) is one kind of choice. A choice whose songs are
 * still being read registers at the gesture, before any read, and plays
 * only if no replacing choice came after it, however the reads finish. A
 * replacing play made at once (a row tap, a page's Play) counts from the
 * moment it plays.
 *
 * Adding to the queue (Play next, Add to queue) is not a replacing choice:
 * it never cancels a pending replacement and is never cancelled by one. It
 * goes into whichever queue is playing when it is made (or, for a card,
 * when its songs arrive); a replacement that lands later replaces that
 * queue, the addition with it.
 */
let latest = 0;

/** A replacing choice made now; answers whether it is still the newest. */
export function chooseReplace(): () => boolean {
  const mine = ++latest;
  return () => latest === mine;
}

/** The queue was replaced at once: every replacing choice still reading
 * its songs is out of date. The player calls this on every replacing play. */
export function replaced() {
  latest++;
}
