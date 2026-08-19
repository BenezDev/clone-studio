import { useState } from "react";

import {
  Alert,
  ErrorBox,
  Panel,
  Pill,
  Spinner,
  formatBytes,
  useAsync,
} from "../components/ui";
import { ModelInfo, api } from "../lib/api";

const PURPOSE_LABEL: Record<string, string> = {
  voice_cloning: "Clonagem de voz",
  lipsync: "Lip-sync",
  transcription: "Transcrição / legendas",
  generative_video: "Vídeo generativo (opcional)",
};

function CommercialBadge({ model }: { model: ModelInfo }) {
  if (model.commercial_ok) return <Pill level="ok">uso comercial ok</Pill>;
  if (model.commercial_status === "forbidden")
    return <Pill level="error">não comercial</Pill>;
  return <Pill level="warn">verificar licença</Pill>;
}

function ModelRow({ model, onInstall }: { model: ModelInfo; onInstall: () => void }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="list-item" style={{ alignItems: "flex-start" }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="row row-wrap" style={{ gap: 8 }}>
          <span className="bold">{model.display_name}</span>
          {model.installed ? (
            <Pill level="ok">instalado</Pill>
          ) : (
            <Pill>não instalado</Pill>
          )}
          <CommercialBadge model={model} />
          {!model.hardware_compatible && (
            <Pill level="error">incompatível com este hardware</Pill>
          )}
          {model.hardware_compatible && model.hardware_degraded && (
            <Pill level="warn">lento neste hardware</Pill>
          )}
          {model.optional && <Pill level="info">opcional</Pill>}
        </div>

        <div className="tiny dim mono mt-1">{model.repo}</div>
        <div className="tiny muted">
          {model.size_gb.toFixed(1)} GB · licença {model.license}
          {model.verified_at && ` · verificada em ${model.verified_at}`}
          {model.installed &&
            ` · ${formatBytes(model.size_on_disk_bytes)} em disco`}
        </div>

        {model.hardware_reasons.map((reason) => (
          <div key={reason} className="tiny" style={{ color: "var(--error)" }}>
            ! {reason}
          </div>
        ))}
        {model.commercial_blockers.map((blocker) => (
          <div key={blocker} className="tiny" style={{ color: "var(--warn)" }}>
            ! {blocker}
          </div>
        ))}

        <button
          className="btn btn-ghost btn-sm mt-1"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "menos" : "detalhes"}
        </button>

        {expanded && (
          <div className="mt-1 small muted">
            {model.notes && <p style={{ marginBottom: 8 }}>{model.notes}</p>}
            <div className="tiny">
              <div>
                fonte:{" "}
                <a
                  href={model.source}
                  target="_blank"
                  rel="noreferrer noopener"
                  style={{ color: "var(--info)" }}
                >
                  {model.source}
                </a>
              </div>
              {model.upstream_code && (
                <div>
                  código: {model.upstream_code}
                  {model.code_revision && ` @ ${model.code_revision.slice(0, 8)}`}
                </div>
              )}
              <div>revisão: {model.revision}</div>
              <div>ambiente: .envs/{model.env}</div>
              <div className="mono">{model.install_dir}</div>
            </div>

            {model.dependencies.length > 0 && (
              <div className="mt-1">
                <div className="tiny bold">Dependências com licença própria:</div>
                {model.dependencies.map((dep) => (
                  <div key={dep.name} className="tiny dim">
                    · {dep.name} — {dep.license}
                    {dep.commercial !== "allowed" && " (verificar)"}
                    {dep.note && ` — ${dep.note}`}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {!model.installed && (
        <button
          className="btn btn-sm"
          onClick={onInstall}
          disabled={!model.hardware_compatible}
          title={
            model.hardware_compatible
              ? `Baixar ~${model.size_gb.toFixed(1)} GB`
              : "Hardware insuficiente"
          }
        >
          Instalar ({model.size_gb.toFixed(1)} GB)
        </button>
      )}
    </div>
  );
}

export default function Models() {
  const [mode, setMode] = useState<"personal" | "commercial">("personal");
  const models = useAsync(() => api.models.list(mode), [mode]);
  const [installing, setInstalling] = useState<string | null>(null);
  const [log, setLog] = useState<string>("");
  const [error, setError] = useState<unknown>(null);

  async function install(key: string, sizeGb: number) {
    const confirmed = window.confirm(
      `Baixar ${key}?\n\nTamanho aproximado: ${sizeGb.toFixed(1)} GB.\n` +
        "O download acontece em segundo plano; nada mais é baixado sem sua ação.",
    );
    if (!confirmed) return;

    setError(null);
    setInstalling(key);
    setLog("");
    try {
      await api.models.install(key);
      const timer = setInterval(async () => {
        const response = await api.models.installLog(key);
        setLog(response.log);
      }, 2500);
      setTimeout(() => {
        clearInterval(timer);
        setInstalling(null);
        models.reload();
      }, 1000 * 60 * 30);
    } catch (err) {
      setError(err);
      setInstalling(null);
    }
  }

  const byPurpose: Record<string, ModelInfo[]> = {};
  for (const model of models.data?.models ?? []) {
    (byPurpose[model.purpose] ??= []).push(model);
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Models</h1>
          <p className="page-desc">
            Nenhuma licença é assumida em silêncio. Cada peso registra fonte,
            versão e o que a licença permite — e nada é baixado sem sua ação.
          </p>
        </div>
        <select
          className="select"
          style={{ width: 210 }}
          value={mode}
          onChange={(e) => setMode(e.target.value as "personal" | "commercial")}
        >
          <option value="personal">Modo pessoal</option>
          <option value="commercial">Modo comercial</option>
        </select>
      </div>

      <ErrorBox error={error || models.error} />

      {mode === "commercial" && (
        <Alert level="info" title="Modo comercial">
          Só aparecem como recomendados os modelos cuja licença — e a de todas as
          dependências — permite uso comercial. Isto é orientação técnica a
          partir do que está registrado, não aconselhamento jurídico.
        </Alert>
      )}

      {installing && (
        <Panel title={`Baixando ${installing}`}>
          <div className="code">{log || "iniciando…"}</div>
          <button className="btn btn-sm mt-1" onClick={models.reload}>
            Atualizar estado
          </button>
        </Panel>
      )}

      {Object.entries(byPurpose).map(([purpose, list]) => (
        <Panel
          key={purpose}
          title={PURPOSE_LABEL[purpose] ?? purpose}
          desc={
            models.data?.recommended[purpose]?.length
              ? `Recomendado neste modo: ${models.data.recommended[purpose].join(", ")}`
              : "Nenhum modelo recomendado neste modo."
          }
        >
          {list.map((model) => (
            <ModelRow
              key={model.key}
              model={model}
              onInstall={() => install(model.key, model.size_gb)}
            />
          ))}
        </Panel>
      ))}

      {models.loading && <Spinner />}
    </div>
  );
}
