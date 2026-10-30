/** Account activity as the People page lists it. */
export type Activity = { at: number; display_name?: string; event: string };

const WEEK = 7 * 86400;

/** People whose passwords someone has been guessing this week (D-49): the
 * server records `sign_in_slowed` once a day while it slows down sign-ins to
 * their account, and lists current ones in `warnings` because failed sign-ins
 * can push them out of the activity feed. */
export function guessedAccounts(
  events: Activity[],
  warnings: Activity[] = [],
  now = Math.floor(Date.now() / 1000),
): string[] {
  const names = [...warnings, ...events]
    .filter((e) => e.event === "sign_in_slowed" && e.at >= now - WEEK)
    .map((e) => e.display_name || "an account that no longer exists");
  return [...new Set(names)];
}
