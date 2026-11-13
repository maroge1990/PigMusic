/** How long a start took to be heard, for the parity targets (#191): from
 * the tap (or skip) until the audio element first plays that song. */

/** "next": the queue moved on by itself when a song ended. */
export type StartAction = "play" | "skip" | "previous" | "next";
export type StartSource = "library" | "mix" | "remote" | "other";

/** The server's kind of source for a player's source label. */
export function startSource(label?: string | null): StartSource {
  if (!label) return "other";
  if (label === "Saved library") return "library";
  if (label === "Your mix" || label === "Prepared mix") return "mix";
  if (label.startsWith("Remote")) return "remote";
  return "other";
}

/** One start at a time: a newer start replaces an unheard one, and each is
 * reported once. */
export class StartClock {
  private pending?: {
    request: number;
    action: StartAction;
    at: number;
    asked: boolean;
  };

  begin(request: number, action: StartAction, now: number) {
    this.pending = { request, action, at: now, asked: false };
  }

  /** The player asked the audio element to play this start's song. */
  asked(request: number) {
    if (this.pending?.request === request) this.pending.asked = true;
  }

  /** A pause after the song was asked to play and before it was heard:
   * the person's wait, not the server's, so the start is not reported.
   * The pause that ends the previous song comes before the ask and is not
   * one. */
  paused(request: number) {
    if (this.pending?.request === request && this.pending.asked)
      this.pending = undefined;
  }

  /** The start heard now, if `request` is the one waiting; else null. */
  heard(request: number, now: number) {
    const pending = this.pending;
    if (!pending || pending.request !== request) return null;
    this.pending = undefined;
    return {
      ms: Math.max(0, Math.round(now - pending.at)),
      action: pending.action,
    };
  }

  /** A start that failed is never reported. */
  cancel(request: number) {
    if (this.pending?.request === request) this.pending = undefined;
  }
}
