import { useEffect, useState, type ReactNode } from "react";
import { ChevronRight, History } from "lucide-react";
import { api } from "./api";
import { Cover } from "./Cover";
import { formatCount, formatLength } from "./format";
import { Empty, InlineError, Loading } from "./EmptyState";
import { MixArtwork, mixCoverKind } from "./Mixes";
import { discoverUrl } from "./navigation";
import { PageHeader } from "./PageHeader";
import { usePlayer } from "./Player";
import { SongRow } from "./SongRow";
import { fromServerTrack } from "./songs";
import type { Track } from "./types";
import "./rewind.css";

export type RewindPeriod = "week" | "month" | "year";
export const rewindPeriods: RewindPeriod[] = ["week", "month", "year"];

type RewindSong = { track: Track; plays: number };
type RewindArtist = {
  name: string;
  plays: number;
  minutes: number;
  id?: string | null;
  musicbrainz_artist_id?: string | null;
  new?: boolean;
  cover_url?: string | null;
};
type RewindAlbum = {
  title: string;
  artist: string;
  plays: number;
  cover_url?: string | null;
  id?: string | null;
  musicbrainz_release_id?: string | null;
};
type RewindReport = {
  period: RewindPeriod;
  start: number;
  end: number;
  timezone: string;
  plays: number;
  minutes: number;
  top_songs: RewindSong[];
  top_artists: RewindArtist[];
  top_albums: RewindAlbum[];
  new_artists: string[];
  top_mixes: {
    title: string;
    plays: number;
    id?: string | null;
    kind?: string | null;
  }[];
  days?: { start: number; minutes: number; plays: number }[];
};

type ListKind = "songs" | "artists" | "albums";
const listKinds: [ListKind, string][] = [
  ["songs", "Songs"],
  ["artists", "Artists"],
  ["albums", "Albums"],
];
const labels: Record<RewindPeriod, string> = {
  week: "Week",
  month: "Month",
  year: "Year",
};
const count = (n: number, one: string, many = one + "s") =>
  `${formatCount(n)} ${n === 1 ? one : many}`;

/** The period a Rewind address asks for; a week unless it names another. */
export function rewindPeriod(value: string | null): RewindPeriod {
  return rewindPeriods.includes(value as RewindPeriod)
    ? (value as RewindPeriod)
    : "week";
}

