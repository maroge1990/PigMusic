import { CollectionCard, albumSongs } from "./CollectionMenu";
import { Cover } from "./Cover";
import { SectionHeader } from "./PageHeader";
import { go } from "./routes";
import type { Album } from "./types";

/** A library artist page's albums, each opening its album page. */
export function ArtistAlbums({
  albums,
  hasSongs,
}: {
  albums: Album[];
  /** Songs follow the grid, under their own heading. */
  hasSongs: boolean;
}) {
  return (
    <>
      {albums.length > 0 && <SectionHeader title="Albums" />}
      <div className="personal-grid">
        {albums.map((a) => (
          <CollectionCard
            key={a.id}
            kind="album"
            title={a.title}
            songs={() => albumSongs(a.id)}
          >
            <button onClick={() => go("/library/album/" + a.id)}>
              <Cover title={a.title} src={a.cover_url} />
              {/* A button holds phrasing content only (#97). */}
              <strong className="grid-card-title">{a.title}</strong>
              <span className="grid-card-detail">
                {[
                  a.year ? String(a.year) : "",
                  `${a.track_count} ${a.track_count === 1 ? "song" : "songs"}`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
            </button>
          </CollectionCard>
        ))}
      </div>
      {hasSongs && <SectionHeader title="Songs" />}
    </>
  );
}
