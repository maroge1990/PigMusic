import { useEffect, useState, type ReactNode } from "react";
import { ArrowLeft, Play, Radio, Shuffle } from "lucide-react";
import { api } from "./api";
import { CollectionCard, useCollectionMenu } from "./CollectionMenu";
import { shuffled } from "./shuffle";
import { chooseReplace } from "./playChoice";
import { usePlayer } from "./Player";
import { SongRow } from "./SongRow";
import { fromMixTrack, type Song } from "./songs";
import type { Track } from "./types";
import "./mixes.css";
import { PageHeader, SectionHeader } from "./PageHeader";
import { InlineError, Loading } from "./EmptyState";
import { CoverPlay, ShelfPlaceholder, usePlaceholder } from "./HomeShelf";
import { notify, notifyError } from "./notices";
import { go } from "./routes";

type Mix = {
  id: string;
  kind: string;
  title: string;
  day: string;
  reason: string;
  tracks: Track[];
};
type MixFeed = {
  mixes: Mix[];
  preparing: boolean;
  radio?: { title: string; ready: number } | null;
};

/** Each kind has its own pair of PigBrand roles (tokens.css). */
const KINDS = new Set([
  "daily",
  "artist",
  "genre",
  "popular",
  "back",
  "radio",
  "fresh",
  "releases",
  "decade",
  "deep",
  "recent",
]);

/** "Your 1990s" → "90s"; 2000 onwards keeps the century: "2010s". */
function decadeLabel(title = "") {
  const match = /\b(\d{2})(\d)0s\b/.exec(title);
  if (!match) return "";
  return match[1] === "19" ? `${match[2]}0s` : `${match[1]}${match[2]}0s`;
}

/** The cover kind for a mix kind; anything else (a saved copy's
 * `saved:…`, an unknown or missing kind) takes the daily cover. */
export function mixCoverKind(kind?: string | null) {
  return kind && KINDS.has(kind) ? kind : "daily";
}

/** Cover family: one geometric motif per kind of mix. */
export function MixArtwork({ kind, title }: { kind: string; title?: string }) {
  const palette = KINDS.has(kind) ? kind : "daily";
  const background = `var(--mix-${palette}-background)`;
  const ink = `var(--mix-${palette}-foreground)`;
  return (
    <svg className="mix-artwork" viewBox="0 0 200 200" aria-hidden="true">
      <rect width="200" height="200" fill={background} />
      <g fill="none" stroke={ink} strokeWidth="12">
        {kind === "daily" && (
          <>
            <circle cx="77" cy="90" r="43" />
            <circle cx="123" cy="116" r="43" />
          </>
        )}
        {kind === "artist" && (
          <>
            <circle cx="100" cy="100" r="57" />
            <ellipse
              cx="100"
              cy="100"
              rx="83"
              ry="26"
              transform="rotate(-35 100 100)"
            />
            <circle cx="100" cy="100" r="13" fill={ink} />
          </>
        )}
        {kind === "genre" &&
          [0, 1, 2, 3].map((i) => (
            <path
              key={i}
              d={`M-10 ${65 + i * 26} Q40 ${5 + i * 26} 100 ${65 + i * 26} T210 ${65 + i * 26}`}
            />
          ))}
        {kind === "popular" &&
          [0, 1, 2, 3].map((i) => (
            <rect
              key={i}
              x={35 + i * 36}
              y={122 - i * 24}
              width="20"
              height={45 + i * 24}
              fill={ink}
              stroke="none"
            />
          ))}
        {kind === "back" && (
          <path
            d="M95 57 L42 100 L95 143 Z M156 57 L103 100 L156 143 Z"
            fill={ink}
            stroke="none"
          />
        )}
        {kind === "radio" && (
          <>
            <circle cx="56" cy="144" r="12" fill={ink} stroke="none" />
            {[45, 80, 115].map((r) => (
              <path
                key={r}
                d={`M${56} ${144 - r} A${r} ${r} 0 0 1 ${56 + r} 144`}
              />
            ))}
          </>
        )}
        {kind === "fresh" &&
          [0, 45, 90, 135].map((angle) => (
            <path
              key={angle}
              d="M100 38 V162"
              transform={`rotate(${angle} 100 100)`}
            />
          ))}
        {kind === "releases" && (
          <>
            <circle cx="100" cy="100" r="62" />
            <circle cx="100" cy="100" r="34" />
            <circle cx="100" cy="100" r="9" fill={ink} stroke="none" />
            <circle cx="160" cy="42" r="14" fill={ink} stroke="none" />
          </>
        )}
        {kind === "decade" && (
          <>
            <path d="M40 84 A60 60 0 0 1 160 84" />
            <path d="M30 168 H170" />
            <text
              x="100"
              y="146"
              textAnchor="middle"
              fill={ink}
              stroke="none"
              fontSize={decadeLabel(title).length > 3 ? 50 : 64}
              fontWeight="800"
              letterSpacing="-2"
            >
              {decadeLabel(title) || "—"}
            </text>
          </>
        )}
        {kind === "deep" && (
          <>
            <rect x="38" y="38" width="124" height="124" />
            <rect x="68" y="68" width="64" height="64" />
            <rect x="92" y="92" width="16" height="16" fill={ink} />
          </>
        )}
        {kind === "recent" && (
          <>
            <rect x="40" y="70" width="70" height="70" />
            <rect x="90" y="50" width="70" height="70" fill={background} />
            <path d="M125 68 V102 M108 85 H142" />
          </>
        )}
      </g>
      <path d="M18 182 H182" stroke={ink} strokeWidth="2" opacity=".5" />
      <circle cx="181" cy="19" r="5" fill={ink} />
    </svg>
  );
}

