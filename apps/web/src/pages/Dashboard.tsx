import { Link } from "react-router-dom";

import {
  Alert,
  Empty,
  ErrorBox,
  Panel,
  Pill,
  Progress,
  Stat,
  formatDate,
  formatDuration,
  useAsync,
} from "../components/ui";
import { api } from "../lib/api";

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

export default function Dashboard() {
  const projects = useAsync(() => api.projects.list(), []);
  const jobs = useAsync(() => api.jobs.list(), []);
  const diagnostics = useAsync(() => api.diagnostics.run(false), []);
  const voices = useAsync(() => api.voice.profiles(), []);
  const templates = useAsync(() => api.templates.list(), []);

  const blocking = diagnostics.data?.checks.filter((c) => c.level === "error") ?? [];
  const activeJobs =
    jobs.data?.jobs.filter(
      (j) => !["completed", "failed", "cancelled"].includes(j.state),
    ) ?? [];
  const recent = projects.data?.projects.slice(0, 6) ?? [];

  const ready =
    (voices.data?.profiles.length ?? 0) > 0 &&
    (templates.data?.templates.length ?? 0) > 0 &&
    blocking.length === 0;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Dashboard</h1>
          <p className="page-desc">
            Estúdio local de vídeos verticais com sua voz e seu rosto. Nada sai
            desta máquina.
          </p>
        </div>
        <Link to="/new" className="btn btn-primary">
          Novo vídeo
        </Link>
      </div>

      <ErrorBox error={projects.error || jobs.error} />

      {!ready && !diagnostics.loading && (
        <Alert level="warn" title="Configuração incompleta">
          <ul style={{ marginLeft: 16, marginTop: 4 }}>
            {(voices.data?.profiles.length ?? 0) === 0 && (
              <li>
                Nenhuma voz cadastrada —{" "}
                <Link to="/voice" style={{ color: "var(--accent)" }}>
                  cadastre sua voz
                </Link>
                .
              </li>
            )}
            {(templates.data?.templates.length ?? 0) === 0 && (
              <li>
                Nenhum template de vídeo —{" "}
                <Link to="/templates" style={{ color: "var(--accent)" }}>
                  adicione vídeos seus
                </Link>
                .
              </li>
            )}
            {blocking.map((check) => (
              <li key={check.name}>
                {check.name}: {check.summary}
                {check.hint ? ` — ${check.hint}` : ""}
              </li>
            ))}
          </ul>
        </Alert>
      )}

      <div className="grid grid-4" style={{ marginBottom: 18 }}>
        <Stat
          label="Projetos"
          value={projects.data?.projects.length ?? "—"}
          sub="vídeos criados"
        />
        <Stat
          label="Vozes"
          value={voices.data?.profiles.length ?? "—"}
          sub={
            voices.data?.profiles[0]
              ? `${voices.data.profiles[0].references.length} amostra(s)`
              : "nenhuma cadastrada"
          }
        />
        <Stat
          label="Templates"
          value={templates.data?.templates.length ?? "—"}
          sub={
            templates.data
              ? `${formatDuration(templates.data.total_duration)} de material`
              : ""
          }
        />
        <Stat
          label="Fila"
          value={activeJobs.length}
          sub={activeJobs.length ? "em execução" : "ociosa"}
        />
      </div>

      {activeJobs.length > 0 && (
        <Panel
          title="Em execução"
          action={
            <Link to="/queue" className="btn btn-ghost btn-sm">
              ver fila
            </Link>
          }
        >
          {activeJobs.map((job) => (
            <div key={job.id} style={{ marginBottom: 14 }}>
              <div className="row row-between" style={{ marginBottom: 5 }}>
                <span className="small">{job.project_id}</span>
                <Pill level="accent">{STATE_LABEL[job.state] ?? job.state}</Pill>
              </div>
              <Progress value={job.progress} />
              <div className="tiny dim mt-1">{job.message}</div>
            </div>
          ))}
        </Panel>
      )}

      <Panel
        title="Projetos recentes"
        action={
          <Link to="/new" className="btn btn-ghost btn-sm">
            + novo
          </Link>
        }
      >
        {recent.length === 0 ? (
          <Empty
            title="Nenhum projeto ainda"
            action={
              <Link to="/new" className="btn btn-primary">
                Criar o primeiro vídeo
              </Link>
            }
          >
            Comece por uma ideia — o roteiro, a voz e o vídeo saem daqui.
          </Empty>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Projeto</th>
                <th>Atualizado</th>
                <th>Etapas</th>
                <th>Saída</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {recent.map((project) => {
                const done = Object.values(project.stages).filter(
                  (s) => s.status === "completed",
                ).length;
                return (
                  <tr key={project.id}>
                    <td>
                      <div className="truncate" style={{ maxWidth: 320 }}>
                        {project.title}
                      </div>
                      <div className="tiny dim mono truncate">{project.id}</div>
                    </td>
                    <td className="small muted">{formatDate(project.updated_at)}</td>
                    <td className="small muted">{done} concluída(s)</td>
                    <td>
                      {project.renders.length ? (
                        <Pill level="ok">{project.renders.length} render(s)</Pill>
                      ) : (
                        <span className="tiny dim">—</span>
                      )}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <Link
                        to={`/new/${project.id}`}
                        className="btn btn-ghost btn-sm"
                      >
                        abrir
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
