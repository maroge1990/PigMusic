import { apiFetch, detailOr, isUnconfirmed, writeFailure } from "./api";
import { useEffect, useState } from "react";
import { QualitySelect, qualityLabel } from "./QualitySelect";
import { qualityDeferredMessage } from "./queueLimits";
export function AlbumQuality({
  id,
  notify,
  kind = "album",
  embedded = false,
}: {
  id: string;
  notify: (message: string) => void;
  kind?: "album" | "artist";
  embedded?: boolean;
}) {
  const [profile, setProfile] = useState("");
  const [effective, setEffective] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const path = `/api/v1/admin/${kind === "album" ? "albums" : "artists"}/${id}/quality`;
  useEffect(() => {
    const controller = new AbortController();
    void apiFetch(path, { signal: controller.signal })
      .then((r) => r.json())
      .then((data) => {
        if (!controller.signal.aborted) {
          setProfile(data.profile || "");
          setEffective(data.effective_profile);
        }
      })
      .catch(() => {});
    return () => controller.abort();
  }, [path]);
  async function apply() {
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: profile || null }),
      });
      if (!response.ok)
        throw await writeFailure(
          response,
          detailOr("Quality preference unavailable."),
        );
      const data = await response.json();
      const updated = await apiFetch(path).then((r) => r.json());
      setEffective(updated.effective_profile);
      notify(
        [
          `${data.created} replacements queued${data.downloads_enabled ? "" : " · downloads paused"}. Existing music stays until replacement succeeds.`,
          // Past a waiting limit (D-128).
          qualityDeferredMessage(data.deferred),
        ]
          .filter(Boolean)
          .join(" "),
      );
    } catch (e) {
      setError((e as Error).message);
      // It may have been saved: show the preference the server holds now.
      if (isUnconfirmed(e))
        void apiFetch(path)
          .then((r) => r.json())
          .then((now) => {
            setProfile(now.profile || "");
            setEffective(now.effective_profile);
          })
          .catch(() => {});
    } finally {
      setBusy(false);
    }
  }
  const content = (
    <>
      {!embedded && (
        <summary>
          {kind === "album" ? "Album" : "Artist"} quality preference
          {effective ? ` · ${qualityLabel(effective)}` : ""}
        </summary>
      )}
      <p>
        Album preference takes precedence over artist preference, then the
        system default. Changing quality re-downloads matching source files; it
        never converts your music. The previous download is removed only after a
        validated replacement succeeds.
      </p>
      <div className="release-actions">
        <label>
          Preferred quality
          <QualitySelect
            ariaLabel={`${kind === "album" ? "Album" : "Artist"} preferred quality`}
            value={profile}
            onChange={setProfile}
            inheritLabel={
              kind === "album"
                ? "Use artist or household default"
                : "Use household default"
            }
          />
        </label>
        <button className="secondary-button" disabled={busy} onClick={apply}>
          Apply preference &amp; re-download
        </button>
      </div>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
    </>
  );
  return embedded ? (
    <div>{content}</div>
  ) : (
    <details className="album-quality">{content}</details>
  );
}
