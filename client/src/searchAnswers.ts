/** Search answers by query, reused while fresh so backspacing or retyping
 * sends no request. They belong to the signed-in person: every account change
 * empties them, and an answer that arrives after one is dropped. Library
 * answers are also dropped when the library changes. */
import { currentSession, sessionMap } from "./api";
import type { Track } from "./types";

export type CatalogueArtist = {
  id: string;
  name: string;
  description?: string;
};
export type Local = { tracks: Track[]; household: Track[] };
type Answer = { time: number; local?: Local; catalogue?: CatalogueArtist[] };

const ANSWER_LIFETIME_MS = 5 * 60_000;
const answers = sessionMap<string, Answer>();

window.addEventListener("music-library-changed", () => {
  for (const answer of answers.values()) delete answer.local;
});

export function answer(key: string, now = Date.now()) {
  const found = answers.get(key);
  if (found && now - found.time > ANSWER_LIFETIME_MS) {
    answers.delete(key);
    return undefined;
  }
  return found;
}

/** Keep part of an answer, unless the session that asked has ended. */
export function remember(
  key: string,
  session: number,
  update: (entry: Answer) => void,
  now = Date.now(),
) {
  if (session !== currentSession()) return false;
  if (!answers.has(key) && answers.size >= 60)
    answers.delete(answers.keys().next().value!);
  const entry = answers.get(key) || { time: now };
  update(entry);
  answers.set(key, entry);
  return true;
}
