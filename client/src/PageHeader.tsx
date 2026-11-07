import type { ReactNode } from "react";

/** Page title block: optional cover, eyebrow, h1, optional subtitle and actions. */
export function PageHeader({
  title,
  subtitle,
  eyebrow,
  actions,
  cover,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  eyebrow?: ReactNode;
  actions?: ReactNode;
  /** Album or release art shown before the title. */
  cover?: ReactNode;
}) {
  return (
    <header className={cover ? "page-header has-cover" : "page-header"}>
      {cover}
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className="page-header-actions">{actions}</div>}
    </header>
  );
}

/** A section inside a page: h2, optional subtitle and actions. */
export function SectionHeader({
  title,
  subtitle,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="section-title">
      <div>
        <h2>{title}</h2>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className="section-actions">{actions}</div>}
    </header>
  );
}
