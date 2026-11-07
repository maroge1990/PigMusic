import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Ellipsis,
  EyeOff,
  ListMusic,
  Play,
  Plus,
  Radio,
  Shuffle,
  X,
} from "lucide-react";
import { api } from "./api";
import { focusMenuItem, menuKeys } from "./menuKeys";
import { shuffled } from "./shuffle";
import { usePlayer } from "./Player";
import { SongRow } from "./SongRow";
import { fromMixTrack, type Song } from "./songs";
import type { Track } from "./types";
import "./mixes.css";
import { SectionHeader } from "./PageHeader";

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

/** Each kind has its own pair of Pig-Branding roles (tokens.css). */
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
export function decadeLabel(title = "") {
  const match = /\b(\d{2})(\d)0s\b/.exec(title);
  if (!match) return "";
  return match[1] === "19" ? `${match[2]}0s` : `${match[1]}${match[2]}0s`;
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

async function saveMix(id: string) {
  await api("/v1/mixes/" + id + "/save", { method: "POST" });
  window.dispatchEvent(new Event("music-library-changed"));
}

/** How long a touch must rest on a card before its options open. */
const LONG_PRESS_MS = 500;

/**
 * A Home mix: a click plays it (#56). Its options (Show songs, Shuffle, Save,
 * Hide) open from the ⋯ button, a right-click, or a long press on touch.
 */
function MixCard({
  mix,
  onPlay,
  onShow,
  onSave,
  onHide,
  busy,
}: {
  mix: Mix;
  onPlay: (shuffle: boolean) => void;
  onShow: () => void;
  onSave: () => void;
  onHide: () => void;
  busy: boolean;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const press = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // A long press already opened the options; the click that follows is not a play.
  const swallowClick = useRef(false);
  const [position, setPosition] = useState<{
    left: number;
    top: number;
  }>({ left: 8, top: 8 });
  function open(at?: { x: number; y: number }) {
    const rect = trigger.current!.getBoundingClientRect();
    const width = Math.min(272, window.innerWidth - 16);
    const x = at?.x ?? rect.right - width;
    const y = at?.y ?? rect.bottom + 4;
    setPosition({
      left: Math.max(8, Math.min(x, window.innerWidth - width - 8)),
      top: Math.max(8, Math.min(y, window.innerHeight - 240)),
    });
    // A manual popover: an automatic one would be light-dismissed by the very
    // pointerup that ends a right-click or a long press.
    if (!popup.current?.matches(":popover-open")) popup.current?.showPopover();
    setOpen(true);
    focusMenuItem(popup.current, 0);
  }
  function close(refocus = false) {
    if (popup.current?.matches(":popover-open")) popup.current.hidePopover();
    setOpen(false);
    if (refocus) trigger.current?.focus();
  }
  const [isOpen, setOpen] = useState(false);
  useEffect(() => {
    if (!isOpen) return;
    const outside = (e: PointerEvent) => {
      const target = e.target as Node;
      if (
        !popup.current?.contains(target) &&
        !trigger.current?.contains(target)
      )
        close();
    };
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape") close(true);
    };
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("keydown", escape);
    };
  }, [isOpen]);
  const cancelPress = () => clearTimeout(press.current);
  useEffect(() => cancelPress, []);
  const item = (label: string, icon: ReactNode, action: () => void) => (
    <button
      role="menuitem"
      disabled={busy}
      onClick={() => {
        close();
        action();
      }}
    >
      <span>{label}</span>
      {icon}
    </button>
  );
  const empty = !mix.tracks.length;
  return (
    <div
      className="mix-card-wrap"
      onContextMenu={(e) => {
        e.preventDefault();
        cancelPress();
        open({ x: e.clientX, y: e.clientY });
      }}
    >
      <button
        className="album-card mix-card"
        aria-label={`Play ${mix.title}`}
        title="Right-click or press and hold for more"
        disabled={empty}
        onPointerDown={(e) => {
          if (e.pointerType === "mouse") return;
          swallowClick.current = false;
          const at = { x: e.clientX, y: e.clientY };
          cancelPress();
          press.current = setTimeout(() => {
            swallowClick.current = true;
            open(at);
          }, LONG_PRESS_MS);
        }}
        onPointerUp={cancelPress}
        onPointerLeave={cancelPress}
        onPointerCancel={cancelPress}
        onClick={() => {
          if (swallowClick.current) {
            swallowClick.current = false;
            return;
          }
          onPlay(false);
        }}
      >
        <MixArtwork kind={mix.kind} title={mix.title} />
        <strong>{mix.title}</strong>
        <span>
          {[...new Set(mix.tracks.map((t) => t.artist))]
            .slice(0, 3)
            .join(" · ")}
        </span>
        <small>
          {mix.tracks.length} {mix.tracks.length === 1 ? "song" : "songs"}
        </small>
      </button>
      <button
        ref={trigger}
        className="song-more mix-more"
        aria-label={`Options for ${mix.title}`}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        onClick={() => (isOpen ? close() : open())}
      >
        <Ellipsis size={20} />
      </button>
      <div
        ref={popup}
        popover="manual"
        className="song-menu"
        role="menu"
        aria-label={mix.title}
        style={position}
        onKeyDown={menuKeys}
      >
        <div className="song-menu-group">
          {item("Show songs", <ListMusic size={18} />, onShow)}
          {!empty && item("Shuffle", <Shuffle size={18} />, () => onPlay(true))}
        </div>
        <div className="song-menu-group">
          {item("Save this edition", <Plus size={18} />, onSave)}
          {item("Hide this edition", <EyeOff size={18} />, onHide)}
        </div>
      </div>
    </div>
  );
}