function useRewind(period: RewindPeriod) {
  const [report, setReport] = useState<RewindReport | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setReport(null);
    setError("");
    void api<RewindReport>("/v1/rewind?period=" + period, {
      signal: controller.signal,
    })
      .then((value) => {
        if (!controller.signal.aborted) setReport(value);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [period]);
  return { report, error };
}

/** The big figure: minutes, then "minutes · N plays". */
function Figure({ report }: { report: RewindReport }) {
  return (
    <div className="rewind-figure">
      <strong className="pig-amount">{formatCount(report.minutes)}</strong>
      <span className="pig-amount">
        {report.minutes === 1 ? "minute" : "minutes"} ·{" "}
        {count(report.plays, "play")}
      </span>
    </div>
  );
}

/** Home: this week at a glance in the page's own look (D-120), opening
 * Rewind. Hidden until a play. */
export function RewindCard() {
  const { report } = useRewind("week");
  if (!report?.plays) return null;
  const figures: [string, string][] = [
    [formatLength(report.minutes * 60, "0 min"), "Listened"],
  ];
  if (report.top_artists[0])
    figures.push([report.top_artists[0].name, "Top artist"]);
  if (report.top_songs[0])
    figures.push([report.top_songs[0].track.title, "Top song"]);
  return (
    <a className="rewind-card" href="#/home/rewind">
      <span className="rewind-card-icon" aria-hidden="true">
        <History size={24} />
      </span>
      <span className="rewind-card-title">
        <strong>Rewind</strong>
        <span>This week</span>
      </span>
      <span className="rewind-card-figures">
        {figures.map(([value, label]) => (
          <span key={label}>
            <strong className={label === "Listened" ? "pig-amount" : ""}>
              {value}
            </strong>
            <span>{label}</span>
          </span>
        ))}
      </span>
      <ChevronRight className="rewind-card-more" size={18} aria-hidden="true" />
    </a>
  );
}

/** One ranked row: rank, cover, name, a bar against number 1, a line. */
function Row({
  rank,
  cover,
  title,
  detail,
  share,
  badge,
}: {
  rank: number;
  cover: ReactNode;
  title: string;
  detail: string;
  share: number;
  badge?: boolean;
}) {
  return (
    <>
      <span className={rank <= 3 ? "rewind-rank top" : "rewind-rank"}>
        {rank}
      </span>
      {cover}
      <span className="rewind-row-text">
        <span className="rewind-row-title">
          <strong>{title}</strong>
          {badge && <span className="rewind-new">New</span>}
        </span>
        <span className="rewind-share" aria-hidden="true">
          <span style={{ width: `${Math.max(4, share * 100)}%` }} />
        </span>
        <small className="pig-amount">{detail}</small>
      </span>
    </>
  );
}

/** A person's listening for the week, month or year so far (#203, #216). */
export function Rewind({ period }: { period: RewindPeriod }) {
  const player = usePlayer();
  const { report, error } = useRewind(period);
  // The chosen list stays while the page is open, across periods.
  const [list, setList] = useState<ListKind>("songs");
  const [opening, setOpening] = useState("");
  // Song n plays n, then the songs ranked after it that can still play.
  const playFrom = (index: number) =>
    player.play(
      (report?.top_songs ?? [])
        .slice(index)
        .filter((entry) => entry.track.available !== false)
        .map((entry) => fromServerTrack(entry.track)),
    );
  // A catalogue release opens on its artist's catalogue page. A release
  // credited to no known artist has no such page (the web has none for a
  // release alone), so Search finds the album by name instead.
  async function openRelease(album: RewindAlbum, releaseId: string) {
    setOpening("");
    try {
      const release = await api<{ artist_id?: string; artist?: string }>(
        "/v1/catalogue/releases/" + releaseId,
      );
      if (!release.artist_id) {
        location.hash =
          "/search?q=" +
          encodeURIComponent(`${album.title} ${album.artist}`.trim());
        return;
      }
      location.hash = discoverUrl(
        { id: release.artist_id, name: release.artist || album.artist },
        releaseId,
        "search",
      );
    } catch (e) {
      setOpening((e as Error).message);
    }
  }
  const songs = report?.top_songs.slice(0, 10) ?? [];
  const artists = report?.top_artists.slice(0, 10) ?? [];
  const albums = report?.top_albums.slice(0, 10) ?? [];
  return (
    <main className="personal-main rewind-page">
      <PageHeader title="Rewind" />
      <nav className="personal-tabs segmented" aria-label="Rewind period">
        {rewindPeriods.map((p) => (
          <a
            key={p}
            href={"#/home/rewind?period=" + p}
            aria-current={p === period ? "page" : undefined}
          >
            {labels[p]}
          </a>
        ))}
      </nav>
      {error ? (
        <InlineError>{error}</InlineError>
      ) : !report ? (
        <Loading>Loading your Rewind…</Loading>
      ) : !report.plays ? (
        <Empty title="Nothing played yet" />
      ) : (
        <>
          <section className="rewind-hero" aria-label="Totals">
            <Figure report={report} />
            {report.top_mixes.length > 0 && (
              <ul className="rewind-mixes" aria-label="Top mixes">
                {report.top_mixes.slice(0, 3).map((mix, i) => (
                  <li key={(mix.id || mix.title) + i} title={mix.title}>
                    <MixArtwork
                      kind={mixCoverKind(mix.kind)}
                      title={mix.title}
                    />
                    <span className="sr-only">
                      {mix.title}, {count(mix.plays, "play")}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <div className="rewind-chips" role="group" aria-label="Show">
            {listKinds.map(([kind, label]) => (
              <button
                type="button"
                key={kind}
                aria-pressed={list === kind}
                onClick={() => setList(kind)}
              >
                {label}
              </button>
            ))}
          </div>
          {opening && <InlineError>{opening}</InlineError>}
          {!{ songs, artists, albums }[list].length && (
            <Empty title="Nothing played yet" />
          )}
          <ol className="rewind-ranked" aria-label={"Top " + list}>
            {list === "songs" &&
              songs.map(({ track, plays }, i) => (
                <li key={track.id + i}>
                  <SongRow
                    song={fromServerTrack(track)}
                    queue={[]}
                    index={i}
                    page="rewind"
                    rank={i + 1}
                    note={count(plays, "play")}
                    unavailable={track.available === false}
                    onPlay={() => playFrom(i)}
                  />
                </li>
              ))}
            {list === "artists" &&
              artists.map((artist, i) => {
                const row = (
                  <Row
                    rank={i + 1}
                    cover={
                      <Cover
                        title={artist.name}
                        src={artist.cover_url}
                        artist
                      />
                    }
                    title={artist.name}
                    detail={count(artist.minutes, "min", "min")}
                    share={artist.minutes / (artists[0].minutes || 1)}
                    badge={artist.new}
                  />
                );
                const href = artist.id
                  ? "#/library/artist/" + encodeURIComponent(artist.id)
                  : artist.musicbrainz_artist_id
                    ? "#" +
                      discoverUrl(
                        {
                          id: artist.musicbrainz_artist_id,
                          name: artist.name,
                        },
                        undefined,
                        "search",
                      )
                    : "";
                return (
                  <li key={artist.name + i}>
                    {href ? (
                      <a className="rewind-row" href={href}>
                        {row}
                      </a>
                    ) : (
                      <div className="rewind-row">{row}</div>
                    )}
                  </li>
                );
              })}
            {list === "albums" &&
              albums.map((album, i) => {
                const row = (
                  <Row
                    rank={i + 1}
                    cover={<Cover title={album.title} src={album.cover_url} />}
                    title={album.title}
                    detail={`${album.artist} · ${count(album.plays, "play")}`}
                    share={album.plays / (albums[0].plays || 1)}
                  />
                );
                const releaseId = album.musicbrainz_release_id;
                return (
                  <li key={album.title + "\u0000" + album.artist + i}>
                    {album.id ? (
                      <a
                        className="rewind-row"
                        href={"#/library/album/" + encodeURIComponent(album.id)}
                      >
                        {row}
                      </a>
                    ) : releaseId ? (
                      <button
                        type="button"
                        className="rewind-row"
                        onClick={() => void openRelease(album, releaseId)}
                      >
                        {row}
                      </button>
                    ) : (
                      <div className="rewind-row">{row}</div>
                    )}
                  </li>
                );
              })}
          </ol>
        </>
      )}
    </main>
  );
}
