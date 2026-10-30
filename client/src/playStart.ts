/** What to tell the person when the browser refuses to start a song's audio,
 * or "" when there is nothing to say. A skip or a new song makes the browser
 * abort the previous play() call, and that late rejection must not cover
 * the song now playing with "Tap Play…" (#80). */
export function startFailure(error: unknown, superseded: boolean): string {
  if (superseded || (error as { name?: string } | null)?.name === "AbortError")
    return "";
  return "Tap Play to start audio on this device.";
}
