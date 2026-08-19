import { useState } from "react";

import {
  Alert,
  ErrorBox,
  Field,
  Panel,
  Pill,
  Spinner,
  formatBytes,
  useAsync,
} from "../components/ui";
import { api } from "../lib/api";

export default function SettingsPage() {
  const settings = useAsync(() => api.settings.get(), []);
  const cache = useAsync(() => api.settings.cache(), []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  const config = settings.data?.settings;

  async function update(overrides: Record<string, unknown>) {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      await api.settings.patch(overrides);
      settings.reload();
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  }

  if (!config) {
    return (
      <div className="page">
        <ErrorBox error={settings.error} />
        {settings.loading && <Spinner />}
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-desc">
            As alterações vão para{" "}
            <span className="mono">{settings.data?.config_file}</span>.
            O <span className="mono">default.yaml</span> nunca é modificado.
          </p>
        </div>
        {saved && <Pill level="ok">salvo</Pill>}
      </div>

      <ErrorBox error={error} />

      <Panel
        title="Privacidade"
        desc="Estas travas são estruturais: telemetria, analytics e envio de mídia não podem ser ligados pela configuração."
      >
        <div className="row row-wrap">
          <Pill level="ok">telemetria desligada</Pill>
          <Pill level="ok">analytics desligado</Pill>
          <Pill level="ok">sem upload automático</Pill>
          <Pill level={config.app.host.startsWith("127.") ? "ok" : "error"}>
            bind {config.app.host}
          </Pill>
        </div>
        <div className="hint mt-1">
          O backend recusa conexões que não venham deste computador, mesmo se a
          porta for exposta por engano.
        </div>
      </Panel>

      <div className="grid grid-2">
        <Panel title="Voz">
          <Field
            label="Modelo"
            hint="auto escolhe 0.6B em CPU/Low VRAM e 1.7B acima disso"
          >
            <select
              className="select"
              value={config.tts.model}
              onChange={(e) => update({ tts: { model: e.target.value } })}
            >
              <option value="auto">auto (recomendado)</option>
              <option value={config.tts.models.low}>
                0.6B — mais rápido
              </option>
              <option value={config.tts.models.high}>
                1.7B — melhor qualidade
              </option>
            </select>
          </Field>

          <Field label="Idioma">
            <input className="input" value={config.tts.language} readOnly />
          </Field>

          <Field label={`Variantes no A/B — ${config.tts.ab_variants}`}>
            <input
              type="range"
              className="slider"
              min={1}
              max={5}
              value={config.tts.ab_variants}
              onChange={(e) =>
                update({ tts: { ab_variants: Number(e.target.value) } })
              }
            />
          </Field>
        </Panel>

        <Panel title="Vídeo">
          <Field label="Resolução">
            <input
              className="input"
              value={`${config.video.width}×${config.video.height}`}
              readOnly
            />
          </Field>

          <Field label={`FPS — ${config.video.fps}`}>
            <select
              className="select"
              value={config.video.fps}
              onChange={(e) => update({ video: { fps: Number(e.target.value) } })}
            >
              <option value={24}>24</option>
              <option value={25}>25</option>
              <option value={30}>30 (padrão)</option>
              <option value={60}>60</option>
            </select>
          </Field>

          <Field label="Codec" hint="H.264 é o mais compatível entre plataformas">
            <select
              className="select"
              value={config.video.video_codec}
              onChange={(e) =>
                update({ video: { video_codec: e.target.value } })
              }
            >
              <option value="libx264">H.264 (padrão)</option>
              <option value="libx265">HEVC</option>
            </select>
          </Field>

          <Field label={`Qualidade (CRF) — ${config.video.crf}`}>
            <input
              type="range"
              className="slider"
              min={14}
              max={28}
              value={config.video.crf}
              onChange={(e) => update({ video: { crf: Number(e.target.value) } })}
            />
          </Field>
        </Panel>

        <Panel title="Legendas">
          <Field label="Preset padrão">
            <select
              className="select"
              value={config.captions.preset}
              onChange={(e) =>
                update({ captions: { preset: e.target.value } })
              }
            >
              {["minimal", "hormozi", "clean", "big_tech", "podcast", "karaoke"].map(
                (value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ),
              )}
            </select>
          </Field>

          <Field
            label={`Margem inferior segura — ${(config.captions.safe_bottom * 100).toFixed(0)}%`}
            hint="espaço reservado à UI da plataforma"
          >
            <input
              type="range"
              className="slider"
              min={0.1}
              max={0.35}
              step={0.01}
              value={config.captions.safe_bottom}
              onChange={(e) =>
                update({ captions: { safe_bottom: Number(e.target.value) } })
              }
            />
          </Field>
        </Panel>

        <Panel title="Desempenho">
          <Field
            label={`Threads de CPU — ${config.hardware.cpu_threads || "automático"}`}
            hint="0 usa os núcleos físicos"
          >
            <input
              type="range"
              className="slider"
              min={0}
              max={32}
              value={config.hardware.cpu_threads}
              onChange={(e) =>
                update({ hardware: { cpu_threads: Number(e.target.value) } })
              }
            />
          </Field>

          <Field label="Perfil de hardware">
            <select
              className="select"
              value={config.hardware.profile}
              onChange={(e) =>
                update({ hardware: { profile: e.target.value } })
              }
            >
              <option value="auto">
                auto — detectado: {settings.data?.active_profile}
              </option>
              {["CPU_ONLY", "LOW_VRAM", "MID_VRAM", "HIGH_VRAM", "EXTREME"].map(
                (value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ),
              )}
            </select>
          </Field>

          <Field label="Preview" hint="segundos gerados no preview; 0 = vídeo inteiro">
            <input
              type="number"
              className="input"
              min={0}
              max={120}
              value={config.preview.max_seconds}
              onChange={(e) =>
                update({ preview: { max_seconds: Number(e.target.value) } })
              }
            />
          </Field>
        </Panel>
      </div>

      <Panel
        title="Cache"
        desc="Preprocessamento de template, embeddings de voz e transcrições. Limpar força o recálculo."
      >
        {cache.data?.namespaces.map((namespace) => (
          <div key={namespace.name} className="list-item">
            <div style={{ flex: 1 }}>
              <div className="small bold">{namespace.name}</div>
              <div className="tiny dim mono truncate">{namespace.path}</div>
            </div>
            <span className="small muted">{formatBytes(namespace.size_bytes)}</span>
            <button
              className="btn btn-sm btn-ghost"
              onClick={async () => {
                await api.settings.clearCache(namespace.name);
                cache.reload();
              }}
            >
              limpar
            </button>
          </div>
        ))}
        <div className="row row-between mt-1">
          <span className="small bold">total</span>
          <span className="small">{formatBytes(cache.data?.total_bytes ?? 0)}</span>
        </div>
      </Panel>

      <Panel title="Diretórios">
        {Object.entries(settings.data?.paths ?? {}).map(([key, value]) => (
          <div key={key} className="list-item">
            <span className="small" style={{ width: 90 }}>
              {key}
            </span>
            <span className="tiny dim mono truncate">{value}</span>
          </div>
        ))}
      </Panel>

      {saving && <Alert level="info">Salvando…</Alert>}
    </div>
  );
}
