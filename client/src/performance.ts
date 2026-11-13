/** The parity targets (#191) as rows: each number beside its target. */
import { formatCount } from "./format";

type Timing = {
  count: number;
  median_ms: number | null;
  p90_ms: number | null;
};
type Downloads = {
  completed: number;
  failed: number;
  cancelled: number;
  completion: number | null;
};
export type PerformanceReport = {
  targets: Record<string, number>;
  instant: {
    taps: number;
    played: number;
    played_share: number | null;
    server_answer: Timing;
  };
  first_sound: Timing;
  downloads: { mix: Downloads; library: Downloads };
  artwork: {
    distinct: number;
    placeholders: number;
    placeholder_share: number | null;
  };
  artists: { with_photo: number; total: number; share: number | null } | null;
};

export type Row = {
  name: string;
  value: number | null | undefined;
  target: number;
  lower?: boolean;
  seconds?: boolean;
  basis: string;
};

export function shown(value: number | null | undefined, seconds?: boolean) {
  if (value === null || value === undefined) return "No data yet";
  return seconds
    ? `${(value / 1000).toFixed(1)} s`
    : `${Math.round(value * 1000) / 10}%`;
}

/** The parity rows (#191): each number beside its target. */
export function performanceRows(report: PerformanceReport): Row[] {
  const { targets, instant, first_sound, downloads, artwork, artists } = report;
  return [
    {
      name: "Taps that play",
      value: instant.played_share,
      target: targets.taps_played,
      basis: `${formatCount(instant.played)} of ${formatCount(instant.taps)} taps`,
    },
    {
      name: "First sound, median",
      value: first_sound.median_ms,
      target: targets.first_sound_median_ms,
      lower: true,
      seconds: true,
      basis: `${formatCount(first_sound.count)} starts`,
    },
    {
      name: "First sound, slowest tenth",
      value: first_sound.p90_ms,
      target: targets.first_sound_p90_ms,
      lower: true,
      seconds: true,
      basis: `${formatCount(first_sound.count)} starts`,
    },
    {
      name: "Mix downloads completed",
      value: downloads.mix.completion,
      target: targets.mix_downloads_completed,
      basis: `${formatCount(downloads.mix.completed)} of ${formatCount(downloads.mix.completed + downloads.mix.failed)}`,
    },
    {
      name: "Library requests completed",
      value: downloads.library.completion,
      target: targets.library_downloads_completed,
      basis: `${formatCount(downloads.library.completed)} of ${formatCount(downloads.library.completed + downloads.library.failed)}`,
    },
    {
      name: "Covers left as placeholders",
      value: artwork.placeholder_share,
      target: targets.artwork_placeholders,
      lower: true,
      basis: `${formatCount(artwork.placeholders)} of ${formatCount(artwork.distinct)} covers`,
    },
    {
      name: "Library artists with a photo",
      value: artists?.share,
      target: targets.artists_with_photo,
      basis: artists
        ? `${formatCount(artists.with_photo)} of ${formatCount(artists.total)}`
        : "",
    },
  ];
}

export function verdict(row: Row) {
  if (row.value === null || row.value === undefined) return "";
  const met = row.lower ? row.value <= row.target : row.value >= row.target;
  return met ? "Meets target" : "Below target";
}
