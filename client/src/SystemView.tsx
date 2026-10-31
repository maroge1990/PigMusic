import { lazy, Suspense, useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  GripVertical,
  HardDrive,
  ShieldCheck,
} from "lucide-react";
import { formatCount, formatDateTime } from "./format";
import { StateBadge } from "./StateBadge";
import type { Library } from "./types";
import { SourceDiagnostics } from "./SourceDiagnostics";
import { QualitySettings } from "./QualitySettings";
import { QualitySelect } from "./QualitySelect";
import { sourceIds, sourceName, sources } from "./sources";
import { LibraryScan } from "./LibraryScan";
import { ToolBoundary } from "./ToolBoundary";
import { MixCacheSettings } from "./MixCacheSettings";
const Organisation = lazy(() =>
  import("./Organisation").then((m) => ({ default: m.Organisation })),
);
const RemovedMusic = lazy(() =>
  import("./RemovedMusic").then((m) => ({ default: m.RemovedMusic })),
);
const DuplicateSongs = lazy(() =>
  import("./DuplicateSongs").then((m) => ({ default: m.DuplicateSongs })),
);
const TidyEditions = lazy(() =>
  import("./TidyEditions").then((m) => ({ default: m.TidyEditions })),
);
export type DownloadSettings = {
  enabled: boolean;
  profile: string;
  sources: string[];
  concurrent_downloads: number;
  fallback_profile: string;
  fallback_minutes: number;
  recheck_hours: number;
};

type Health = { configured: boolean; connected: boolean } | null;

export function SystemStatus({
  library,
  downloadSettings,
  soulseekHealth,
  publicOrigin,
}: {
  library: Library | null;
  downloadSettings: DownloadSettings | null;
  soulseekHealth: Health;
  /** undefined while loading, null when none is configured. */
  publicOrigin?: string | null;
}) {
  return (
    <>
      <p className="status-text">
        Soulseek engine:{" "}
        {soulseekHealth
          ? soulseekHealth.connected
            ? "Connected"
            : soulseekHealth.configured
              ? "Unavailable"
              : "Not configured"
          : "Checking connection…"}
      </p>
      <div className="system-grid">
        <section className="system-panel">
          <HardDrive />
          <h2>Synology library</h2>
          <StateBadge state="library" label="Local snapshot ready" />
          <dl>
            <dt>Source</dt>
            <dd>{library?.snapshot.source || "No snapshot loaded"}</dd>
            <dt>Last scan</dt>
            <dd>
              {library?.snapshot.captured_at
                ? formatDateTime(library.snapshot.captured_at)
                : "Not yet"}
            </dd>
            <dt>Indexed tracks</dt>
            <dd>{library ? formatCount(library.stats.tracks) : ""}</dd>
            <dt>Tag read issues</dt>
            <dd>{library?.snapshot.read_failures?.length || 0}</dd>
          </dl>
          <p>
            Browsing uses a local snapshot. Use Scan library to pick up changes
            made outside PigMusic.
          </p>
        </section>
        <section className="system-panel">
          <ShieldCheck />
          <h2>Library storage</h2>
          <StateBadge state="neutral" label="Direct SMB storage" />
          <dl>
            <dt>Downloads</dt>
            <dd>{downloadSettings?.enabled ? "Enabled" : "Paused"}</dd>
            <dt>Tagging & renaming</dt>
            <dd>Existing artist / album folders</dd>
            <dt>Migration &amp; queue</dt>
            <dd>Saved locally</dd>
            <dt>Public address</dt>
            <dd>
              {publicOrigin === undefined
                ? "Checking…"
                : publicOrigin
                  ? new URL(publicOrigin).host
                  : "Not configured"}
            </dd>
          </dl>
          <p>
            Downloads are validated and tagged before publication into your SMB
            library.
          </p>
        </section>
      </div>
      <MixCacheSettings />
    </>
  );
}

