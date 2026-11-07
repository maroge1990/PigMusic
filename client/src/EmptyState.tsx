import { LoaderCircle } from "lucide-react";
import type { ReactNode } from "react";

/** A page or list with nothing in it yet: the pig, a title and a line. */
export function Empty({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="empty-state">
      <img className="empty-pig" src="/brand/mark.png" alt="" />
      <h3>{title}</h3>
      <p>{description}</p>
    </div>
  );
}

/** The inline variant: one quiet line where a list or panel result would be. */
export function EmptyLine({ children }: { children: ReactNode }) {
  return <p className="empty-line">{children}</p>;
}

/** Something is loading: one line with a spinner, announced politely. */
export function Loading({ children = "Loading…" }: { children?: ReactNode }) {
  return (
    <p className="subtle-loading" role="status">
      <LoaderCircle size={16} className="spin" aria-hidden="true" />
      {children}
    </p>
  );
}

/** A failure shown beside what failed; a page-wide one uses .error-banner. */
export function InlineError({
  children,
  className,
}: {
  children: ReactNode;
  /** Layout for where it sits, never its own colour or size. */
  className?: string;
}) {
  return (
    <p
      className={className ? `inline-error ${className}` : "inline-error"}
      role="alert"
    >
      {children}
    </p>
  );
}
