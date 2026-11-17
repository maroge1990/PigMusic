import { useEffect, useState } from "react";
import { api } from "./api";
import { formatDateTime } from "./format";
import { Loading } from "./EmptyState";
import {
  busyLine,
  noProblems,
  restartsLine,
  workerName,
  workerNote,
  workerState,
  type SystemHealth,
} from "./serverHealth";

/** Owner view on System: what broke and why, without the container log. */
export function ServerHealth() {
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void api<SystemHealth>("/v1/admin/system/health", {
      signal: controller.signal,
    })
      .then(setHealth)
      .catch((e) => {
        if (!controller.signal.aborted) setNotice(e.message);
      });
    return () => controller.abort();
  }, []);
  return (
    <section className="system-panel server-health">
      <h2>Server health</h2>
      {notice ? (
        <p role="alert">{notice}</p>
      ) : !health ? (
        <Loading>Checking the server…</Loading>
      ) : (
        <>
          <dl>
            <dt>Running since</dt>
            <dd>{formatDateTime(health.started_at)}</dd>
            {health.previous_run && (
              <>
                <dt>Last time it stopped</dt>
                <dd>
                  {formatDateTime(health.previous_run.at)}.{" "}
                  {health.previous_run.sentence}
                </dd>
              </>
            )}
            {health.workers.map((worker) => (
              <WorkerRow key={worker.name} worker={worker} />
            ))}
          </dl>
          {restartsLine(health) && <p>{restartsLine(health)}</p>}
          {noProblems(health) ? (
            <p>No problems since the server started.</p>
          ) : (
            <>
              {busyLine(health) && <p>{busyLine(health)}</p>}
              {health.startup_problems.length > 0 && (
                <>
                  <h3>When the server started</h3>
                  <ul className="server-health-problems">
                    {health.startup_problems.map((problem, index) => (
                      <li key={index}>
                        {problem.at != null &&
                          `${formatDateTime(problem.at)}: `}
                        {problem.sentence}
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {health.recent_errors.length > 0 && (
                <>
                  <h3>Recent problems</h3>
                  <ul className="server-health-problems">
                    {health.recent_errors.map((error) => (
                      <li key={error.request_id + error.at}>
                        {formatDateTime(error.at)}: {error.sentence}{" "}
                        <code title="Reference">{error.request_id}</code>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}

function WorkerRow({ worker }: { worker: SystemHealth["workers"][number] }) {
  const note = workerNote(worker);
  return (
    <>
      <dt>{workerName(worker.name)}</dt>
      <dd>
        {workerState(worker)}
        {note && ` · ${note}`}
      </dd>
    </>
  );
}
