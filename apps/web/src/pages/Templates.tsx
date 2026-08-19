import { useRef, useState } from "react";

import {
  Alert,
  Empty,
  ErrorBox,
  Field,
  Panel,
  Pill,
  SafeZones,
  Spinner,
  formatDuration,
  useAsync,
} from "../components/ui";
import { Recorder } from "../components/Recorder";
import { Template, api } from "../lib/api";

const OPTIONS = {
  style: ["talking_head", "podcast", "selfie", "seated", "standing", "walking"],
  energy: ["low", "medium", "high"],
  gestures: ["none", "low", "medium", "high"],
  camera: ["close", "medium_close", "medium", "wide"],
  background: ["dark", "light", "neutral", "office", "outdoor"],
};

function TemplateEditor({
  template,
  onSaved,
}: {
  template: Template;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState(template);
  const [saving, setSaving] = useState(false);
  const [showSafe, setShowSafe] = useState(false);

  async function save() {
    setSaving(true);
    try {
      await api.templates.update(template.id, {
        style: draft.style,
        energy: draft.energy,
        gestures: draft.gestures,
        camera: draft.camera,
        background: draft.background,
        loopable: draft.loopable,
        enabled: draft.enabled,
        notes: draft.notes,
      });
      onSaved();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="grid grid-2">
      <div>
        <div className="video-frame" style={{ maxWidth: 260 }}>
          <video src={template.preview_url} controls muted playsInline />
          <SafeZones show={showSafe} />
        </div>
        <label className="checkbox mt-1">
          <input
            type="checkbox"
            checked={showSafe}
            onChange={(e) => setShowSafe(e.target.checked)}
          />
          zonas seguras (só no editor)
        </label>
        <div className="tiny dim mt-1">
          {template.width}×{template.height} · {template.fps.toFixed(0)} fps ·{" "}
          {formatDuration(template.duration)}
        </div>
      </div>

      <div>
        {(["style", "energy", "gestures", "camera", "background"] as const).map(
          (key) => (
            <Field key={key} label={key}>
              <select
                className="select"
                value={draft[key] as string}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
              >
                {OPTIONS[key].map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </Field>
          ),
        )}

        <label className="checkbox" style={{ marginBottom: 10 }}>
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
          />
          disponível para seleção automática
        </label>

        <button className="btn btn-primary" onClick={save} disabled={saving}>
          {saving ? <Spinner /> : null} Salvar
        </button>
      </div>
    </div>
  );
}

export default function Templates() {
  const templates = useAsync(() => api.templates.list(true), []);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const [source, setSource] = useState<"upload" | "record">("record");
  const [recorded, setRecorded] = useState<File | null>(null);
  // Remonta o gravador depois de enviar: zera o estado dele e libera a câmera
  // em vez de deixá-la aberta entre um take e outro.
  const [recorderNonce, setRecorderNonce] = useState(0);

  async function upload() {
    const file = source === "record" ? recorded : fileRef.current?.files?.[0];
    if (!file) {
      setError(
        new Error(
          source === "record"
            ? "Grave um vídeo antes de enviar."
            : "Escolha um arquivo de vídeo.",
        ),
      );
      return;
    }
    setBusy(true);
    setError(null);
    setWarnings([]);
    try {
      const form = new FormData();
      form.append("video", file);
      const response = await api.templates.upload(form);
      setWarnings(response.warnings);
      if (fileRef.current) fileRef.current.value = "";
      setRecorded(null);
      setRecorderNonce((value) => value + 1);
      templates.reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function reindex() {
    setBusy(true);
    try {
      await api.templates.index();
      templates.reload();
    } finally {
      setBusy(false);
    }
  }

  const current = templates.data?.templates.find((t) => t.id === selected);

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Templates</h1>
          <p className="page-desc">
            Seus vídeos reais são a matéria-prima do Production Mode. Nada é
            gerado por diffusion: rosto, roupa, cenário e gestos são os seus.
          </p>
        </div>
        <button className="btn" onClick={reindex} disabled={busy}>
          {busy ? <Spinner /> : null} Reindexar pasta
        </button>
      </div>

      <ErrorBox error={error || templates.error} />
      {warnings.map((warning) => (
        <Alert key={warning} level="warn">
          {warning}
        </Alert>
      ))}

      <Panel
        title="Adicionar template"
        desc="Grave direto pela câmera ou envie um arquivo. Junte de 10 a 30 vídeos verticais seus: olhando para a câmera, em pé, sentado, gesticulando, estilo podcast, estilo selfie, enquadramentos e roupas diferentes. Quanto mais material, menos repetição perceptível."
      >
        <div className="source-tabs">
          <button
            type="button"
            className={`source-tab ${source === "record" ? "active" : ""}`}
            onClick={() => setSource("record")}
          >
            Gravar agora
          </button>
          <button
            type="button"
            className={`source-tab ${source === "upload" ? "active" : ""}`}
            onClick={() => setSource("upload")}
          >
            Enviar arquivo
          </button>
        </div>

        {source === "upload" ? (
          <div className="row row-wrap">
            <input
              ref={fileRef}
              type="file"
              className="input"
              accept="video/mp4,video/quicktime,video/x-matroska,video/webm"
              style={{ maxWidth: 420 }}
            />
            <button className="btn btn-primary" onClick={upload} disabled={busy}>
              {busy ? <Spinner /> : null} Enviar
            </button>
          </div>
        ) : (
          <>
            <Recorder
              key={recorderNonce}
              kind="video"
              maxSeconds={300}
              idealRange={[15, 90]}
              onRecorded={setRecorded}
            />
            <button
              className="btn btn-primary mt-2"
              onClick={upload}
              disabled={busy || !recorded}
            >
              {busy ? <Spinner /> : null} Usar esta gravação
            </button>
          </>
        )}
        <div className="hint mt-1">
          Ou copie os arquivos direto para{" "}
          <span className="mono">{templates.data?.directory}</span> e clique em
          reindexar.
        </div>
      </Panel>

      {templates.data && (
        <div className="row" style={{ marginBottom: 14 }}>
          <Pill level="info">
            {templates.data.templates.length} template(s)
          </Pill>
          <Pill level="info">
            {formatDuration(templates.data.total_duration)} de material
          </Pill>
        </div>
      )}

      {templates.data?.templates.length === 0 ? (
        <Empty title="Nenhum template ainda">
          Sem template de vídeo o pipeline não tem o que sincronizar.
        </Empty>
      ) : (
        <div className="grid grid-3">
          {templates.data?.templates.map((template) => (
            <div
              key={template.id}
              className={`template-card ${
                selected === template.id ? "selected" : ""
              }`}
              onClick={() =>
                setSelected(selected === template.id ? null : template.id)
              }
            >
              <video
                className="template-preview"
                src={template.preview_url}
                muted
                playsInline
                preload="metadata"
                onMouseEnter={(e) => void e.currentTarget.play().catch(() => {})}
                onMouseLeave={(e) => {
                  e.currentTarget.pause();
                  e.currentTarget.currentTime = 0;
                }}
              />
              <div className="template-body">
                <div className="row row-between">
                  <span className="small bold truncate">{template.id}</span>
                  <span className="tiny dim">
                    {formatDuration(template.duration)}
                  </span>
                </div>
                <div className="row row-wrap mt-1" style={{ gap: 5 }}>
                  <Pill>{template.style}</Pill>
                  <Pill>energia {template.energy}</Pill>
                  {template.orientation !== "vertical" && (
                    <Pill level="warn">horizontal</Pill>
                  )}
                  {!template.enabled && <Pill level="error">desativado</Pill>}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {current && (
        <Panel title={`Editar · ${current.id}`} className="mt-2">
          <TemplateEditor template={current} onSaved={templates.reload} />
        </Panel>
      )}
    </div>
  );
}
