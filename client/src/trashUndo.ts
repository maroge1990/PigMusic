import { api } from "./api";

/** Undo for a removal (D-79, #183): every song it put in Trash comes back.
 * Songs that can't come back are named in the error; they stay in Removed
 * music. */
export async function restoreFromTrash(identities: string[]) {
  const result = await api<{ restored: string[]; failed: string[] }>(
    "/v1/admin/library/trash/restore",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ identities }),
    },
  );
  window.dispatchEvent(new Event("music-library-changed"));
  if (result.failed.length)
    throw new Error(
      `${result.failed.length} of ${identities.length} songs couldn't be put back; they're still in Removed music.`,
    );
}
