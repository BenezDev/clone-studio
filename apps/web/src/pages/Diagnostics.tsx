import { useState } from "react";

import {
  Alert,
  Dot,
  ErrorBox,
  Panel,
  Spinner,
  useAsync,
} from "../components/ui";
import { Check, api } from "../lib/api";

const TEST_LABELS: Record<string, string> = {
  ffmpeg: "Test FFmpeg",
  voice: "Test Voice",
  captions: "Test Captions",
  lipsync: "Test Lip Sync",
  ollama: "Test Ollama",
  comfyui: "Test ComfyUI",
};

const GROUP_LABELS: Record<string, string> = {
  hardware: "Hardware",
  ferramentas: "Ferramentas",
  ambientes: "Ambientes Python",
  modelos: "Modelos",
  rede: "Rede e portas",
  privacidade: "Privacidade",
  identidade: "Identidade",
  "serviços": "Serviços externos",
  testes: "Testes",
};

function CheckRow({ check }: { check: Check }) {
  return (
    <div className="list-item">
      <Dot level={check.level} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="row" style={{ gap: 8 }}>
          <span className="bold small">{check.name}</span>
          <span className="small muted truncate">{check.summary}</span>
        </div>
        {check.detail && <div className="tiny dim">{check.detail}</div>}
        {check.hint && (
          <div className="tiny" style={{ color: "var(--warn)" }}>
            → {check.hint}
          </div>
        )}
      </div>
    </div>
  );
}

export default function Diagnostics() {
  const report = useAsync(() => api.diagnostics.run(), []);
  const [results, setResults] = useState<Record<string, Check>>({});
  const [running, setRunning] = useState<string | null>(null);
  const [testError, setTestError] = useState<unknown>(null);

  async function runTest(name: string) {
    setRunning(name);
    setTestError(null);
    try {
      const result = await api.diagnostics.runTest(name);
      setResults((prev) => ({ ...prev, [name]: result }));
    } catch (err) {
      setTestError(err);
    } finally {
      setRunning(null);
    }
  }

  const groups: Record<string, Check[]> = {};
  for (const check of report.data?.checks ?? []) {
    (groups[check.group] ??= []).push(check);
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Diagnostics</h1>
          <p className="page-desc">
            Estado real do ambiente: hardware, ambientes Python isolados,
            modelos, portas e engines.
          </p>
        </div>
        <div className="row">
          <button
            className="btn"
            onClick={async () => {
              await api.diagnostics.refreshHardware();
              report.reload();
            }}
          >
            Redetectar hardware
          </button>
          <button className="btn" onClick={report.reload} disabled={report.loading}>
            {report.loading ? <Spinner /> : "Atualizar"}
          </button>
        </div>
      </div>

      <ErrorBox error={report.error} />
      <ErrorBox error={testError} />

      {report.data && (
        <Alert
          level={report.data.healthy ? "ok" : "error"}
          title={
            report.data.healthy
              ? "Ambiente saudável"
              : `${report.data.error_count} problema(s) bloqueante(s)`
          }
        >
          {report.data.warning_count > 0 &&
            `${report.data.warning_count} aviso(s) não bloqueante(s).`}
        </Alert>
      )}

      <Panel
        title="Testes"
        desc="Cada botão executa a engine de verdade e reporta o erro completo se falhar."
      >
        <div className="row row-wrap" style={{ marginBottom: 14 }}>
          {Object.entries(TEST_LABELS).map(([key, label]) => (
            <button
              key={key}
              className="btn btn-sm"
              onClick={() => runTest(key)}
              disabled={running !== null}
            >
              {running === key ? <Spinner /> : null} {label}
            </button>
          ))}
        </div>

        {Object.keys(results).length === 0 ? (
          <div className="tiny dim">
            Nenhum teste executado ainda. O teste de voz e o de lip-sync sobem os
            modelos de verdade e podem levar alguns segundos.
          </div>
        ) : (
          Object.values(results).map((check) => (
            <CheckRow key={check.name} check={check} />
          ))
        )}
      </Panel>

      {Object.entries(groups).map(([group, checks]) => (
        <Panel key={group} title={GROUP_LABELS[group] ?? group}>
          {checks.map((check) => (
            <CheckRow key={check.name + check.summary} check={check} />
          ))}
        </Panel>
      ))}
    </div>
  );
}
