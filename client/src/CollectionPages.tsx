import { useState, type ReactNode } from "react";

import "./CollectionPages.css";

const PAGE_SIZE = 100;

/** Bound collection DOM without removing keyboard-focused cards on scroll. */
export function CollectionPages<T>({
  items,
  label,
  children,
}: {
  items: T[];
  label: string;
  children: (items: T[]) => ReactNode;
}) {
  const [requestedPage, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const page = Math.min(requestedPage, pageCount - 1);
  const start = page * PAGE_SIZE;
  return (
    <>
      {pageCount > 1 && (
        <nav className="collection-pages" aria-label={`${label} pages`}>
          <button
            className="secondary-button"
            disabled={page === 0}
            onClick={() => setPage(page - 1)}
          >
            Previous
          </button>
          <span role="status" aria-live="polite">
            {start + 1}–{Math.min(start + PAGE_SIZE, items.length)} of{" "}
            {items.length}
          </span>
          <button
            className="secondary-button"
            disabled={page === pageCount - 1}
            onClick={() => setPage(page + 1)}
          >
            Next
          </button>
        </nav>
      )}
      {children(items.slice(start, start + PAGE_SIZE))}
    </>
  );
}