/** Drag the grip (mouse or touch) or use the arrows to reorder sources. */
function SourceOrder({
  sources,
  onChange,
}: {
  sources: string[];
  onChange: (sources: string[]) => void;
}) {
  const [dragging, setDragging] = useState<string | null>(null);
  const move = (from: number, to: number) => {
    if (from === to || to < 0 || to >= sources.length) return;
    const next = [...sources];
    next.splice(to, 0, ...next.splice(from, 1));
    onChange(next);
  };
  return (
    <ol className="source-order" aria-label="Download source order">
      {sources.map((source, index) => (
        <li
          key={source}
          data-source={source}
          className={dragging === source ? "dragging" : undefined}
        >
          <span
            className="source-grip"
            aria-hidden="true"
            onPointerDown={(e) => {
              e.preventDefault();
              e.currentTarget.setPointerCapture(e.pointerId);
              setDragging(source);
            }}
            onPointerMove={(e) => {
              if (dragging !== source) return;
              const over = document
                .elementFromPoint(e.clientX, e.clientY)
                ?.closest<HTMLElement>(".source-order li")?.dataset.source;
              if (over && over !== source) move(index, sources.indexOf(over));
            }}
            onPointerUp={() => setDragging(null)}
            onPointerCancel={() => setDragging(null)}
          >
            <GripVertical size={18} />
          </span>
          <span className="source-order-name">
            {index + 1}. {sourceName(source)}
          </span>
          <button
            aria-label={`Move ${sourceName(source)} earlier`}
            disabled={index === 0}
            onClick={() => move(index, index - 1)}
          >
            <ChevronUp size={18} />
          </button>
          <button
            aria-label={`Move ${sourceName(source)} later`}
            disabled={index === sources.length - 1}
            onClick={() => move(index, index + 1)}
          >
            <ChevronDown size={18} />
          </button>
        </li>
      ))}
    </ol>
  );
}

export function DownloadSettingsPanel({
  downloadSettings,
  setDownloadSettings,
  busy,
  save,
  soulseekHealth,
}: {
  soulseekHealth: Health;
  downloadSettings: DownloadSettings;
  setDownloadSettings: (value: DownloadSettings) => void;
  busy: boolean;
  save: () => void;
}) {
  return (
    <section className="system-panel download-panel">
      <h2>Download settings</h2>
      <p className="status-text">
        Soulseek engine:{" "}
        {soulseekHealth
          ? soulseekHealth.connected
            ? "Connected"
            : soulseekHealth.configured
              ? "Unavailable"
              : "Not configured"
          : "Checking connection…"}
      </p>
      <p>
        Files download directly to SMB and publish into your existing artist and
        album folders. Quality is checked on the actual file; quality changes
        always download a new source file and never convert existing files.
      </p>
      <label className="download-option">
        <input
          type="checkbox"
          checked={downloadSettings.enabled}
          onChange={(e) =>
            setDownloadSettings({
              ...downloadSettings,
              enabled: e.target.checked,
            })
          }
        />
        Enable downloads
      </label>
      <label className="download-option">
        Household default quality
        <QualitySelect
          ariaLabel="Download quality"
          value={downloadSettings.profile}
          onChange={(profile) =>
            setDownloadSettings({ ...downloadSettings, profile })
          }
        />
      </label>
      <p className="status-text">
        Lossless files are available from Soulseek and Monochrome. Other sources
        provide the supported formats described below.
      </p>
      {sourceIds.map((source) => (
        <label className="download-option source-option" key={source}>
          <input
            type="checkbox"
            checked={downloadSettings.sources.includes(source)}
            onChange={(e) =>
              setDownloadSettings({
                ...downloadSettings,
                sources: e.target.checked
                  ? [...downloadSettings.sources, source]
                  : downloadSettings.sources.filter((s) => s !== source),
              })
            }
          />
          <span className="source-text">
            {sourceName(source)}
            <small>{sources[source].description}</small>
          </span>
        </label>
      ))}
      <p>
        Sources are tried in the saved order; drag to reorder. SoundCloud
        formats depend on each upload. Monochrome supplies FLAC. Opus and Ogg
        playback depends on your player.
      </p>
      <SourceOrder
        sources={downloadSettings.sources}
        onChange={(sources) =>
          setDownloadSettings({ ...downloadSettings, sources })
        }
      />
      <SourceDiagnostics />
      <QualitySettings
        value={downloadSettings}
        onChange={(value) =>
          setDownloadSettings({ ...downloadSettings, ...value })
        }
      />
      <button className="primary-button" disabled={busy} onClick={save}>
        Save download settings
      </button>
    </section>
  );
}

export function ServerLibraryTools({
  library,
  refresh,
  setToast,
}: {
  library: Library | null;
  refresh: () => void;
  setToast: (message: string) => void;
}) {
  return (
    <>
      <LibraryScan refresh={refresh} />
      <ToolBoundary>
        <Suspense fallback={<p>Loading library tools…</p>}>
          <TidyEditions notify={setToast} />
          <DuplicateSongs notify={setToast} />
          <RemovedMusic notify={setToast} />
          <Organisation notify={setToast} />
        </Suspense>
      </ToolBoundary>
      {!!library?.snapshot.read_failures?.length && (
        <>
          <div className="section-title">
            <h2>Files needing attention</h2>
          </div>
          <div className="read-issues">
            {library.snapshot.read_failures.slice(0, 30).map((f) => (
              <div key={f.path}>
                <strong>{f.path}</strong>
                <span>{f.error}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
}
