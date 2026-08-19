import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import {
  Alert,
  ErrorBox,
  Field,
  Panel,
  Pill,
  Progress,
  SafeZones,
  Spinner,
  formatDuration,
  useAsync,
} from "../components/ui";
import { Job, Project, Script, api } from "../lib/api";
import { ScriptLibrary } from "../components/ScriptLibrary";

const STEPS = [
  "Ideia",
  "Roteiro",
  "Voz",
  "Template",
  "Edição",
  "Legendas",
  "Preview",
  "Render",
] as const;

const STATE_LABEL: Record<string, string> = {
  queued: "na fila",
  preparing: "preparando",
  tts: "gerando voz",
  lipsync: "lip-sync",
  captions: "legendas",
  editing: "montando base",
  rendering: "renderizando",
  completed: "concluído",
  failed: "falhou",
  cancelled: "cancelado",
};

export default function NewVideo() {
  const { projectId } = useParams();
  const navigate = useNavigate();

  const [step, setStep] = useState(0);
  const [project, setProject] = useState<Project | null>(null);
  const [script, setScript] = useState<Script | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [libraryNotice, setLibraryNotice] = useState("");

  const [idea, setIdea] = useState("");
  const [preset, setPreset] = useState("viral");
  const [duration, setDuration] = useState(45);

  const [job, setJob] = useState<Job | null>(null);
  const [showSafe, setShowSafe] = useState(true);

  const voices = useAsync(() => api.voice.profiles(), []);
  const templates = useAsync(() => api.templates.list(), []);
  const presets = useAsync(() => api.settings.presets(), []);
  const llm = useAsync(() => api.script.status(), []);

  // Carrega o projeto quando aberto pela URL.
  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    (async () => {
      try {
        const loaded = await api.projects.get(projectId);
        if (cancelled) return;
        setProject(loaded);
        setIdea(loaded.idea);
        const response = await api.projects.getScript(projectId);
        if (!cancelled && response.script) {
          setScript(response.script);
          setStep((current) => (current === 0 ? 2 : current));
        } else if (!cancelled) {
          setStep((current) => (current === 0 ? 1 : current));
        }
      } catch (err) {
        if (!cancelled) setError(err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Acompanha o job em execução por SSE.
  useEffect(() => {
    if (!job || ["completed", "failed", "cancelled"].includes(job.state)) return;
    const source = api.jobs.stream(job.id);
    source.onmessage = (event) => {
      const updated = JSON.parse(event.data) as Job;
      setJob(updated);
      if (["completed", "failed", "cancelled"].includes(updated.state)) {
        source.close();
        if (projectId) api.projects.get(projectId).then(setProject).catch(() => {});
      }
    };
    source.onerror = () => source.close();
    return () => source.close();
  }, [job?.id, job?.state, projectId]);

  async function createProject() {
    if (!idea.trim()) {
      setError(new Error("Descreva a ideia do vídeo."));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api.projects.create({ idea, title: idea.slice(0, 70) });
      setProject(created);
      navigate(`/new/${created.id}`, { replace: true });
      setStep(1);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function generateScript() {
    if (!project) return;
    setBusy(true);
    setError(null);
    try {
      const response = await api.script.generate({
        idea: project.idea,
        preset,
        duration,
        model: llm.data?.selected,
      });
      setScript(response.script);
      await api.projects.saveScript(project.id, response.script);
      setProject(await api.projects.get(project.id));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  function manualScript() {
    if (!project) return;
    setScript({
      title: project.title,
      hook: "",
      estimated_duration: duration,
      voice_style: { emotion: "confident", speed: 1.05 },
      scenes: [
        { start: 0, text: project.idea, visual: "talking_head", broll_prompt: null, emphasis_words: [] },
      ],
      cta: "",
      caption: "",
      hashtags: [],
      preset: "manual",
      model: "",
    });
  }

  async function saveScript() {
    if (!project || !script) return;
    setBusy(true);
    setError(null);
    try {
      await api.projects.saveScript(project.id, script);
      setProject(await api.projects.get(project.id));
      setStep(2);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function updateProject(payload: Record<string, unknown>) {
    if (!project) return;
    setError(null);
    try {
      setProject(await api.projects.update(project.id, payload));
    } catch (err) {
      setError(err);
    }
  }

  async function render(preview: boolean) {
    if (!project) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.projects.render(project.id, { preview });
      setJob(created);
      setStep(preview ? 6 : 7);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const scriptWords = script?.scenes.reduce(
    (total, scene) => total + scene.text.trim().split(/\s+/).filter(Boolean).length,
    0,
  ) ?? 0;
  const estimatedSeconds = scriptWords / 2.6;

  const renderPath = project?.outputs?.final ?? project?.outputs?.preview;
  const renderUrl =
    project && renderPath
      ? api.mediaUrl(`projects/${project.id}/${renderPath}`)
      : null;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">{project ? project.title : "Novo vídeo"}</h1>
          <p className="page-desc">
            {project ? (
              <span className="mono tiny">{project.id}</span>
            ) : (
              "Da ideia ao MP4 vertical, tudo local."
            )}
          </p>
        </div>
      </div>

      <div className="steps">
        {STEPS.map((label, index) => (
          <button
            key={label}
            className={`step ${step === index ? "active" : ""} ${
              step > index ? "done" : ""
            }`}
            onClick={() => setStep(index)}
            disabled={!project && index > 0}
          >
            <span className="step-num">{step > index ? "✓" : index + 1}</span>
            {label}
          </button>
        ))}
      </div>

      <ErrorBox error={error} />

      {/* 1 — Ideia --------------------------------------------------------- */}
      {step === 0 && (
        <Panel
          title="Qual é a ideia?"
          desc="Uma frase basta. O roteiro sai daqui."
        >
          <Field label="Ideia">
            <textarea
              className="textarea"
              value={idea}
              onChange={(e) => setIdea(e.target.value)}
              placeholder="Por que programadores deveriam aprender vendas"
              autoFocus
            />
          </Field>
          <button
            className="btn btn-primary"
            onClick={createProject}
            disabled={busy || !idea.trim()}
          >
            {busy ? <Spinner /> : null} Criar projeto
          </button>
        </Panel>
      )}

      {/* 2 — Roteiro ------------------------------------------------------- */}
      {step === 1 && project && (
        <>
          <Panel title="Roteiro" desc="Gere com o Ollama ou escreva você mesmo. Você edita tudo antes de qualquer coisa ser gerada.">
            {!llm.data?.available && (
              <Alert level="info" title="Ollama offline">
                {llm.data?.detail ??
                  "O Ollama é opcional — escreva o roteiro à mão e siga normalmente."}
              </Alert>
            )}

            <div className="grid grid-3">
              <Field label="Estilo">
                <select
                  className="select"
                  value={preset}
                  onChange={(e) => setPreset(e.target.value)}
                >
                  {presets.data?.script.map((option) => (
                    <option key={option.key} value={option.key}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </Field>

              <Field label={`Duração alvo — ${duration}s`}>
                <input
                  type="range"
                  className="slider"
                  min={15}
                  max={120}
                  step={5}
                  value={duration}
                  onChange={(e) => setDuration(Number(e.target.value))}
                />
              </Field>

              <Field label="Modelo">
                <select
                  className="select"
                  value={llm.data?.selected ?? ""}
                  disabled={!llm.data?.available}
                  onChange={() => undefined}
                >
                  {llm.data?.models.map((model) => (
                    <option key={model.name} value={model.name}>
                      {model.name}
                    </option>
                  ))}
                  {!llm.data?.available && <option>indisponível</option>}
                </select>
              </Field>
            </div>

            <div className="row">
              <button
                className="btn btn-primary"
                onClick={generateScript}
                disabled={busy || !llm.data?.available}
              >
                {busy ? <Spinner /> : null} Gerar roteiro
              </button>
              <button className="btn" onClick={manualScript}>
                Escrever à mão
              </button>
            </div>
          </Panel>

          {!script && project && (
            <ScriptLibrary
              onPick={async (escolhido, aviso) => {
                setScript(escolhido);
                if (aviso) setLibraryNotice(aviso);
                try {
                  await api.projects.saveScript(project.id, escolhido);
                  setProject(await api.projects.get(project.id));
                } catch (err) {
                  setError(err);
                }
              }}
            />
          )}

          {libraryNotice && (
            <Alert level="warn" title="Antes de publicar">
              {libraryNotice}
            </Alert>
          )}

          {script && (
            <Panel
              title="Editar roteiro"
              action={
                <span className="tiny dim">
                  {scriptWords} palavras · ~{formatDuration(estimatedSeconds)}
                </span>
              }
            >
              <Field label="Título">
                <input
                  className="input"
                  value={script.title}
                  onChange={(e) => setScript({ ...script, title: e.target.value })}
                />
              </Field>

              <Field label="Hook" hint="os primeiros 2 segundos decidem tudo">
                <input
                  className="input"
                  value={script.hook}
                  onChange={(e) => setScript({ ...script, hook: e.target.value })}
                />
              </Field>

              {script.scenes.map((scene, index) => (
                <Field key={index} label={`Cena ${index + 1} · ${scene.start}s`}>
                  <textarea
                    className="textarea"
                    style={{ minHeight: 70 }}
                    value={scene.text}
                    onChange={(e) => {
                      const scenes = [...script.scenes];
                      scenes[index] = { ...scene, text: e.target.value };
                      setScript({ ...script, scenes });
                    }}
                  />
                </Field>
              ))}

              <div className="row" style={{ marginBottom: 14 }}>
                <button
                  className="btn btn-sm"
                  onClick={() =>
                    setScript({
                      ...script,
                      scenes: [
                        ...script.scenes,
                        {
                          start: 0,
                          text: "",
                          visual: "talking_head",
                          broll_prompt: null,
                          emphasis_words: [],
                        },
                      ],
                    })
                  }
                >
                  + cena
                </button>
                {script.scenes.length > 1 && (
                  <button
                    className="btn btn-sm btn-ghost"
                    onClick={() =>
                      setScript({ ...script, scenes: script.scenes.slice(0, -1) })
                    }
                  >
                    − última cena
                  </button>
                )}
              </div>

              <Field label="CTA">
                <input
                  className="input"
                  value={script.cta}
                  onChange={(e) => setScript({ ...script, cta: e.target.value })}
                />
              </Field>

              <button className="btn btn-primary" onClick={saveScript} disabled={busy}>
                {busy ? <Spinner /> : null} Salvar e continuar
              </button>
            </Panel>
          )}
        </>
      )}

      {/* 3 — Voz ----------------------------------------------------------- */}
      {step === 2 && project && (
        <Panel title="Voz" desc="Qual perfil e com que entrega.">
          {voices.data?.profiles.length === 0 && (
            <Alert level="error" title="Nenhuma voz cadastrada">
              Cadastre sua voz na página Voice antes de gerar.
            </Alert>
          )}

          <div className="grid grid-3">
            <Field label="Perfil">
              <select
                className="select"
                value={project.voice.profile_id}
                onChange={(e) =>
                  updateProject({ voice: { profile_id: e.target.value } })
                }
              >
                {voices.data?.profiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.display_name}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Estilo">
              <select
                className="select"
                value={project.voice.emotion}
                onChange={(e) =>
                  updateProject({ voice: { emotion: e.target.value } })
                }
              >
                <option value="">principal</option>
                {[
                  ...new Set(
                    voices.data?.profiles
                      .find((p) => p.id === project.voice.profile_id)
                      ?.references.map((r) => r.label) ?? [],
                  ),
                ].map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </Field>

            <Field label={`Velocidade — ${project.voice.speed.toFixed(2)}x`}>
              <input
                type="range"
                className="slider"
                min={0.7}
                max={1.4}
                step={0.05}
                value={project.voice.speed}
                onChange={(e) =>
                  updateProject({ voice: { speed: Number(e.target.value) } })
                }
              />
            </Field>
          </div>

          <button className="btn btn-primary" onClick={() => setStep(3)}>
            Continuar
          </button>
        </Panel>
      )}

      {/* 4 — Template ------------------------------------------------------ */}
      {step === 3 && project && (
        <Panel
          title="Template"
          desc="Automático escolhe pelo tom do roteiro e compõe vários trechos se o áudio for mais longo que o vídeo."
        >
          {templates.data?.templates.length === 0 && (
            <Alert level="error" title="Nenhum template">
              Adicione vídeos verticais seus na página Templates.
            </Alert>
          )}

          <Field label="Modo">
            <select
              className="select"
              value={project.template.mode}
              onChange={(e) =>
                updateProject({ template: { mode: e.target.value } })
              }
            >
              <option value="auto">Automático</option>
              <option value="manual">Escolher manualmente</option>
            </select>
          </Field>

          {project.template.mode === "manual" && (
            <div className="grid grid-4">
              {templates.data?.templates.map((template) => (
                <div
                  key={template.id}
                  className={`template-card ${
                    project.template.template_id === template.id ? "selected" : ""
                  }`}
                  onClick={() =>
                    updateProject({ template: { template_id: template.id } })
                  }
                >
                  <video
                    className="template-preview"
                    src={template.preview_url}
                    muted
                    playsInline
                    preload="metadata"
                  />
                  <div className="template-body">
                    <div className="small truncate">{template.id}</div>
                    <div className="tiny dim">
                      {formatDuration(template.duration)} · {template.energy}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          <button className="btn btn-primary mt-1" onClick={() => setStep(4)}>
            Continuar
          </button>
        </Panel>
      )}

      {/* 5 — Edição -------------------------------------------------------- */}
      {step === 4 && project && (
        <Panel
          title="Edição"
          desc="Fecha o enquadramento no rosto quando a frase importa e abre quando ela acaba — o que um editor faz com uma câmera só."
        >
          <label className="checkbox" style={{ marginBottom: 14 }}>
            <input
              type="checkbox"
              checked={project.editing.auto_cut}
              onChange={(e) =>
                updateProject({ editing: { auto_cut: e.target.checked } })
              }
            />
            auto editor
          </label>

          <Field
            label="Preset"
            hint="O plano fica em edit.json, dentro da pasta do projeto — dá para conferir onde cada corte caiu."
          >
            <select
              className="select"
              value={project.editing.preset}
              disabled={!project.editing.auto_cut}
              onChange={(e) =>
                updateProject({ editing: { preset: e.target.value } })
              }
            >
              {presets.data?.editing.map((option) => (
                <option key={option.key} value={option.key}>
                  {option.label}
                </option>
              ))}
            </select>
          </Field>

          <Alert level="info" title="Não corta tempo, corta enquadramento">
            O vídeo está lip-sincronizado quadro a quadro com a voz. Remover
            trechos dessincronizaria a boca da fala, então o auto editor muda o
            enquadramento e preserva a duração. As legendas ficam fixas no
            quadro, sem acompanhar o zoom.
          </Alert>

          <button className="btn btn-primary" onClick={() => setStep(5)}>
            Continuar
          </button>
        </Panel>
      )}

      {/* 6 — Legendas ------------------------------------------------------ */}
      {step === 5 && project && (
        <Panel
          title="Legendas"
          desc="Queimadas no vídeo, palavra por palavra. Nunca dependa das legendas automáticas da plataforma."
        >
          <label className="checkbox" style={{ marginBottom: 14 }}>
            <input
              type="checkbox"
              checked={project.captions.enabled}
              onChange={(e) =>
                updateProject({ captions: { enabled: e.target.checked } })
              }
            />
            gerar legendas
          </label>

          <div className="grid grid-3">
            {presets.data?.captions.map((option) => (
              <div
                key={option.key}
                className="panel"
                style={{
                  cursor: "pointer",
                  marginBottom: 0,
                  background: "var(--surface-2)",
                  borderColor:
                    project.captions.preset === option.key
                      ? "var(--accent)"
                      : "var(--border)",
                }}
                onClick={() =>
                  updateProject({ captions: { preset: option.key } })
                }
              >
                <div className="bold small">{option.label}</div>
                <div className="tiny dim">
                  {option.words_per_cue} palavras · {option.uppercase ? "CAIXA ALTA" : "normal"}
                </div>
              </div>
            ))}
          </div>

          <button className="btn btn-primary mt-2" onClick={() => setStep(6)}>
            Continuar
          </button>
        </Panel>
      )}

      {/* 7/8 — Preview e Render -------------------------------------------- */}
      {step >= 6 && project && (
        <div className="grid grid-2">
          <Panel
            title={step === 6 ? "Preview" : "Render final"}
            desc={
              step === 6
                ? "540×960, primeiros 10 segundos — descubra o erro antes de esperar o render inteiro."
                : "1080×1920, H.264/AAC, pronto para Reels, TikTok e Shorts."
            }
          >
            <div className="row" style={{ marginBottom: 14 }}>
              <button
                className="btn"
                onClick={() => render(true)}
                disabled={busy || !!job && !["completed","failed","cancelled"].includes(job.state)}
              >
                Gerar preview
              </button>
              <button
                className="btn btn-primary"
                onClick={() => render(false)}
                disabled={busy || !!job && !["completed","failed","cancelled"].includes(job.state)}
              >
                Render final
              </button>
            </div>

            {job && (
              <div>
                <div className="row row-between" style={{ marginBottom: 6 }}>
                  <Pill level={job.state === "failed" ? "error" : "accent"}>
                    {STATE_LABEL[job.state] ?? job.state}
                  </Pill>
                  <span className="tiny dim">
                    {(job.progress * 100).toFixed(0)}%
                  </span>
                </div>
                <Progress value={job.progress} />
                <div className="tiny muted mt-1">{job.message}</div>

                {job.error && (
                  <Alert level="error" title={`Falha em "${job.error.stage}"`}>
                    <div>{job.error.message}</div>
                    {job.error.hint && (
                      <div style={{ color: "var(--warn)", marginTop: 4 }}>
                        → {job.error.hint}
                      </div>
                    )}
                    {job.log_file && (
                      <div className="tiny dim mt-1">log: {job.log_file}</div>
                    )}
                  </Alert>
                )}

                {!["completed", "failed", "cancelled"].includes(job.state) && (
                  <button
                    className="btn btn-sm btn-danger mt-1"
                    onClick={() => api.jobs.cancel(job.id)}
                  >
                    Cancelar
                  </button>
                )}
              </div>
            )}

            {Array.isArray((job?.result as { warnings?: string[] })?.warnings) &&
              (job!.result as { warnings: string[] }).warnings.map((warning) => (
                <Alert key={warning} level="warn">
                  {warning}
                </Alert>
              ))}
          </Panel>

          <Panel
            title="Resultado"
            action={
              <label className="checkbox tiny">
                <input
                  type="checkbox"
                  checked={showSafe}
                  onChange={(e) => setShowSafe(e.target.checked)}
                />
                zonas seguras
              </label>
            }
          >
            {renderUrl ? (
              <>
                <div className="video-frame">
                  <video src={renderUrl} controls playsInline />
                  <SafeZones show={showSafe} />
                </div>
                <a
                  className="btn btn-primary mt-2"
                  href={renderUrl}
                  download
                  style={{ display: "inline-flex" }}
                >
                  Baixar MP4
                </a>
                <div className="tiny dim mt-1">
                  O overlay de zonas seguras é só do editor — não entra no
                  arquivo final.
                </div>
              </>
            ) : (
              <div className="empty">
                <div className="empty-title">Nenhum render ainda</div>
                <div className="small">
                  Gere um preview para ver o resultado aqui.
                </div>
              </div>
            )}
          </Panel>
        </div>
      )}
    </div>
  );
}
