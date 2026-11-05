import { apiFetch } from "./api";
import { useState } from "react";

type Song = {
  id: string;
  title: string;
  album: string;
  quality: string;
  /** Why a song stays for review, when it is not just unique to its edition. */
  reason?: string;
};
type Album = {
  id: string;
  artist: string;
  keep: { title: string; songs: number };
  editions: {
    album_id: string;
    title: string;
    year?: number | null;
    songs: number;
  }[];
  remove: (Song & { kept: Song })[];
  replace: (Song & { replaces: Song })[];
  /** Songs of an album split across editions, each moving onto the kept
   * album at its own track number. */
  join?: (Song & {
    album_id: string;
    to: { track_number: number; disc_number: number };
  })[];
  leftovers: Song[];
  fingerprint: string;
};
type Done = {
  id: string;
  artist: string;
  title: string;
  status: string;
  error?: string | null;
  /** Changes an earlier version made without recording which tidy-up made
   * them: the owner says whether each belongs to this one. */
  unresolved?: {
    id: string;
    kind: string;
    title: string;
    album: string;
    state: string;
  }[];
};

/** Albums the library has on several editions, tidied to the fullest. */
export function TidyEditions({ notify }: { notify: (s: string) => void }) {
  const [albums, setAlbums] = useState<Album[] | null>(null);
  const [history, setHistory] = useState<Done[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  /** Refresh the review; a tidy-up or Undo that just failed keeps its
   * `failure` on screen. */
  async function review(failure = "") {
    setBusy(true);
    setError(failure);
    try {
      const r = await apiFetch("/api/v1/admin/library/tidy");
      if (!r.ok) throw new Error("Edition review unavailable.");
      const d: { albums: Album[]; history: Done[] } = await r.json();
      setAlbums(d.albums);
      setHistory(d.history);
    } catch (e) {
      setError([failure, (e as Error).message].filter(Boolean).join(" "));
    } finally {
      setBusy(false);
    }
  }
  async function post(path: string, body?: object) {
    const r = await apiFetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || "Stopped. Check the library folder.");
    }
  }
  async function tidy(album: Album) {
    const moved = album.remove.length + album.replace.length * 2;
    const joined = album.join?.length ?? 0;
    const changes = [
      moved ? `move ${moved} files to Removed music` : "",
      joined ? `join ${joined} songs into it` : "",
    ].filter(Boolean);
    if (
      !confirm(
        `Keep ${album.keep.title} and ${changes.join(" and ")}? Listeners keep their songs, and you can undo this.`,
      )
    )
      return;
    setBusy(true);
    setError("");
    let failure = "";
    try {
      await post("/api/v1/admin/library/tidy/" + album.id, {
        fingerprint: album.fingerprint,
      });
      notify(`${album.artist} · ${album.keep.title} tidied.`);
    } catch (e) {
      failure = (e as Error).message;
    }
    // A stopped tidy-up may still have changed files.
    window.dispatchEvent(new Event("music-library-changed"));
    await review(failure);
  }
  async function undo(done: Done) {
    setBusy(true);
    setError("");
    let failure = "";
    try {
      await post(`/api/v1/admin/library/tidy/history/${done.id}/undo`);
      notify(`${done.artist} · ${done.title} put back.`);
    } catch (e) {
      failure = (e as Error).message;
    }
    window.dispatchEvent(new Event("music-library-changed"));
    await review(failure);
  }
  async function resolve(done: Done, id: string, include: boolean) {
    setBusy(true);
    setError("");
    let failure = "";
    try {
      await post(
        `/api/v1/admin/library/tidy/history/${done.id}/unresolved/${id}`,
        {
          include,
        },
      );
    } catch (e) {
      failure = (e as Error).message;
    }
    await review(failure);
  }
  return (
    <section className="system-panel">
      <h2>Album editions</h2>
      <p>
        Albums you have on more than one edition, such as an album and its
        deluxe edition. The fullest edition is kept, with the best copy of each
        song. Singles, EPs, live albums and greatest hits stay separate.
      </p>
      <button
        className="secondary-button"
        disabled={busy}
        onClick={() => void review()}
      >
        Find editions
      </button>
      {albums && !albums.length && <p>No albums on several editions.</p>}
      {!!albums?.length && (
        <div className="organisation-list">
          {albums.map((album) => (
            <div key={album.id}>
              <h3>
                {album.artist} · {album.keep.title}
              </h3>
              <p>
                Keep {album.keep.title} ({album.keep.songs} songs); tidy{" "}
                {album.editions
                  .map((e) => `${e.title} (${e.songs} songs)`)
                  .join(", ")}
                .
              </p>
              {album.editions.map((edition) => {
                const songs = (album.join ?? []).filter(
                  (s) => s.album_id === edition.album_id,
                );
                if (!songs.length) return null;
                return (
                  <div key={"join-" + edition.album_id}>
                    <p>
                      Join {songs.length} songs from {edition.title}
                      {edition.year ? ` (${edition.year})` : ""}
                    </p>
                    {songs.map((s) => (
                      <div className="result-row" key={s.id}>
                        <div>
                          <strong>{s.title}</strong>
                          <span>
                            {s.quality} becomes track{" "}
                            {s.to.disc_number > 1
                              ? `${s.to.disc_number}-${s.to.track_number}`
                              : s.to.track_number}{" "}
                            of {album.keep.title}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                );
              })}
              {album.replace.map((s) => (
                <div className="result-row" key={s.id}>
                  <div>
                    <strong>{s.title}</strong>
                    <span>
                      {s.quality} from {s.album} replaces {s.replaces.quality}
                    </span>
                  </div>
                </div>
              ))}
              {album.remove.map((s) => (
                <div className="result-row" key={s.id}>
                  <div>
                    <strong>{s.title}</strong>
                    <span>
                      Removed from {s.album} · {s.kept.quality} kept
                    </span>
                  </div>
                </div>
              ))}
              {album.leftovers.map((s) => (
                <div className="result-row" key={s.id}>
                  <div>
                    <strong>{s.title}</strong>
                    <span>
                      Stays on {s.album}
                      {s.reason ? ` (${s.reason})` : ""}: review it yourself
                    </span>
                  </div>
                </div>
              ))}
              <button
                className="primary-button"
                disabled={
                  busy ||
                  !(
                    album.remove.length +
                    album.replace.length +
                    (album.join?.length ?? 0)
                  )
                }
                onClick={() => void tidy(album)}
              >
                Tidy {album.keep.title}
              </button>
            </div>
          ))}
        </div>
      )}
      {!!history.length && (
        <>
          <h3>Tidied albums</h3>
          {history.map((done) => (
            <div key={done.id}>
              <div className="result-row">
                <div>
                  <strong>{done.title}</strong>
                  <span>
                    {done.artist}
                    {done.status === "applied" ? "" : " · stopped part-way"}
                    {done.error ? `: ${done.error}` : ""}
                  </span>
                </div>
                <button
                  className="secondary-button"
                  disabled={busy}
                  onClick={() => void undo(done)}
                >
                  Undo
                </button>
              </div>
              {done.unresolved?.map((change) => (
                <div className="result-row" key={change.id}>
                  <div>
                    <strong>{change.title}</strong>
                    <span>
                      {change.album} · {change.state}. Part of this tidy-up?
                    </span>
                  </div>
                  <button
                    className="secondary-button"
                    disabled={busy}
                    onClick={() => void resolve(done, change.id, true)}
                  >
                    Include in Undo
                  </button>
                  <button
                    className="secondary-button"
                    disabled={busy}
                    onClick={() => void resolve(done, change.id, false)}
                  >
                    Not part of it
                  </button>
                </div>
              ))}
            </div>
          ))}
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
