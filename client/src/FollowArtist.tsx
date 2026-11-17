import { useEffect, useState } from "react";
import { Bell, BellOff } from "lucide-react";
import { api } from "./api";

/** Follow an artist's future albums into this person's library. */
export function FollowArtist({ id, name }: { id: string; name: string }) {
  const [following, setFollowing] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    void api<{ monitor?: boolean }>(`/v1/artists/${id}/acquisition`)
      .then((state) => setFollowing(!!state.monitor))
      .catch(() => setFollowing(false));
  }, [id]);
  async function toggle() {
    setBusy(true);
    setMessage("");
    try {
      if (following)
        await api(`/v1/artists/${id}/acquisition`, { method: "DELETE" });
      else
        await api(`/v1/artists/${id}/acquisition`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            kinds: ["album"],
            monitor: true,
            request: false,
          }),
        });
      setFollowing(!following);
      setMessage(
        following
          ? `Stopped following ${name}`
          : `Following new albums from ${name}`,
      );
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (following === null) return null;
  return (
    <div className="follow-artist">
      <button
        className="secondary-button"
        aria-pressed={following}
        disabled={busy}
        onClick={() => void toggle()}
      >
        {following ? <BellOff size={16} /> : <Bell size={16} />}
        {following ? "Following" : "Follow new albums"}
      </button>
      {message && <span role="status">{message}</span>}
    </div>
  );
}
