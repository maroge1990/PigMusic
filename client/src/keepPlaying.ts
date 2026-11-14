/** Whether similar songs carry on when a queue ends (#202). The account holds
 * the choice; this copy lets the player read it without asking each time. */
const KEY = "pig-music-keep-playing";
let current: boolean | null = null;

export function keepPlaying(): boolean {
  if (current === null)
    try {
      current = localStorage.getItem(KEY) !== "false";
    } catch {
      current = true;
    }
  return current;
}

export function setKeepPlaying(value: boolean) {
  current = value;
  try {
    localStorage.setItem(KEY, String(value));
  } catch {
    /* Remembered for this visit only. */
  }
}
