import type { Playlist } from "./libraryTypes";

/** Rename or delete a playlist, below its songs. */
export function PlaylistOptions({
  playlist,
  rename,
  remove,
}: {
  playlist: Playlist;
  rename: (name: FormDataEntryValue | null) => void;
  /** Deletes the playlist once the person has confirmed. */
  remove: () => void;
}) {
  return (
    <details className="playlist-options">
      <summary>Playlist options</summary>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          rename(new FormData(e.currentTarget).get("name"));
        }}
      >
        <label>
          Playlist name
          <input
            name="name"
            defaultValue={playlist.name}
            required
            maxLength={100}
          />
        </label>
        <button type="submit">Rename</button>
      </form>
      <button
        onClick={() => {
          if (
            confirm("Delete this playlist? Music files stay in your library.")
          )
            remove();
        }}
      >
        Delete playlist
      </button>
    </details>
  );
}
