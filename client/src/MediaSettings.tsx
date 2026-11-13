import { apiFetch } from "./api";
import { useEffect, useState } from "react";
type Settings = {
  portraits: boolean;
  remote_playback: boolean;
  fanart_configured: boolean;
  listenbrainz_configured: boolean;
  chart_region: string;
};
const regions: [string, string][] = [
  ["au", "Australia"],
  ["nz", "New Zealand"],
  ["us", "United States"],
  ["gb", "United Kingdom"],
  ["ca", "Canada"],
  ["ie", "Ireland"],
];
export function MediaSettings() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [key, setKey] = useState("");
  const [listenToken, setListenToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    let active = true;
    void apiFetch("/api/v1/admin/settings/media")
      .then((r) => {
        if (!r.ok) throw new Error("Media settings unavailable.");
        return r.json();
      })
      .then((s) => {
        if (active) setSettings(s);
      })
      .catch((e) => {
        if (active) setMessage(e.message);
      });
    return () => {
      active = false;
    };
  }, []);
  async function save(clear?: "fanart" | "listenbrainz") {
    if (!settings) return;
    setBusy(true);
    setMessage("");
    try {
      const response = await apiFetch("/api/v1/admin/settings/media", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          portraits: settings.portraits,
          remote_playback: settings.remote_playback,
          fanart_key: clear === "fanart" ? "" : key || null,
          listenbrainz_token:
            clear === "listenbrainz" ? "" : listenToken || null,
          chart_region: settings.chart_region,
        }),
      });
      const body = await response.json();
      if (!response.ok)
        throw new Error(
          typeof body.detail === "string"
            ? body.detail
            : "Could not save settings.",
        );
      setSettings(body);
      setKey("");
      setListenToken("");
      setMessage("Saved. Library artwork is preloaded in the background.");
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="system-panel download-panel">
      <h2>Artwork &amp; playback</h2>
      <p>
        Artist portraits use Fanart when configured, then ArtistGrid, then album
        artwork. Saved music plays directly from your server.
      </p>
      {settings && (
        <>
          <label className="download-option">
            <input
              type="checkbox"
              checked={settings.portraits}
              onChange={(e) =>
                setSettings({ ...settings, portraits: e.target.checked })
              }
            />
            Artist portraits
          </label>
          <label className="download-option">
            <input
              type="checkbox"
              checked={settings.remote_playback}
              onChange={(e) =>
                setSettings({ ...settings, remote_playback: e.target.checked })
              }
            />
            Experimental remote playback
          </label>
          <p className="field-help">
            Remote listening depends on a third-party service. It does not add a
            song to your library, and audio quality and exact mastering remain
            unverified.
          </p>
          <label className="download-option">
            Fanart API key
            <input
              className="provider-key"
              type="password"
              autoComplete="off"
              aria-label="Fanart API key"
              value={key}
              placeholder={
                settings.fanart_configured
                  ? "Configured · enter a replacement"
                  : "Enter your API key"
              }
              onChange={(e) => setKey(e.target.value)}
            />
          </label>
          <p className="field-help">
            <a
              href="https://fanart.tv/get-an-api-key/"
              target="_blank"
              rel="noreferrer"
            >
              Get a Fanart API key
            </a>
            . The key is stored on your server and never returned to the
            browser. Leave this field blank to keep your existing key.
          </p>
          <label className="download-option">
            ListenBrainz key
            <input
              className="provider-key"
              type="password"
              autoComplete="off"
              aria-label="ListenBrainz key"
              value={listenToken}
              placeholder={
                settings.listenbrainz_configured
                  ? "Configured · enter a replacement"
                  : "Paste your ListenBrainz key"
              }
              onChange={(e) => setListenToken(e.target.value)}
            />
          </label>
          <p className="field-help">
            <a
              href="https://listenbrainz.org/settings/"
              target="_blank"
              rel="noreferrer"
            >
              Get your ListenBrainz key
            </a>{" "}
            (ListenBrainz calls it your user token) to show each artist&rsquo;s
            popular songs and albums. Recommendations work without it. Leave
            blank to keep an existing token. Tokens stay on your server.
          </p>
          <label className="download-option">
            Popular chart region
            <select
              aria-label="Popular chart region"
              value={settings.chart_region}
              onChange={(e) =>
                setSettings({ ...settings, chart_region: e.target.value })
              }
            >
              {regions.map(([code, name]) => (
                <option key={code} value={code}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <div className="import-actions">
            <button
              className="primary-button"
              disabled={busy}
              onClick={() => save()}
            >
              Save artwork &amp; playback
            </button>
            {settings.fanart_configured && (
              <button
                className="secondary-button"
                disabled={busy}
                onClick={() => save("fanart")}
              >
                Remove Fanart key
              </button>
            )}
            {settings.listenbrainz_configured && (
              <button
                className="secondary-button"
                disabled={busy}
                onClick={() => save("listenbrainz")}
              >
                Remove ListenBrainz token
              </button>
            )}
          </div>
        </>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
