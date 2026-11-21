import { apiFetch, peekCatalogue } from "./api";
import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Cover, catalogueCover } from "./Cover";
import { SongRow } from "./SongRow";
import { withLibrary, type Song as Row } from "./songs";
import { popularRows, type PopularSong } from "./catalogueAlbum";
type Group = { id: string; title: string; date: string; type: string };
type Song = PopularSong;
export function ArtistPopular({
  id,
  openAlbum,
  songsOnly = false,
}: {
  id: string;
  openAlbum: (album: Group) => void;
  songsOnly?: boolean;
}) {
  const path = `/api/v1/catalogue/artists/${id}/popularity`;
  const cached = peekCatalogue<{ songs: Song[]; albums: Group[] }>(path);
  // The same rows the artist's card plays (#267).
  const toRows = (songs: Song[]): Row[] => popularRows(id, songs);
  const [data, setData] = useState<{ songs: Song[]; albums: Group[] } | null>(
    cached || null,
  );
  const [rows, setRows] = useState<Row[]>(() => toRows(cached?.songs || []));
  const [songPage, setSongPage] = useState(0);
  const [albumPage, setAlbumPage] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setData(cached || null);
    setRows(toRows(cached?.songs || []));
    setSongPage(0);
    setAlbumPage(0);
    void (async () => {
      try {
        const response = await apiFetch(
          `/api/v1/catalogue/artists/${id}/popularity`,
          { signal: controller.signal },
        );
        if (response.ok) {
          const body = await response.json();
          if (controller.signal.aborted) return;
          setData(body);
          const plain = toRows(body.songs);
          setRows(plain);
          const found = await withLibrary(plain);
          if (!controller.signal.aborted) setRows(found);
        }
      } catch {
        /* Optional enrichment never blocks discography. */
      }
    })();
    return () => controller.abort();
  }, [id]);
  if (!data || (!data.songs.length && !data.albums.length)) return null;
  const pager = (
    label: string,
    page: number,
    size: number,
    total: number,
    setPage: (n: number) => void,
  ) => (
    <div className="popular-pager">
      <span aria-live="polite">
        {page * size + 1}–{Math.min((page + 1) * size, total)} of {total}
      </span>
      <button
        className="icon-button"
        aria-label={`Previous popular ${label}`}
        disabled={page === 0}
        onClick={() => setPage(page - 1)}
      >
        <ChevronLeft size={18} />
      </button>
      <button
        className="icon-button"
        aria-label={`Next popular ${label}`}
        disabled={(page + 1) * size >= total}
        onClick={() => setPage(page + 1)}
      >
        <ChevronRight size={18} />
      </button>
    </div>
  );
  return (
    <section className="artist-popular" aria-label="Popular music">
      {!!data.songs.length && (
        <>
          <div className="section-title">
            <h2>Popular songs</h2>
            {pager("songs", songPage, 5, data.songs.length, setSongPage)}
          </div>
          <div className="song-list">
            {rows.slice(songPage * 5, songPage * 5 + 5).map((row, i) => (
              <SongRow
                key={row.key}
                song={row}
                queue={rows}
                index={songPage * 5 + i}
                onArtist
                page="chart"
              />
            ))}
          </div>
        </>
      )}
      {!songsOnly && !!data.albums.length && (
        <>
          <div className="section-title">
            <h2>Popular albums</h2>
            {pager("albums", albumPage, 3, data.albums.length, setAlbumPage)}
          </div>
          <div className="popular-albums">
            {data.albums
              .slice(albumPage * 3, albumPage * 3 + 3)
              .map((album) => (
                <button
                  className="album-card"
                  key={album.id}
                  onClick={() => openAlbum(album)}
                >
                  <Cover
                    title={album.title}
                    src={catalogueCover("release-group", album.id)}
                  />
                  <div className="card-name">{album.title}</div>
                  <div className="card-caption">{album.date.slice(0, 4)}</div>
                </button>
              ))}
          </div>
        </>
      )}
    </section>
  );
}