export function Mixes() {
  const [feed, setFeed] = useState<MixFeed | null>(null);
  const [selected, setSelected] = useState<Mix | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const player = usePlayer();
  const [starting, setStarting] = useState(false);
  function startRadio() {
    setStarting(true);
    void api<{ tracks: Track[] }>("/v1/radio")
      .then((batch) => {
        if (batch.tracks.length)
          player.play(batch.tracks.map((t) => fromMixTrack(t, true)));
        else setError("Your radio is still preparing. Try again shortly.");
      })
      .catch((e) => setError(e.message))
      .finally(() => setStarting(false));
  }
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  function dropEdition(id: string) {
    setFeed(
      (value) =>
        value && {
          ...value,
          mixes: value.mixes.filter((mix) => mix.id !== id),
        },
    );
  }
  async function save(mix: Mix) {
    setBusy(true);
    setNotice("");
    try {
      await saveMix(mix.id);
      setNotice(SAVED_NOTICE);
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function hideEdition(mix: Mix) {
    setBusy(true);
    setNotice("");
    try {
      await api("/v1/mixes/" + mix.id + "/hide", { method: "POST" });
      dropEdition(mix.id);
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setBusy(false);
    }
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
  }, [revision]);
  return (
    <section className="home-section" aria-label="Your mixes">
      <SectionHeader
        title="Your mixes"
        subtitle="Familiar favourites. Something new. Made for today."
      />
      {feed?.mixes.length || feed?.radio ? (
        <div className="mix-row">
          {feed.radio && (
            <button
              className="album-card mix-card"
              disabled={starting}
              onClick={startRadio}
            >
              <MixArtwork kind="radio" />
              <strong>{feed.radio.title}</strong>
              <span>Your library and new discoveries</span>
              <small>
                <Radio size={12} aria-hidden="true" /> Endless · Start radio
              </small>
            </button>
          )}
          {feed.mixes.map((mix) => (
            <MixCard
              key={mix.id}
              mix={mix}
              onPlay={(shuffle) => {
                const queue = songs(mix);
                if (queue.length)
                  player.play(shuffle ? shuffled(queue) : queue);
              }}
              onShow={() => setSelected(mix)}
              onSave={() => void save(mix)}
              onHide={() => void hideEdition(mix)}
              busy={busy}
            />
          ))}
        </div>
      ) : (
        <p role="status">
          {error ||
            (feed
              ? feed.preparing
                ? "Preparing your mixes. They'll appear once their audio is ready."
                : "Your mixes appear as your library grows. Discoveries are prepared ahead of playback."
              : "Preparing your mixes…")}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {feed?.preparing && !!feed.mixes.length && (
        <small role="status">
          Preparing more mixes. These are ready to play.
        </small>
      )}
      {selected && (
        <MixDetails
          key={selected.id}
          mix={selected}
          onClose={() => setSelected(null)}
          onChange={() => setRevision((value) => value + 1)}
          onHide={() => {
            dropEdition(selected.id);
            setSelected(null);
          }}
        />
      )}
    </section>
  );
}

function MixDetails({
  mix,
  onClose,
  onChange,
  onHide,
}: {
  mix: Mix;
  onClose: () => void;
  onChange: () => void;
  onHide: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const player = usePlayer();
  const [queue, setQueue] = useState(() => songs(mix));
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    dialog.current?.showModal();
  }, []);
  async function hide(song: Song) {
    try {
      await api("/v1/mixes/hide", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ track_id: song.cache_id }),
      });
      setQueue((current) => current.filter((t) => t.key !== song.key));
      setNotice("Hidden from future mixes.");
      onChange();
    } catch (e) {
      setNotice((e as Error).message);
    }
  }
  return (
    <dialog
      ref={dialog}
      className="mix-dialog"
      onClose={onClose}
      aria-labelledby="mix-title"
    >
      <button
        className="mix-close"
        aria-label="Close mix"
        onClick={() => dialog.current?.close()}
      >
        <X size={20} />
      </button>
      <div className="mix-heading">
        <MixArtwork kind={mix.kind} title={mix.title} />
        <div>
          <small>{mix.day}</small>
          <h2 id="mix-title">{mix.title}</h2>
          <p>{mix.reason}</p>
        </div>
      </div>
      <div className="mix-actions">
        <button
          className="primary-button"
          disabled={!queue.length}
          onClick={() => {
            player.play(queue);
            dialog.current?.close();
          }}
        >
          <Play size={16} /> Play mix
        </button>
        <button
          disabled={saving}
          onClick={() => {
            setSaving(true);
            void saveMix(mix.id)
              .then(() => setNotice(SAVED_NOTICE))
              .catch((e) => setNotice(e.message))
              .finally(() => setSaving(false));
          }}
        >
          Save this edition
        </button>
      </div>
      <details className="mix-options">
        <summary>Mix options</summary>
        <p>Hide this edition until the next daily rotation.</p>
        <button
          disabled={saving}
          onClick={() => {
            setSaving(true);
            void api("/v1/mixes/" + mix.id + "/hide", { method: "POST" })
              .then(onHide)
              .catch((e) => setNotice(e.message))
              .finally(() => setSaving(false));
          }}
        >
          Hide this edition
        </button>
      </details>
      {notice && <p role="status">{notice}</p>}
      <div className="song-list">
        {queue.map((song, index) => (
          <SongRow
            key={song.key}
            song={song}
            queue={queue}
            index={index}
            menu={
              <button role="menuitem" onClick={() => void hide(song)}>
                Hide from mixes
              </button>
            }
          />
        ))}
      </div>
    </dialog>
  );
}