function songs(mix: Mix): Song[] {
  return mix.tracks.map((t) => fromMixTrack(t));
}

const SAVED_NOTICE =
  "Playlist saved. Add songs to your library to keep their audio after this mix leaves rotation.";

/** Save and Hide this edition live only in a mix's menu (D-118). */
function mixActions(mix: Mix, hidden: () => void) {
  return {
    save: () =>
      void api("/v1/mixes/" + mix.id + "/save", { method: "POST" })
        .then(() => {
          notify(SAVED_NOTICE);
          window.dispatchEvent(new Event("music-library-changed"));
        })
        .catch((e) => notifyError((e as Error).message)),
    hide: () =>
      void api("/v1/mixes/" + mix.id + "/hide", { method: "POST" })
        .then(() => {
          notify(`${mix.title} is hidden until the next daily rotation.`);
          hidden();
        })
        .catch((e) => notifyError((e as Error).message)),
  };
}

/** Where a mix's page lives. */
export const mixUrl = (id: string) => "/home/mix/" + encodeURIComponent(id);

export function Mixes() {
  const [feed, setFeed] = useState<MixFeed | null>(null);
  const [error, setError] = useState("");
  const player = usePlayer();
  const [starting, setStarting] = useState(false);
  function startRadio() {
    // A replacing choice, counted from the tap: only a later one wins,
    // whichever answer comes first (playChoice.ts, Codex round 2 on #267).
    const newest = chooseReplace();
    setStarting(true);
    void api<{ tracks: Track[] }>("/v1/radio")
      .then((batch) => {
        if (!newest()) return;
        if (batch.tracks.length)
          player.play(batch.tracks.map((t) => fromMixTrack(t, true)));
        else setError("Your radio is still preparing. Try again shortly.");
      })
      .catch((e) => {
        if (newest()) setError(e.message);
      })
      .finally(() => setStarting(false));
  }
  function dropEdition(id: string) {
    setFeed(
      (value) =>
        value && {
          ...value,
          mixes: value.mixes.filter((mix) => mix.id !== id),
        },
    );
  }
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const load = () => {
      if (document.hidden) {
        timer = setTimeout(load, 30000);
        return;
      }
      void api<MixFeed>("/v1/home/mixes", { signal: controller.signal })
        .then((value) => {
          if (!controller.signal.aborted) {
            setFeed(value);
            setError("");
            timer = setTimeout(load, value.preparing ? 30000 : 300000);
          }
        })
        .catch((e) => {
          if (!controller.signal.aborted) {
            setError(e.message);
            timer = setTimeout(load, 60000);
          }
        });
    };
    load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, []);
  const something = !!(feed?.mixes.length || feed?.radio);
  // Shown only with something to play; while the first answer or the first
  // mixes are on their way, at most a quiet placeholder row (D-120).
  const placeholder = usePlaceholder(
    "mixes",
    !something && !error && (!feed || !!feed.preparing),
  );
  if (!something)
    return placeholder ? <ShelfPlaceholder label="Mixes" /> : null;
  return (
    <section className="home-section" aria-label="Mixes">
      <SectionHeader title="Mixes" />
      {feed && (
        <div className="mix-row">
          {feed.radio && (
            <button
              className="album-card mix-card"
              disabled={starting}
              onClick={startRadio}
            >
              <MixArtwork kind="radio" />
              <strong>{feed.radio.title}</strong>
              <small>
                <Radio size={12} aria-hidden="true" /> Endless · Start radio
              </small>
            </button>
          )}
          {feed.mixes.map((mix) => (
            <CollectionCard
              key={mix.id}
              kind="mix"
              title={mix.title}
              className="mix-card-wrap"
              songs={async () => songs(mix)}
              mix={mixActions(mix, () => dropEdition(mix.id))}
            >
              {/* A mix opens as its page, like an album (D-118). */}
              <a
                className="album-card mix-card"
                href={"#" + mixUrl(mix.id)}
                title="Right-click or press and hold for more"
              >
                <MixArtwork kind={mix.kind} title={mix.title} />
                <strong>{mix.title}</strong>
              </a>
              <CoverPlay title={mix.title} songs={async () => songs(mix)} />
            </CollectionCard>
          ))}
        </div>
      )}
      {error && <InlineError>{error}</InlineError>}
    </section>
  );
}

