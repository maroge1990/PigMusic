/**
 * The newest playing choice, app-wide (Codex round 1 on #267). A menu or
 * button whose songs are still being read (a catalogue album's lookup, a
 * library album's read) plays only if nothing has been played or queued
 * since it was chosen, from any card, row, page or menu: a slow first
 * choice never replaces the queue a later one started.
 */
let latest = 0;

/** A playing choice made now; answers whether it is still the newest. */
export function choosePlay(): () => boolean {
  const mine = ++latest;
  return () => latest === mine;
}

/** Something was played or queued: every choice still reading its songs
 * is out of date. The player calls this on every play and queue. */
export function played() {
  latest++;
}
