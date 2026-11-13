import { useEffect, useState } from "react";

export function catalogueCover(kind: "release" | "release-group", id: string) {
  return `/api/v1/catalogue/artwork/${kind}/${encodeURIComponent(id)}`;
}

/** A catalogue artist's portrait, for search results and artist pages. */
export function catalogueArtistCover(id: string) {
  return `/api/v1/catalogue/artists/${encodeURIComponent(id)}/artwork`;
}

/** The exact address a cover requests at one width; pre-caching uses it too. */
export function coverUrl(src: string, width: number, revision = 0) {
  if (!src.startsWith("/api/v1/")) return src;
  const url = new URL(src, window.location.origin);
  url.searchParams.set("w", String(width));
  url.searchParams.set("v", "1");
  if (!src.startsWith("/api/v1/covers/"))
    url.searchParams.set("r", String(revision));
  return url.pathname + url.search;
}

/** After a missing image, the server fetches it in the background: look
 * again after these delays, then only when the server says something changed. */
const LOOKS_MS = [2000, 6000, 30000];

/** The `r=` an artwork address asks with, and whether the picture at that
 * address failed. The revision moves when the artwork revision changes
 * (`music-artwork-changed`) and, while the picture is missing, for the few
 * early looks above and on each library revision the event stream reports
 * (`music-artwork-retry`). Nothing re-asks on a timer after that, so a
 * picture that will never exist is not requested again and again. `key`
 * names the picture; a new one starts the looks again. */
export function useArtworkRevision(key: string) {
  const [revision, setRevision] = useState(0);
  const [failed, setFailed] = useState("");
  const [looks, setLooks] = useState({ key, count: 0 });
  const missing = failed === `${revision}:${key}`;
  const count = looks.key === key ? looks.count : 0;
  useEffect(() => {
    const changed = () => setRevision((r) => r + 1);
    window.addEventListener("music-artwork-changed", changed);
    return () => window.removeEventListener("music-artwork-changed", changed);
  }, []);
  useEffect(() => {
    if (!missing) return;
    const again = () => setRevision((r) => r + 1);
    const soon =
      count < LOOKS_MS.length
        ? setTimeout(() => {
            setLooks({ key, count: count + 1 });
            again();
          }, LOOKS_MS[count])
        : undefined;
    window.addEventListener("music-artwork-retry", again);
    return () => {
      clearTimeout(soon);
      window.removeEventListener("music-artwork-retry", again);
    };
  }, [missing, count, key]);
  return {
    revision,
    missing,
    /** Call from the image's onError. */
    fail: () => setFailed(`${revision}:${key}`),
  };
}

export function Cover({
  title,
  src,
  artist = false,
  className = "",
  sizes = "(max-width: 480px) 50vw, (max-width: 600px) 33vw, (max-width: 1200px) 25vw, 200px",
}: {
  title: string;
  src?: string | null;
  artist?: boolean;
  className?: string;
  sizes?: string;
}) {
  const artwork = useArtworkRevision(src || "");
  const broken = artwork.missing;
  const image = (width: number) => coverUrl(src || "", width, artwork.revision);
  return (
    <div className={`cover ${artist ? "artist-cover" : ""} ${className}`}>
      {src && !broken ? (
        <img
          src={image(512)}
          srcSet={
            src.startsWith("/api/v1/")
              ? [128, 256, 512, 1024].map((w) => `${image(w)} ${w}w`).join(", ")
              : undefined
          }
          sizes={sizes}
          alt=""
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onError={artwork.fail}
        />
      ) : (
        <>
          <span className="placeholder-halo" aria-hidden="true" />
          <span className="cover-initials" aria-hidden="true">
            {title
              .split(" ")
              .filter(Boolean)
              .slice(0, 2)
              .map((w) => w[0])
              .join("")}
          </span>
        </>
      )}
    </div>
  );
}
