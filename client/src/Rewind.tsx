import { useEffect, useState } from "react";
import { Play } from "lucide-react";
import { api } from "./api";
import { Cover } from "./Cover";
import { formatCount } from "./format";
import { InlineError, Loading } from "./EmptyState";
import { PageHeader, SectionHeader } from "./PageHeader";
import { usePlayer } from "./Player";
import { fromServerTrack } from "./songs";
import type { Track } from "./types";
import "./rewind.css";

export type RewindPeriod = "week" | "month" | "year";
export const rewindPeriods: RewindPeriod[] = ["week", "month", "year"];

type RewindReport = {
  period: RewindPeriod;
  start: number;
  end: number;
  timezone: string;
  plays: number;
  minutes: number;
  top_songs: { track: Track; plays: number }[];
  top_artists: { name: string; plays: number; minutes: number }[];
  top_albums: {
    title: string;
    artist: string;
    plays: number;
    cover_url: string | null;
  }[];
  new_artists: string[];
  top_mixes: { title: string; plays: number }[];
};

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

/** A person's listening for the week, month or year so far (#203). */
export function Rewind({ period }: { period: RewindPeriod }) {
  const player = usePlayer();
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
  // A song that can no longer play is listed but left out of the queue.
  const playable = (report?.top_songs ?? [])
    .filter((entry) => entry.track.available !== false)
    .map((entry) => fromServerTrack(entry.track));
  return (
    <main className="personal-main rewind-page">
      <PageHeader
        title="Your Rewind"
        subtitle={`Your listening this ${period} so far.`}
      />
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
        <p role="status">Nothing played yet this {period}.</p>
      ) : (
        <>
          <dl className="rewind-totals">
            <div>
              <dt>Minutes listened</dt>
              <dd className="pig-amount">{formatCount(report.minutes)}</dd>
            </div>
            <div>
              <dt>Listens</dt>
              <dd className="pig-amount">{formatCount(report.plays)}</dd>
            </div>
          </dl>
          {report.top_songs.length > 0 && (
            <section className="home-section">
              <SectionHeader
                title="Top songs"
                actions={
                  <button
                    type="button"
                    className="primary-button"
                    disabled={!playable.length}
                    onClick={() => player.play(playable)}
                  >
                    <Play size={16} fill="currentColor" aria-hidden="true" />
                    Play your top songs
                  </button>
                }
              />
              <ol className="rewind-list">
                {report.top_songs.map(({ track, plays }, i) => (
                  <li key={track.id + i}>
                    <span>
                      <strong>{track.title}</strong>
                      <small>
                        {track.artist}
                        {track.available === false && " · No longer available"}
                      </small>
                    </span>
                    <small className="pig-amount">{count(plays, "play")}</small>
                  </li>
                ))}
              </ol>
            </section>
          )}
          {report.top_artists.length > 0 && (
            <section className="home-section">
              <SectionHeader title="Top artists" />
              <ol className="rewind-list">
                {report.top_artists.map((artist) => (
                  <li key={artist.name}>
                    <strong>{artist.name}</strong>
                    <small className="pig-amount">
                      {count(artist.plays, "play")} ·{" "}
                      {count(artist.minutes, "minute")}
                    </small>
                  </li>
                ))}
              </ol>
            </section>
          )}
          {report.top_albums.length > 0 && (
            <section className="home-section">
              <SectionHeader title="Top albums" />
              <div className="home-grid">
                {report.top_albums.map((album) => (
                  <div
                    className="album-card"
                    key={album.title + "\u0000" + album.artist}
                  >
                    <Cover title={album.title} src={album.cover_url} />
                    <strong>{album.title}</strong>
                    <span className="card-caption">{album.artist}</span>
                    <small className="pig-amount">
                      {count(album.plays, "play")}
                    </small>
                  </div>
                ))}
              </div>
            </section>
          )}
          {report.new_artists.length > 0 && (
            <section className="home-section">
              <SectionHeader title="New to you" />
              <ul className="rewind-list">
                {report.new_artists.map((name) => (
                  <li key={name}>
                    <strong>{name}</strong>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {report.top_mixes.length > 0 && (
            <section className="home-section">
              <SectionHeader title="Mixes you played most" />
              <ol className="rewind-list">
                {report.top_mixes.map((mix) => (
                  <li key={mix.title}>
                    <strong>{mix.title}</strong>
                    <small className="pig-amount">
                      {count(mix.plays, "play")}
                    </small>
                  </li>
                ))}
              </ol>
            </section>
          )}
        </>
      )}
    </main>
  );
}
