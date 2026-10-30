/** Account activity as the People page lists it. */
export type Activity = { at: number; display_name?: string; event: string };

const WEEK = 7 * 86400;

/** People whose passwords someone has been guessing this week (D-49): the
 * server records `sign_in_slowed` once a day when it starts slowing down
 * sign-ins to their account. */
export function guessedAccounts(
  events: Activity[],
  now = Math.floor(Date.now() / 1000),
): string[] {
  const names = events
    .filter((e) => e.event === "sign_in_slowed" && e.at >= now - WEEK)
    .map((e) => e.display_name || "an account that no longer exists");
  return [...new Set(names)];
}