/** A mix's own page (#249): Play, Shuffle and the mix's menu over its songs. */
export function MixPage({ id }: { id: string }) {
  const [mix, setMix] = useState<Mix | null | undefined>(undefined);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void api<MixFeed>("/v1/home/mixes", { signal: controller.signal })
      .then((feed) => setMix(feed.mixes.find((m) => m.id === id) ?? null))
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [id]);
  const back = (
    <a className="text-button back-link" href="#/home">
      <ArrowLeft size={16} aria-hidden="true" /> Home
    </a>
  );
  if (error || mix === null)
    return (
      <main className="personal-main">
        {back}
        <InlineError>
          {error || "This mix has left rotation. Pick another from Home."}
        </InlineError>
      </main>
    );
  if (!mix)
    return (
      <main className="personal-main">
        {back}
        <Loading>Loading your mix…</Loading>
      </main>
    );
  return <MixDetails key={mix.id} mix={mix} back={back} />;
}

function MixDetails({ mix, back }: { mix: Mix; back: ReactNode }) {
  const player = usePlayer();
  const [queue, setQueue] = useState(() => songs(mix));
  const { menu, parts } = useCollectionMenu({
    kind: "mix",
    title: mix.title,
    songs: async () => queue,
    mix: mixActions(mix, () => go("/home")),
    buttonClass: "round-action",
  });
  async function hide(song: Song) {
    try {
      await api("/v1/mixes/hide", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ track_id: song.cache_id }),
      });
      setQueue((current) => current.filter((t) => t.key !== song.key));
      notify("Hidden from future mixes.");
    } catch (e) {
      notifyError((e as Error).message);
    }
  }
  return (
    <main className="personal-main mix-page" {...menu.target}>
      {back}
      <PageHeader
        cover={
          <div className="cover">
            <MixArtwork kind={mix.kind} title={mix.title} />
          </div>
        }
        eyebrow={mix.day}
        title={mix.title}
        subtitle={mix.reason}
      />
      <div className="release-actions">
        <button
          className="primary-button"
          disabled={!queue.length}
          onClick={() => player.play(queue, 0)}
        >
          <Play size={16} fill="currentColor" aria-hidden="true" /> Play
        </button>
        <button
          className="round-action"
          aria-label="Shuffle"
          title="Shuffle"
          disabled={!queue.length}
          onClick={() => player.play(shuffled(queue), 0)}
        >
          <Shuffle size={18} aria-hidden="true" />
        </button>
        {parts}
      </div>
      <div className="song-list">
        {queue.map((song, index) => (
          <SongRow
            key={song.key}
            song={song}
            queue={queue}
            index={index}
            page="mix"
            onHide={() => void hide(song)}
          />
        ))}
      </div>
    </main>
  );
}
