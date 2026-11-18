import { expectedSession, sessionHeader } from "./sessionId";
import { askToConfirm } from "./confirmDialog";
import { notify } from "./notices";
import { useEffect, useState } from "react";
import { api } from "./api";
import { formatDateTime } from "./format";
import type { Person } from "./AppShell";
import { InvitePanel, type Invite } from "./InvitePanel";
import { guessedAccounts, type Activity } from "./accountAlerts";
import { EmptyLine, InlineError } from "./EmptyState";

type InboxAlbum = {
  id: string;
  title: string;
  artist: string;
  tracks: { id: string }[];
};

function json(body: unknown) {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

/** Shared loading/notice state for the administrator account screens. */
function useAdminData(inboxOffset = 0) {
  const [people, setPeople] = useState<Person[]>([]);
  const [events, setEvents] = useState<Activity[]>([]);
  const [warnings, setWarnings] = useState<Activity[]>([]);
  const [inbox, setInbox] = useState<InboxAlbum[]>([]);
  const [inboxNext, setInboxNext] = useState<number | null>(null);
  const [error, setError] = useState("");
  async function load() {
    setPeople((await api<{ people: Person[] }>("/v1/people")).people);
    const activity = await api<{ events: Activity[]; warnings?: Activity[] }>(
      "/v1/people/activity",
    );
    setEvents(activity.events);
    setWarnings(activity.warnings ?? []);
    const page = await api<{
      albums: InboxAlbum[];
      next_offset: number | null;
    }>(`/v1/admin/unassigned?offset=${inboxOffset}`);
    setInbox(page.albums);
    setInboxNext(page.next_offset);
  }
  useEffect(() => {
    void load().catch((e) => setError(e.message));
  }, [inboxOffset]);
  async function action(fn: () => Promise<unknown>, text = "Saved") {
    setError("");
    try {
      await fn();
      notify(text);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const notices = <>{error && <InlineError>{error}</InlineError>}</>;
  return { people, events, warnings, inbox, inboxNext, action, notices };
}

export function AdminPeople() {
  const { people, events, warnings, action, notices } = useAdminData();
  const [invite, setInvite] = useState<{ invite: Invite; name: string } | null>(
    null,
  );
  const guessed = guessedAccounts(events, warnings);
  return (
    <>
      {notices}
      {guessed.length > 0 && (
        <p className="error-banner" role="alert">
          Someone has been trying wrong passwords for {guessed.join(", ")} this
          week. PigMusic is slowing their attempts down; nobody is locked out.
        </p>
      )}
      <section className="account-section">
        <h2>People</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const form = e.currentTarget;
            const d = new FormData(form);
            void action(async () => {
              const result = await api<Invite>(
                "/v1/people",
                json({
                  username: d.get("username"),
                  display_name: d.get("name"),
                }),
              );
              setInvite({
                invite: result,
                name: String(d.get("name") || "this person"),
              });
              form.reset();
            }, "Person added");
          }}
        >
          <label>
            Name
            <input name="name" required maxLength={128} />
          </label>
          <label>
            Username
            <input name="username" required minLength={2} maxLength={64} />
          </label>
          <button type="submit">Add person</button>
        </form>
        {invite && (
          <InvitePanel
            invite={invite.invite}
            name={invite.name}
            done={() => setInvite(null)}
          />
        )}
        {people.map((p) => (
          <p key={p.id}>
            {p.display_name} · {p.role}
            {p.role !== "owner" && (
              <>
                <button
                  aria-label={`Reset password for ${p.display_name}`}
                  onClick={async () => {
                    const session = await askToConfirm({
                      title: `Reset ${p.display_name}'s password?`,
                      body: `${p.display_name} is signed out everywhere at once and needs the new invite link to choose a password. This can't be undone.`,
                      action: "Reset password",
                      destructive: true,
                    });
                    if (session)
                      void action(
                        async () =>
                          setInvite({
                            invite: await api<Invite>(
                              `/v1/people/${p.id}/setup-code`,
                              {
                                method: "POST",
                                headers: sessionHeader(session),
                              },
                            ),
                            name: p.display_name,
                          }),
                        "New invite created",
                      );
                  }}
                >
                  Reset password
                </button>
                <button
                  aria-label={`${p.disabled ? "Enable" : "Disable"} ${p.display_name}`}
                  onClick={async () => {
                    // Disabling signs them out everywhere: ask first (#72).
                    const session = p.disabled
                      ? expectedSession()
                      : await askToConfirm({
                          title: `Disable ${p.display_name}'s account?`,
                          body: `${p.display_name} is signed out everywhere and can't sign in until you enable the account again.`,
                          action: "Disable",
                          destructive: true,
                        });
                    if (session)
                      void action(
                        () =>
                          api(`/v1/people/${p.id}/disabled`, {
                            method: "PUT",
                            headers: {
                              "Content-Type": "application/json",
                              ...sessionHeader(session),
                            },
                            body: JSON.stringify({ disabled: !p.disabled }),
                          }),
                        p.disabled ? "Account enabled" : "Account disabled",
                      );
                  }}
                >
                  {p.disabled ? "Enable" : "Disable"}
                </button>
              </>
            )}
          </p>
        ))}
      </section>
      <section className="account-section">
        <h2>Account activity</h2>
        {events.map((e, i) => (
          <p key={i}>
            {formatDateTime(e.at)} · {e.display_name || "Unknown account"} ·{" "}
            {e.event.replaceAll("_", " ")}
          </p>
        ))}
      </section>
    </>
  );
}

export function UnassignedMusic() {
  const [inboxOffset, setInboxOffset] = useState(0);
  const { people, inbox, inboxNext, action, notices } =
    useAdminData(inboxOffset);
  return (
    <>
      {notices}
      <section className="account-section">
        <h2>Unassigned music</h2>
        <p>
          Newly scanned music waits here until you choose whose library it
          belongs in.
        </p>
        {!inbox.length && (
          <EmptyLine>No music is waiting for assignment.</EmptyLine>
        )}
        {inbox.map((a) => (
          <form
            key={a.id}
            onSubmit={(e) => {
              e.preventDefault();
              const d = new FormData(e.currentTarget);
              void action(
                () =>
                  api(
                    "/v1/admin/unassigned/assign",
                    json({
                      track_ids: a.tracks.map((t) => t.id),
                      user_ids: d.getAll("person"),
                    }),
                  ),
                "Music assigned",
              );
            }}
          >
            <h3>
              {a.title} · {a.artist}
            </h3>
            {people
              .filter((p) => !p.disabled)
              .map((p) => (
                <label key={p.id}>
                  <input type="checkbox" name="person" value={p.id} />
                  {p.display_name}
                </label>
              ))}
            <button type="submit">Assign {a.tracks.length} songs</button>
          </form>
        ))}
        <button
          disabled={!inboxOffset}
          onClick={() => setInboxOffset(Math.max(0, inboxOffset - 100))}
        >
          Previous
        </button>
        <button
          disabled={inboxNext === null}
          onClick={() => setInboxOffset(inboxNext || 0)}
        >
          Next
        </button>
      </section>
    </>
  );
}
