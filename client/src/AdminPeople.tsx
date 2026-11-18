import { expectedSession, sessionHeader } from "./sessionId";
import { askToConfirm } from "./confirmDialog";
import { notify } from "./notices";
import { useEffect, useState } from "react";
import { KeyRound, Plus, UserCheck, UserX } from "lucide-react";
import { api } from "./api";
import { Cover } from "./Cover";
import { formatCount, formatDateTime, initials } from "./format";
import type { Person } from "./AppShell";
import { InvitePanel, type Invite } from "./InvitePanel";
import { guessedAccounts, type Activity } from "./accountAlerts";
import { EmptyLine, InlineError } from "./EmptyState";
import { MenuButton, MenuPopup, useMenu } from "./Menu";
import { SectionHeader } from "./PageHeader";

type InboxAlbum = {
  id: string;
  title: string;
  artist: string;
  tracks: { id: string; cover_url?: string | null }[];
};

const PAGE = 100;

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
  const [inboxTotal, setInboxTotal] = useState(0);
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
      total?: number;
      next_offset: number | null;
    }>(`/v1/admin/unassigned?offset=${inboxOffset}`);
    setInbox(page.albums);
    setInboxTotal(page.total ?? page.albums.length);
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
  return {
    people,
    events,
    warnings,
    inbox,
    inboxTotal,
    inboxNext,
    action,
    notices,
  };
}

type Act = (fn: () => Promise<unknown>, text?: string) => Promise<void>;

/** One person: initials, name, role, and their actions under ⋯. */
function PersonRow({
  person: p,
  action,
  invited,
}: {
  person: Person;
  action: Act;
  invited: (invite: Invite, name: string) => void;
}) {
  const menu = useMenu();
  async function reset() {
    const session = await askToConfirm({
      title: `Reset ${p.display_name}'s password?`,
      body: `${p.display_name} is signed out everywhere at once and needs the new invite link to choose a password. This can't be undone.`,
      action: "Reset password",
      destructive: true,
    });
    if (session)
      void action(
        async () =>
          invited(
            await api<Invite>(`/v1/people/${p.id}/setup-code`, {
              method: "POST",
              headers: sessionHeader(session),
            }),
            p.display_name,
          ),
        "New invite created",
      );
  }
  async function toggle() {
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
  }
  return (
    <li className={p.disabled ? "person-row is-disabled" : "person-row"}>
      <span className="person-avatar" aria-hidden="true">
        {initials(p.display_name || p.username)}
      </span>
      <div className="person-text">
        <strong>{p.display_name}</strong>
        <span>
          {p.disabled ? "Disabled" : p.role === "owner" ? "Owner" : "Member"}
        </span>
      </div>
      {p.role !== "owner" && (
        <>
          <MenuButton
            menu={menu}
            label={`More actions for ${p.display_name}`}
          />
          <MenuPopup
            menu={menu}
            label={p.display_name}
            groups={[
              [
                {
                  key: "reset",
                  label: "Reset password",
                  icon: <KeyRound size={18} />,
                  run: () => void reset(),
                },
                {
                  key: "disabled",
                  label: p.disabled ? "Enable" : "Disable",
                  icon: p.disabled ? (
                    <UserCheck size={18} />
                  ) : (
                    <UserX size={18} />
                  ),
                  run: () => void toggle(),
                  danger: !p.disabled,
                },
              ],
            ]}
          />
        </>
      )}
    </li>
  );
}

/** People and Unassigned music side by side, account activity below. */
export function AdminPeople() {
  const [inboxOffset, setInboxOffset] = useState(0);
  const {
    people,
    events,
    warnings,
    inbox,
    inboxTotal,
    inboxNext,
    action,
    notices,
  } = useAdminData(inboxOffset);
  const [invite, setInvite] = useState<{ invite: Invite; name: string } | null>(
    null,
  );
  const [adding, setAdding] = useState(false);
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
      <div className="admin-people-grid">
        <section className="account-section" aria-labelledby="admin-people">
          <SectionHeader
            id="admin-people"
            title="People"
            actions={
              <button
                className="primary-button"
                aria-expanded={adding}
                onClick={() => setAdding(!adding)}
              >
                <Plus size={16} aria-hidden="true" />
                Add person
              </button>
            }
          />
          {adding && (
            <form
              className="admin-add-person"
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
                  setAdding(false);
                }, "Person added");
              }}
            >
              <label>
                Name
                <input name="name" required autoFocus maxLength={128} />
              </label>
              <label>
                Username
                <input name="username" required minLength={2} maxLength={64} />
              </label>
              <button type="submit">Add</button>
            </form>
          )}
          {invite && (
            <InvitePanel
              invite={invite.invite}
              name={invite.name}
              done={() => setInvite(null)}
            />
          )}
          <ul className="person-list">
            {people.map((p) => (
              <PersonRow
                key={p.id}
                person={p}
                action={action}
                invited={(invite, name) => setInvite({ invite, name })}
              />
            ))}
          </ul>
        </section>
        <section className="account-section" aria-labelledby="admin-unassigned">
          <SectionHeader
            id="admin-unassigned"
            title={
              <>
                Unassigned music
                {inboxTotal > 0 && (
                  <span className="admin-count pig-amount">
                    {formatCount(inboxTotal)}{" "}
                    {inboxTotal === 1 ? "album" : "albums"}
                  </span>
                )}
              </>
            }
          />
          {!inbox.length && (
            <EmptyLine>No music is waiting for assignment.</EmptyLine>
          )}
          <ul className="unassigned-list">
            {inbox.map((a) => (
              <li key={a.id}>
                <form
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
                  <Cover
                    title={a.title}
                    src={a.tracks.find((t) => t.cover_url)?.cover_url}
                    sizes="52px"
                  />
                  <div className="unassigned-text">
                    <strong>{a.title}</strong>
                    <span>
                      {a.artist} · {formatCount(a.tracks.length)}{" "}
                      {a.tracks.length === 1 ? "song" : "songs"}
                    </span>
                    <fieldset>
                      <legend className="sr-only">
                        Whose library {a.title} goes in
                      </legend>
                      {people
                        .filter((p) => !p.disabled)
                        .map((p) => (
                          <label key={p.id}>
                            <input type="checkbox" name="person" value={p.id} />
                            {p.display_name}
                          </label>
                        ))}
                    </fieldset>
                  </div>
                  <button
                    type="submit"
                    aria-label={`Assign ${a.title}`}
                    className="secondary-button"
                  >
                    Assign
                  </button>
                </form>
              </li>
            ))}
          </ul>
          {(inboxOffset > 0 || inboxNext !== null) && (
            <nav className="collection-pages" aria-label="Unassigned pages">
              <button
                className="secondary-button"
                disabled={!inboxOffset}
                onClick={() => setInboxOffset(Math.max(0, inboxOffset - PAGE))}
              >
                Previous
              </button>
              <button
                className="secondary-button"
                disabled={inboxNext === null}
                onClick={() => setInboxOffset(inboxNext || 0)}
              >
                Next
              </button>
            </nav>
          )}
        </section>
      </div>
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
