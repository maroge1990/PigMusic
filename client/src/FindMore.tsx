import { Search } from "lucide-react";
import { ApiError, api } from "./api";
import { discoverUrl } from "./navigation";
import { useOwner } from "./person";
import type { Artist } from "./types";
import { useState } from "react";
import { InlineError } from "./EmptyState";

type Candidate = { id: string; name: string; disambiguation?: string };

/** The library artist page's way to the catalogue page: "Find more by …".
 * It resolves the catalogue artist, or lets the owner choose between artists
 * that share the name, and stays under Library. */
export function FindMore({ artist }: { artist: Artist }) {
  const owner = useOwner();
  const [error, setError] = useState("");
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [busy, setBusy] = useState(false);
  async function open() {
    setError("");
    try {
      const identity = await api<{ id: string; name: string }>(
        `/v1/catalogue/library-artists/${artist.id}`,
      );
      location.hash = discoverUrl(identity, undefined, "library");
    } catch (e) {
      const found = e instanceof ApiError ? e.body.candidates : undefined;
      if (e instanceof ApiError && e.code === "conflict") {
        setCandidates(Array.isArray(found) ? (found as Candidate[]) : []);
        return;
      }
      setError((e as Error).message);
    }
  }
  async function choose(candidate: Candidate) {
    setBusy(true);
    setError("");
    try {
      const identity = await api<{ id: string; name: string }>(
        `/v1/catalogue/library-artists/${artist.id}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: candidate.id }),
        },
      );
      setCandidates(null);
      location.hash = discoverUrl(identity, undefined, "library");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button className="secondary-button" onClick={() => void open()}>
        <Search size={16} aria-hidden="true" /> Find more by {artist.name}
      </button>
      {(candidates || error) && (
        <div className="find-more-status">
          {candidates &&
            (owner ? (
              <div
                className="artist-choice"
                role="group"
                aria-label="Choose artist"
              >
                <p>
                  Several artists share this name. Choose the one in your
                  library.
                </p>
                <ul>
                  {candidates.map((candidate) => (
                    <li key={candidate.id}>
                      <span>
                        <strong>{candidate.name}</strong>
                        {candidate.disambiguation && (
                          <small> · {candidate.disambiguation}</small>
                        )}
                      </span>
                      <button
                        className="secondary-button"
                        disabled={busy}
                        onClick={() => void choose(candidate)}
                      >
                        Choose
                      </button>
                    </li>
                  ))}
                </ul>
                {!candidates.length && (
                  <p>
                    No matching artists were found. Try the catalogue search.
                  </p>
                )}
              </div>
            ) : (
              <p role="status">
                This artist shares a name with others. The owner can link it to
                the right artist.
              </p>
            ))}
          {error && <InlineError>{error}</InlineError>}
        </div>
      )}
    </>
  );
}
