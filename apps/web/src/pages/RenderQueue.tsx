import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  Empty,
  ErrorBox,
  Panel,
  Pill,
  Progress,
  formatDate,
  formatDuration,
  useAsync,
} from "../components/ui";
import { Job, api } from "../lib/api";

const STATE_LABEL: Record<string, string> = {
  queued: "na fila",
  preparing: "preparando",
  tts: "gerando voz",
  lipsync: "lip-sync",
  captions: "legendas",
  editing: "edição",
  rendering: "renderizando",
  completed: "concluído",
  failed: "falhou",
  cancelled: "cancelado",
};

const STATE_LEVEL: Record<string, "ok" | "warn" | "error" | "info" | "accent"> = {
  completed: "ok",
  failed: "error",
  cancelled: "warn",
};

function JobCard({ job, onChanged }: { job: Job; onChanged: () => void }) {
  const [log, setLog] = useState<string | null>(null);
  const active = !["completed", "failed", "cancelled"].includes(job.state);

  return (
    <div className="panel" style={{ background: "var(--surface-2)" }}>
      <div className="row row-between" style={{ marginBottom: 8 }}>
        <div style={{ minWidth: 0 }}>
          <Link to={`/new/${job.project_id}`} className="bold small">
            {job.project_id}
          </Link>
          <div className="tiny dim mono">
            {job.id} · {job.kind} · {formatDate(job.created_at)}
          </div>
        </div>
        <Pill level={STATE_LEVEL[job.state] ?? "accent"}>
          {STATE_LABEL[job.state] ?? job.state}
        </Pill>
      </div>

      {active && (
        <>
          <Progress value={job.progress} />
          <div className="tiny muted mt-1">{job.message}</div>
        </>
      )}

      {job.state === "completed" && (
        <div className="tiny muted">
          {job.message}
          {typeof (job.result as { duration_seconds?: number }).duration_seconds ===
            "number" && (
            <>
              {" · "}
              {formatDuration(
                (job.result as { duration_seconds: number }).duration_seconds,
              )}
            </>
          )}
        </div>
      )}

      {Array.isArray((job.result as { warnings?: string[] }).warnings) &&
        (job.result as { warnings: string[] }).warnings.map((warning) => (
          <div key={warning} className="tiny mt-1" style={{ color: "var(--warn)" }}>
            ! {warning}
          </div>
        ))}

      {job.error && (
        <div className="alert alert-error mt-1" style={{ marginBottom: 0 }}>
          <div style={{ minWidth: 0 }}>
            <div className="alert-title">
              Falha na etapa “{job.error.stage ?? "?"}”
            </div>
            <div className="alert-body">{job.error.message}</div>
            {job.error.hint && (
              <div className="alert-body" style={{ color: "var(--warn)" }}>
                → {job.error.hint}
              </div>
            )}
          </div>
        </div>
      )}

      <div className="row mt-1">
        {active && (
          <button
            className="btn btn-sm btn-danger"
            onClick={async () => {
              await api.jobs.cancel(job.id);
              onChanged();
            }}
          >
            Cancelar
          </button>
        )}
        {job.log_file && (
          <button
            className="btn btn-sm btn-ghost"
            onClick={async () => {
              if (log !== null) {
                setLog(null);
                return;
              }
              const response = await api.jobs.log(job.id);
              setLog(response.log || "(log vazio)");
            }}
          >
            {log !== null ? "ocultar log" : "ver log"}
          </button>
        )}
      </div>

      {log !== null && <div className="code mt-1">{log}</div>}
    </div>
  );
}

export default function RenderQueue() {
  const jobs = useAsync(() => api.jobs.list(), []);

  // Renders demoram; atualizar de 3 em 3 segundos é suficiente e barato.
  useEffect(() => {
    const timer = setInterval(jobs.reload, 3000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const active = jobs.data?.jobs.filter(
    (j) => !["completed", "failed", "cancelled"].includes(j.state),
  ) ?? [];
  const finished = jobs.data?.jobs.filter((j) =>
    ["completed", "failed", "cancelled"].includes(j.state),
  ) ?? [];

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Render Queue</h1>
          <p className="page-desc">
            Um render por vez. Se algo falhar, a próxima execução reaproveita as
            etapas que já terminaram.
          </p>
        </div>
      </div>

      <ErrorBox error={jobs.error} />

      <Panel title={`Em execução (${active.length})`}>
        {active.length === 0 ? (
          <div className="tiny dim">Fila ociosa.</div>
        ) : (
          active.map((job) => (
            <JobCard key={job.id} job={job} onChanged={jobs.reload} />
          ))
        )}
      </Panel>

      <Panel title={`Histórico (${finished.length})`}>
        {finished.length === 0 ? (
          <Empty title="Nenhum render ainda" />
        ) : (
          finished
            .slice(0, 30)
            .map((job) => (
              <JobCard key={job.id} job={job} onChanged={jobs.reload} />
            ))
        )}
      </Panel>
    </div>
  );
}
