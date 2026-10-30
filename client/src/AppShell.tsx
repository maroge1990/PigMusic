import { lazy, Suspense, useEffect, useState } from "react";
import {
  ArrowDownToLine,
  House,
  LibraryBig,
  Search,
  ShieldCheck,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import { api, ApiError, clearSessionReads } from "./api";
import { PersonalLibrary } from "./PersonalLibrary";
import { AccountSettings } from "./AccountSettings";
import { Home } from "./Home";
import { FirstRun } from "./FirstRun";
import { SignIn } from "./SignIn";
import { ToolBoundary } from "./ToolBoundary";
import { useFixedHeight } from "./useFixedHeight";
import { PageHeader } from "./PageHeader";
import { PersonalRequests } from "./PersonalRequests";
import { OwnerContext } from "./person";
import { parseRoute, type Route, type Section } from "./routes";
import {
  captureInvite,
  clearInvite,
  dismissInstallHint,
  installHintPending,
} from "./invite";
import "./accounts.css";

const AdminTools = lazy(() =>
  import("./AdminTools").then((m) => ({ default: m.AdminTools })),
);

export type Person = {
  id: string;
  username: string;
  display_name: string;
  role: "owner" | "member";
  disabled?: boolean;
};

const sections: { id: Section; label: string; icon: LucideIcon }[] = [
  { id: "home", label: "Home", icon: House },
  { id: "search", label: "Search", icon: Search },
  { id: "library", label: "Library", icon: LibraryBig },
  { id: "downloads", label: "Downloads", icon: ArrowDownToLine },
  { id: "account", label: "Account", icon: UserRound },
  // Administrator additions always follow everything a member sees.
  { id: "admin", label: "Admin", icon: ShieldCheck },
];

export function AppShell() {
  const [user, setUser] = useState<Person | null>(null);
  const [ready, setReady] = useState(false);
  const [firstRun, setFirstRun] = useState(false);
  const [error, setError] = useState("");
  const [route, setRoute] = useState<Route>(() => parseRoute());
  const [hint, setHint] = useState(false);
  // Remounts the sign-in form when an invite link opens in an existing tab.
  const [invites, setInvites] = useState(0);
  const nav = useFixedHeight("--mobile-nav-height", !!user);
  // Only an owner on the Admin section subscribes to the household queue.
  useServerEvents(!!user, user?.role === "owner" && route.section === "admin");
  const downloading = useActiveDownloads(!!user);

  useEffect(() => {
    // "System" appearance follows the device live, not only at page load.
    const media = matchMedia("(prefers-color-scheme: dark)");
    const follow = () => {
      let chosen: string | null = null;
      try {
        chosen = localStorage.getItem("pig-music-appearance");
      } catch {
        /* storage unavailable: follow the device */
      }
      if (chosen === "light" || chosen === "dark") return;
      const theme = media.matches ? "dark" : "light";
      document.documentElement.dataset.theme = theme;
    };
    // The browser chrome follows whatever theme is applied, whether the device
    // changed or the person picked System, Light or Dark in Account.
    const paint = () =>
      document
        .querySelector('meta[name="theme-color"]')
        ?.setAttribute(
          "content",
          document.documentElement.dataset.theme === "dark"
            ? "#15111A"
            : "#FBF8FA",
        );
    const observer = new MutationObserver(paint);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    paint();
    media.addEventListener("change", follow);
    return () => {
      observer.disconnect();
      media.removeEventListener("change", follow);
    };
  }, []);

  useEffect(() => {
    // Appearance belongs to the account, as on iPhone: the browser copy only
    // gives the first paint the right theme before the account answers.
    if (!user) return;
    window.dispatchEvent(new Event("music-signed-in"));
    let active = true;
    void api<{ appearance?: string }>("/v1/preferences")
      .then(({ appearance = "system" }) => {
        if (!active) return;
        try {
          localStorage.setItem("pig-music-appearance", appearance);
        } catch {
          /* storage unavailable: the theme still applies for this visit */
        }
        document.documentElement.dataset.theme =
          appearance === "light" || appearance === "dark"
            ? appearance
            : matchMedia("(prefers-color-scheme: dark)").matches
              ? "dark"
              : "light";
      })
      .catch(() => {
        /* Keep the browser's last known theme. */
      });
    return () => {
      active = false;
    };
  }, [user?.id]);

  useEffect(() => {
    document.title = user
      ? `${sections.find((s) => s.id === route.section)?.label || "Home"} · PigMusic`
      : "PigMusic";
  }, [user, route.section]);

  useEffect(() => {
    const changed = () => {
      // A link changing only the fragment does not reload the page.
      if (/^#\/?join\b/.test(location.hash) && captureInvite())
        setInvites((n) => n + 1);
      setRoute(parseRoute());
      window.scrollTo(0, 0);
      // Move keyboard and screen-reader focus to the new page's heading.
      requestAnimationFrame(() => {
        const heading = document.querySelector<HTMLElement>("main h1");
        if (!heading) return;
        heading.tabIndex = -1;
        heading.focus({ preventScroll: true });
      });
    };
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);

  async function refresh() {
    try {
      const state = await api<{ setup_required: boolean }>("/v1/setup/owner");
      setFirstRun(state.setup_required);
      if (state.setup_required) {
        setUser(null);
        setError("");
        return;
      }
      setUser(await api<Person>("/v1/me"));
      setError("");
    } catch (e) {
      setUser(null);
      if ((e as ApiError).code === "setup_required")
        setError("The administrator needs to finish the first account setup.");
      else if ((e as ApiError).code !== "authentication_required")
        setError((e as Error).message);
    } finally {
      setReady(true);
    }
  }

  useEffect(() => {
    void refresh();
    const expired = () => {
      setUser(null);
      clearSessionReads();
    };
    const forbidden = () => setError("You do not have access to that action.");
    window.addEventListener("music-auth-required", expired);
    window.addEventListener("music-forbidden", forbidden);
    return () => {
      window.removeEventListener("music-auth-required", expired);
      window.removeEventListener("music-forbidden", forbidden);
    };
  }, []);

  useEffect(() => {
    // An invite is for new accounts: a signed-in person goes home instead.
    if (user && /^#\/?(join|setup)\b/.test(location.hash)) {
      clearInvite();
      location.replace("#/home");
    }
  }, [user, route]);

  async function signOut() {
    await api("/v1/auth/logout", { method: "POST" });
    clearSessionReads();
    window.dispatchEvent(new Event("music-signed-out"));
    setUser(null);
    location.hash = "/home";
  }

  if (!ready)
    return (
      <main className="account-shell pig-splash app-splash">
        <img src="/brand/mark.png" alt="" className="account-pig" />
        <p role="status">Loading your account…</p>
      </main>
    );
  if (!user && firstRun)
    return (
      <FirstRun
        complete={() => {
          setFirstRun(false);
          void refresh();
        }}
      />
    );
  if (!user)
    return (
      <SignIn
        key={invites}
        signedIn={() => {
          setHint(installHintPending());
          void refresh();
        }}
        initialError={error}
      />
    );

  const owner = user.role === "owner";
  const section = route.section === "admin" && !owner ? "home" : route.section;
  return (
    <OwnerContext.Provider value={owner}>
      <div className="personal-app">
        <header className="personal-header">
          <a href="#/home" className="personal-brand">
            <img src="/brand/mark.png" alt="" />
            <span className="header-wordmark" role="img" aria-label="PigMusic">
              <img
                src="/brand/wordmark-light.svg"
                className="light-wordmark"
                alt=""
              />
              <img
                src="/brand/wordmark-dark.svg"
                className="dark-wordmark"
                alt=""
              />
            </span>
          </a>
          <a
            href="#/account"
            className="header-account"
            aria-label="Account"
            aria-current={section === "account" ? "page" : undefined}
          >
            {initials(user.display_name || user.username)}
          </a>
          <nav ref={nav} className="app-nav" aria-label="Main navigation">
            {sections
              .filter((s) => s.id !== "admin" || owner)
              .map(({ id, label, icon: Icon }) => {
                const count = id === "downloads" ? downloading : 0;
                return (
                  <a
                    key={id}
                    href={"#/" + id}
                    className={"nav-" + id}
                    aria-current={section === id ? "page" : undefined}
                    aria-label={
                      count ? `${label}, ${count} in progress` : undefined
                    }
                  >
                    <Icon size={20} aria-hidden="true" />
                    <span>{label}</span>
                    {count > 0 && (
                      <span className="nav-badge" aria-hidden="true">
                        {count > 99 ? "99+" : count}
                      </span>
                    )}
                  </a>
                );
              })}
          </nav>
        </header>
        {hint && (
          <div className="personal-notice install-hint" role="status">
            <p>
              Tip: add PigMusic to your Home Screen — tap Share, then Add to
              Home Screen.
            </p>
            <button
              type="button"
              onClick={() => {
                dismissInstallHint();
                setHint(false);
              }}
            >
              Dismiss
            </button>
          </div>
        )}
        {error && (
          <p className="personal-notice" role="alert">
            {error}
          </p>
        )}
        {section === "home" ? (
          <Home />
        ) : section === "search" || section === "library" ? (
          <PersonalLibrary user={user} route={{ ...route, section }} />
        ) : section === "downloads" ? (
          <main className="personal-main">
            <PageHeader
              title="Downloads"
              subtitle="Music you asked the server to get, newest first."
            />
            <PersonalRequests />
          </main>
        ) : section === "account" ? (
          <AccountSettings user={user} signOut={signOut} />
        ) : (
          <ToolBoundary>
            <Suspense
              fallback={
                <main className="personal-main">
                  <p role="status">Loading admin…</p>
                </main>
              }
            >
              <AdminTools route={route} />
            </Suspense>
          </ToolBoundary>
        )}
      </div>
    </OwnerContext.Provider>
  );
}

const initials = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join("");

const RECONNECT_FIRST = 3000;
const RECONNECT_MAX = 300000;

/** One change feed for every section: library revisions, download progress
 * and, for an owner on the Admin section, Admin state (`music-admin-state`;
 * the server computes it only for those streams). While the feed is
 * disconnected, one slow poll stands in for it, and only while the page is
 * visible (`music-admin-poll` asks Admin to fetch); a stream the browser gave
 * up on is reopened with backoff. */
function useServerEvents(active: boolean, admin: boolean) {
  useEffect(() => {
    if (!active) return;
    const changed = () => {
      window.dispatchEvent(new Event("music-library-changed"));
      // New artwork also refreshes the library; let missing covers look again.
      window.dispatchEvent(new Event("music-artwork-retry"));
    };
    const requests = () =>
      window.dispatchEvent(new Event("music-requests-changed"));
    let fallback: ReturnType<typeof setInterval> | undefined;
    const poll = () => {
      if (document.hidden) return;
      changed();
      requests();
      window.dispatchEvent(new Event("music-admin-poll"));
    };
    const visible = () => {
      if (fallback) poll();
    };
    let events: EventSource;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let delay = RECONNECT_FIRST;
    const connect = () => {
      events = new EventSource(
        admin ? "/api/v1/events?admin=true" : "/api/v1/events",
      );
      events.addEventListener("revision", changed);
      events.addEventListener("queue", requests);
      events.addEventListener("revoked", () =>
        window.dispatchEvent(new Event("music-auth-required")),
      );
      events.addEventListener("admin", (event) => {
        try {
          const detail: unknown = JSON.parse((event as MessageEvent).data);
          window.dispatchEvent(
            new CustomEvent("music-admin-state", { detail }),
          );
        } catch {
          /* a malformed message waits for the next one */
        }
      });
      events.onerror = () => {
        fallback ||= setInterval(poll, 15000);
        // Browsers retry a dropped stream themselves but give up for good
        // after an error response (a 502 during a release, a 429 at the
        // stream cap): open a new one with backoff (#73).
        if (events.readyState === EventSource.CLOSED && !retry) {
          retry = setTimeout(() => {
            retry = undefined;
            connect();
          }, delay);
          delay = Math.min(delay * 2, RECONNECT_MAX);
        }
      };
      events.onopen = () => {
        clearInterval(fallback);
        fallback = undefined;
        delay = RECONNECT_FIRST;
      };
    };
    connect();
    document.addEventListener("visibilitychange", visible);
    return () => {
      events.close();
      clearTimeout(retry);
      clearInterval(fallback);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [active, admin]);
}

/** Count of this person's queued or in-progress requests, for the nav badge. */
function useActiveDownloads(active: boolean) {
  const [count, setCount] = useState(0);
  useEffect(() => {
    if (!active) {
      setCount(0);
      return;
    }
    let serial = 0;
    let last = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () => {
      last = Date.now();
      const mine = ++serial;
      void api<{ active?: number }>("/v1/requests?offset=0&limit=1")
        .then((data) => {
          if (mine === serial) setCount(data.active ?? 0);
        })
        .catch(() => {
          /* keep the last known count */
        });
    };
    // SSE queue events can be frequent while downloading: refresh at most ~1/s.
    const changed = () => {
      if (timer) return;
      timer = setTimeout(
        () => {
          timer = undefined;
          load();
        },
        Math.max(0, 1000 - (Date.now() - last)),
      );
    };
    load();
    window.addEventListener("music-requests-changed", changed);
    return () => {
      ++serial;
      clearTimeout(timer);
      window.removeEventListener("music-requests-changed", changed);
    };
  }, [active]);
  return count;
}
