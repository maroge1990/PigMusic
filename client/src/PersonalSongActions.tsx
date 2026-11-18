import { PlaylistPicker, addToPlaylist } from "./PlaylistPicker";
import { useEffect, useState } from "react";
import { Star } from "lucide-react";
import { api } from "./api";
import { songBody, type Song } from "./songs";
/** Favourite and playlist actions for the playing song. The payload keeps the
 * recording and release identity, so one edition never stands in for another. */
export function PersonalSongActions({
  song,
  hidePlaylist = false,
}: {
  song: Song;
  hidePlaylist?: boolean;
}) {
  const [favourite, setFavourite] = useState(false);
  const [error, setError] = useState("");
  const body = songBody(song);
  const key = JSON.stringify(body);
  useEffect(() => {
    void api<{ favourited: boolean }>("/v1/favourites", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track: body }),
    })
      .then((d) => setFavourite(d.favourited))
      .catch((e) => setError(e.message));
  }, [key]);
  return (
    <>
      <button
        aria-label={
          favourite
            ? `Remove ${song.title} from favourites`
            : `Favourite ${song.title}`
        }
        onClick={() =>
          void api<{ favourited: boolean }>("/v1/favourites", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ track: body, favourite: !favourite }),
          })
            .then((d) => {
              setFavourite(d.favourited);
              window.dispatchEvent(new Event("music-library-changed"));
            })
            .catch((e) => setError(e.message))
        }
      >
        <Star size={18} fill={favourite ? "currentColor" : "none"} />
      </button>
      {!hidePlaylist && (
        <PlaylistPicker
          button
          title={song.title}
          add={(id) => addToPlaylist(id, [body])}
        />
      )}
      {error && (
        <span className="inline-error" role="alert">
          {error}
        </span>
      )}
    </>
  );
}
