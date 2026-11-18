/** The most songs one request may carry: the server's `Tracks` model
 * (`max_length`), checked against api/openapi.v1.json by
 * tests/limits.test.mjs. */
export const MAX_SONGS_PER_REQUEST = 500;

/** `items` in runs of at most `size`, in order. */
export function batches<T>(items: T[], size = MAX_SONGS_PER_REQUEST): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size)
    out.push(items.slice(i, i + size));
  return out;
}
