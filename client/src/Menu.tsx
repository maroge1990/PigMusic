import {
  useEffect,
  useRef,
  useState,
  type MouseEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { Ellipsis, LoaderCircle } from "lucide-react";
import { focusMenuItem, menuKeys } from "./menuKeys";

/** How long a touch must rest on a row or card before its menu opens. */
export const LONG_PRESS_MS = 500;

export type MenuEntry = {
  key: string;
  label: string;
  icon: ReactNode;
  run: () => void;
  disabled?: boolean;
  danger?: boolean;
};

type Position = { left: number; top?: number; bottom?: number };

export type MenuControl = {
  isOpen: boolean;
  open: (at?: { x: number; y: number }) => void;
  close: (refocus?: boolean) => void;
  trigger: RefObject<HTMLButtonElement | null>;
  popup: RefObject<HTMLDivElement | null>;
  position: Position;
  /** Spread on the row or card: right-click and a long press open the menu,
   * and the click that ends a long press does not also play. */
  target: {
    onContextMenu: (e: MouseEvent) => void;
    onPointerDown: (e: ReactPointerEvent) => void;
    onPointerUp: () => void;
    onPointerLeave: () => void;
    onPointerCancel: () => void;
    onClickCapture: (e: MouseEvent) => void;
  };
};

const inside = (target: EventTarget | null, selector: string) =>
  target instanceof Element && !!target.closest(selector);

/**
 * One menu for songs, albums, artists, playlists and mixes (#249): the ⋯
 * button, a right-click and a long press open the same list. Arrow keys,
 * Home and End move, Escape closes, and focus goes back to the ⋯ button.
 */
export function useMenu(onOpen?: () => void): MenuControl {
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const press = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const swallowClick = useRef(false);
  const [isOpen, setOpen] = useState(false);
  const [position, setPosition] = useState<Position>({ left: 8, top: 8 });
  function open(at?: { x: number; y: number }) {
    const rect = trigger.current?.getBoundingClientRect();
    const width = Math.min(272, window.innerWidth - 16);
    const x = at?.x ?? (rect ? rect.right - width : 8);
    const left = Math.max(8, Math.min(x, window.innerWidth - width - 8));
    const y = at?.y ?? (rect ? rect.bottom : 8);
    setPosition(
      at
        ? { left, top: Math.max(8, Math.min(y, window.innerHeight - 320)) }
        : rect && rect.top > window.innerHeight / 2
          ? { left, bottom: window.innerHeight - rect.top + 4 }
          : { left, top: y + 4 },
    );
    // A manual popover: an automatic one would be light-dismissed by the very
    // pointerup that ends a right-click or a long press.
    if (!popup.current?.matches(":popover-open")) popup.current?.showPopover();
    setOpen(true);
    onOpen?.();
    focusMenuItem(popup.current, 0);
  }
  function close(refocus = false) {
    if (popup.current?.matches(":popover-open")) popup.current.hidePopover();
    setOpen(false);
    if (refocus && trigger.current?.isConnected) trigger.current.focus();
  }
  useEffect(() => {
    if (!isOpen) return;
    const outside = (e: PointerEvent) => {
      const target = e.target as Node;
      if (
        !popup.current?.contains(target) &&
        !trigger.current?.contains(target)
      )
        close();
    };
    const escape = (e: KeyboardEvent) => {
      if (e.key === "Escape") close(true);
    };
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("keydown", escape);
    };
  }, [isOpen]);
  const cancelPress = () => clearTimeout(press.current);
  useEffect(() => cancelPress, []);
  return {
    isOpen,
    open,
    close,
    trigger,
    popup,
    position,
    target: {
      onContextMenu: (e) => {
        if (inside(e.target, "[popover], dialog")) return;
        // The innermost row or card answers; a page around it does not.
        e.preventDefault();
        e.stopPropagation();
        cancelPress();
        open({ x: e.clientX, y: e.clientY });
      },
      onPointerDown: (e) => {
        if (e.pointerType === "mouse") return;
        if (inside(e.target, "[popover], dialog")) return;
        e.stopPropagation();
        swallowClick.current = false;
        const at = { x: e.clientX, y: e.clientY };
        cancelPress();
        press.current = setTimeout(() => {
          swallowClick.current = true;
          open(at);
        }, LONG_PRESS_MS);
      },
      onPointerUp: cancelPress,
      onPointerLeave: cancelPress,
      onPointerCancel: cancelPress,
      onClickCapture: (e) => {
        if (!swallowClick.current) return;
        swallowClick.current = false;
        e.preventDefault();
        e.stopPropagation();
      },
    },
  };
}

/** The ⋯ button that opens a menu. */
export function MenuButton({
  menu,
  label,
  className = "song-more",
  busy = false,
}: {
  menu: MenuControl;
  label: string;
  className?: string;
  /** Something chosen from the menu is still on its way: the spinner. */
  busy?: boolean;
}) {
  return (
    <button
      ref={menu.trigger}
      type="button"
      className={className}
      aria-label={label}
      aria-haspopup="menu"
      aria-expanded={menu.isOpen}
      aria-busy={busy || undefined}
      onClick={() => (menu.isOpen ? menu.close() : menu.open())}
    >
      {busy ? (
        <LoaderCircle size={20} className="spin" aria-hidden="true" />
      ) : (
        <Ellipsis size={20} />
      )}
    </button>
  );
}

/** The menu's list: groups of items, each with its word and icon. */
export function MenuPopup({
  menu,
  label,
  groups,
  children,
}: {
  menu: MenuControl;
  label: string;
  groups: MenuEntry[][];
  /** Anything after the items, such as the owner's tools. */
  children?: ReactNode;
}) {
  return (
    <div
      ref={menu.popup}
      popover="manual"
      className="song-menu pig-glass"
      role="menu"
      aria-label={label}
      style={menu.position}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          // Only the menu closes, not a sheet it sits on.
          e.stopPropagation();
          menu.close(true);
          return;
        }
        if (e.key === "Tab") menu.close();
        menuKeys(e);
      }}
    >
      {groups
        .filter((group) => group.length)
        .map((group) => (
          <div className="song-menu-group" key={group[0].key}>
            {group.map((entry) => (
              <button
                key={entry.key}
                type="button"
                role="menuitem"
                className={entry.danger ? "danger" : undefined}
                disabled={entry.disabled}
                onClick={() => {
                  menu.close(true);
                  entry.run();
                }}
              >
                <span>{entry.label}</span>
                {entry.icon}
              </button>
            ))}
          </div>
        ))}
      {children}
    </div>
  );
}
