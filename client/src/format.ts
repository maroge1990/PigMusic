// One place for dates, relative times and counts (en-AU).
const dateTime = new Intl.DateTimeFormat("en-AU", {
  dateStyle: "medium",
  timeStyle: "short",
});
// Fixed three-letter months: ICU spells some out ("July") and varies by build.
const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];
const relative = new Intl.RelativeTimeFormat("en-AU", { numeric: "auto" });
const count = new Intl.NumberFormat("en-AU");

/** Strings are ISO; numbers are epoch seconds, as the server sends them. */
const toDate = (value: string | number | Date) =>
  value instanceof Date
    ? value
    : new Date(typeof value === "number" ? value * 1000 : value);

/** "9 Oct 2026, 3:06 pm" from an ISO string or epoch seconds. */
export function formatDateTime(value: string | number | Date): string {
  const date = toDate(value);
  return Number.isNaN(date.getTime()) ? "" : dateTime.format(date);
}

/** MusicBrainz partial dates: "2010-07-03", "2010-07", "2010" or "". */
export function formatDate(value?: string | null): string {
  const text = (value || "").trim();
  const m = /^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$/.exec(text);
  if (!m) return text;
  const [, y, mo, d] = m;
  if (!mo) return y;
  const date = new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d || 1)));
  if (date.getUTCMonth() !== Number(mo) - 1) return y;
  const name = `${MONTHS[date.getUTCMonth()]} ${y}`;
  return d ? `${Number(d)} ${name}` : name;
}

/** "26 min ago", "3 h ago", "yesterday", then the date. */
export function formatRelative(
  value: string | number | Date,
  now: number = Date.now(),
): string {
  const date = toDate(value);
  if (Number.isNaN(date.getTime())) return "";
  const seconds = Math.round((now - date.getTime()) / 1000);
  if (seconds < 0 || seconds >= 2 * 86400) return dateTime.format(date);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return relative.format(-1, "day");
}

export const formatCount = (n: number | null | undefined): string =>
  count.format(n ?? 0);

/** "3:07" from seconds. Whole seconds are floored, so 59.6 s reads "0:59",
 * never "0:60". Missing, zero or invalid durations give `empty`. */
export function formatDuration(
  seconds: number | null | undefined,
  empty: string = "",
): string {
  if (!seconds || !Number.isFinite(seconds) || seconds < 0) return empty;
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}
