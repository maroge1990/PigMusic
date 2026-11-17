import { useState } from "react";
import { encode } from "uqr";
import { expiryText, qrPath } from "./invite";

export type Invite = {
  user_id: string;
  code: string;
  expires_at: number;
  invite_url: string | null;
};

function QrCode({ url, label }: { url: string; label: string }) {
  const { size, d } = qrPath(encode(url, { ecc: "M", border: 0 }).data);
  return (
    <svg
      className="invite-qr"
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={label}
      shapeRendering="crispEdges"
    >
      <rect width={size} height={size} fill="#ffffff" />
      <path d={d} fill="#000000" />
    </svg>
  );
}

/** Shows a one-use invite. Lives in React state only and is never stored. */
export function InvitePanel({
  invite,
  name,
  done,
}: {
  invite: Invite;
  name: string;
  done: () => void;
}) {
  const [status, setStatus] = useState("");
  const url = invite.invite_url;
  async function copy() {
    try {
      await navigator.clipboard.writeText(url as string);
      setStatus("Link copied");
    } catch {
      setStatus("Couldn't copy. Select the link and copy it manually.");
    }
  }
  async function share() {
    try {
      await navigator.share({ title: "Join PigMusic", url: url as string });
    } catch {
      /* cancelled by the person sharing */
    }
  }
  return (
    <section className="invite-panel" aria-label={`Invite for ${name}`}>
      <h3>Invite {name}</h3>
      {url ? (
        <>
          <p>Have them scan this with their camera, or send them the link.</p>
          <QrCode url={url} label={`Invite QR code for ${name}`} />
          <label>
            Invite link
            <input
              readOnly
              value={url}
              onFocus={(e) => e.currentTarget.select()}
            />
          </label>
          <div className="invite-actions">
            <button type="button" onClick={() => void copy()}>
              Copy link
            </button>
            {typeof navigator !== "undefined" && "share" in navigator && (
              <button type="button" onClick={() => void share()}>
                Share
              </button>
            )}
          </div>
          <p role="status">{status}</p>
        </>
      ) : (
        <p>
          Invite links need the server’s public HTTPS address. Set
          PIG_MUSIC_PUBLIC_ORIGIN, or give them the code below.
        </p>
      )}
      <p>{expiryText(invite.expires_at)} (one use).</p>
      <p className="invite-code">
        Setup code: <strong>{invite.code}</strong>
      </p>
      <button type="button" onClick={done}>
        Done
      </button>
    </section>
  );
}
