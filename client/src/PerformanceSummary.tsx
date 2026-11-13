import { Fragment, useEffect, useState } from "react";
import { api } from "./api";
import {
  performanceRows,
  shown,
  sourceLines,
  verdict,
  type PerformanceReport,
} from "./performance";
import { Loading } from "./EmptyState";

/** Owner view at the bottom of System: this week against the targets. */
export function PerformanceSummary() {
  const [report, setReport] = useState<PerformanceReport | null>(null);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void api<PerformanceReport>("/v1/admin/performance", {
      signal: controller.signal,
    })
      .then(setReport)
      .catch((e) => {
        if (!controller.signal.aborted) setNotice(e.message);
      });
    return () => controller.abort();
  }, []);
  return (
    <section className="system-panel performance-summary">
      <h2>Speed and coverage, last 7 days</h2>
      {notice ? (
        <p role="alert">{notice}</p>
      ) : !report ? (
        <Loading>Loading the last week’s numbers…</Loading>
      ) : (
        <dl>
          {performanceRows(report).map((row) => (
            <Fragment key={row.name}>
              <dt>{row.name}</dt>
              <dd>
                <span className="pig-amount">
                  {shown(row.value, row.seconds)}
                </span>
                {row.basis && ` · ${row.basis}`} · target{" "}
                {row.lower ? "at most" : "at least"}{" "}
                <span className="pig-amount">
                  {shown(row.target, row.seconds)}
                </span>
                {verdict(row) && ` · ${verdict(row)}`}
              </dd>
            </Fragment>
          ))}
        </dl>
      )}
      {report && sourceLines(report).length > 0 && (
        <>
          <h3>Instant sources</h3>
          <ul className="performance-sources">
            {sourceLines(report).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </>
      )}
      <p>
        Counted on this server and kept for a week. Library artists are measured
        now; the rest are what people met while using PigMusic.
      </p>
    </section>
  );
}
