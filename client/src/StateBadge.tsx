import type { ReactNode } from "react";
import { CircleAlert, CircleCheck, Clock, LoaderCircle } from "lucide-react";

export type BadgeState =
  | "library"
  | "requested"
  | "active"
  | "failed"
  | "neutral";

// Icons take the row's 20 px when they stand alone, 14 px beside words.
const defaults: Record<
  BadgeState,
  { label: string; icon: (size: number) => ReactNode }
> = {
  library: {
    label: "In your library",
    icon: (size) => <CircleCheck size={size} />,
  },
  requested: { label: "Adding", icon: (size) => <Clock size={size} /> },
  active: {
    label: "Adding",
    icon: (size) => <LoaderCircle size={size} className="spin" />,
  },
  failed: {
    label: "Couldn't add",
    icon: (size) => <CircleAlert size={size} />,
  },
  neutral: { label: "", icon: () => null },
};

/** The one place a song's or download's state is shown: icon plus words. */
export function StateBadge({
  state,
  label,
  iconOnly = false,
}: {
  state: BadgeState;
  label?: string;
  iconOnly?: boolean;
}) {
  const text = label ?? defaults[state].label;
  const icon = defaults[state].icon(iconOnly ? 20 : 14);
  if (iconOnly)
    return (
      <span
        className="state-badge icon-only"
        data-state={state}
        role="img"
        aria-label={text}
        title={text}
      >
        {icon}
      </span>
    );
  return (
    <span className="state-badge" data-state={state}>
      {icon}
      {text}
    </span>
  );
}
