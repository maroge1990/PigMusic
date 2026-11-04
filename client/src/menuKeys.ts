/** Where focus goes in a menu of `count` items after `key` (null: not a menu key). */
export function nextMenuIndex(
  key: string,
  current: number,
  count: number,
): number | null {
  if (!count) return null;
  if (key === "ArrowDown") return current < 0 ? 0 : (current + 1) % count;
  if (key === "ArrowUp")
    return current < 0 ? count - 1 : (current - 1 + count) % count;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return null;
}

function items(menu: HTMLElement | null) {
  return menu
    ? [
        ...menu.querySelectorAll<HTMLElement>(
          '[role="menuitem"]:not(:disabled)',
        ),
      ]
    : [];
}

/** Focuses a menu's item by position (the first when it opens). */
export function focusMenuItem(menu: HTMLElement | null, index: number) {
  items(menu)[index]?.focus();
}

/** Arrow keys, Home and End move between a menu's items. */
export function menuKeys(event: {
  key: string;
  currentTarget: HTMLElement;
  preventDefault: () => void;
}) {
  const list = items(event.currentTarget);
  const at = nextMenuIndex(
    event.key,
    list.indexOf(document.activeElement as HTMLElement),
    list.length,
  );
  if (at === null) return;
  event.preventDefault();
  list[at].focus();
}
