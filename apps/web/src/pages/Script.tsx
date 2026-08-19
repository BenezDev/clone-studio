import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import {
  Alert,
  ErrorBox,
  Field,
  Panel,
  Pill,
  Spinner,
  formatDuration,
  useAsync,
} from "../components/ui";
import { Script, api } from "../lib/api";

/** Edição focada do roteiro, com as operações de LLM à mão. */
export default function ScriptPage() {
  const { projectId } = useParams();
  const navigate = useNavigate();

  const [script, setScript] = useState<Script | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const llm = useAsync(() => api.script.status(), []);

  useEffect(() => {
    if (!projectId) return;
    api.projects
      .getScript(projectId)
      .then((response) => setScript(response.script))
      .catch(setError);
  }, [projectId]);

  const words =
    script?.scenes.reduce(
      (total, scene) =>
        total + scene.text.trim().split(/\s+/).filter(Boolean).length,
      0,
    ) ?? 0;

  async function run(name: string, action: () => Promise<void>) {
    setBusy(name);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    if (!projectId || !script) return;
    await run("save", async () => {
      await api.projects.saveScript(projectId, script);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    });
  }

  if (!script) {
    return (
      <div className="page">
        <ErrorBox error={error} />
        <Alert level="info">
          Este projeto ainda não tem roteiro. Comece pelo fluxo de New Video.
        </Alert>
        <button className="btn" onClick={() => navigate(`/new/${projectId}`)}>
          Abrir New Video
        </button>
      </div>
    );
  }

  const fullText = script.scenes.map((s) => s.text).join(" ");
  const disabled = !llm.data?.available;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Roteiro</h1>
          <p className="page-desc">
            Você edita tudo antes de qualquer geração. Nada é publicado
            automaticamente em lugar nenhum.
          </p>
        </div>
        <div className="row">
          {saved && <Pill level="ok">salvo</Pill>}
          <span className="tiny dim">
            {words} palavras · ~{formatDuration(words / 2.6)}
          </span>
          <button className="btn btn-primary" onClick={save} disabled={busy !== null}>
            {busy === "save" ? <Spinner /> : null} Salvar
          </button>
        </div>
      </div>

      <ErrorBox error={error} />

      {disabled && (
        <Alert level="info" title="Ollama offline">
          As ferramentas de reescrita ficam indisponíveis, mas a edição manual
          funciona normalmente.
        </Alert>
      )}

      <Panel title="Título e hook">
        <Field label="Título">
          <input
            className="input"
            value={script.title}
            onChange={(e) => setScript({ ...script, title: e.target.value })}
          />
        </Field>

        <Field label="Hook" hint="os primeiros 2 segundos decidem se a pessoa fica">
          <input
            className="input"
            value={script.hook}
            onChange={(e) => setScript({ ...script, hook: e.target.value })}
          />
        </Field>

        <button
          className="btn btn-sm"
          disabled={disabled || busy !== null}
          onClick={() =>
            run("hook", async () => {
              const response = await api.script.hook(script.hook || fullText);
              setScript({ ...script, hook: response.result });
            })
          }
        >
          {busy === "hook" ? <Spinner /> : null} Melhorar hook
        </button>
      </Panel>

      <Panel title="Cenas">
        {script.scenes.map((scene, index) => (
          <div key={index} style={{ marginBottom: 16 }}>
            <div className="row row-between" style={{ marginBottom: 4 }}>
              <span className="label">
                Cena {index + 1} · início {scene.start}s
              </span>
              <div className="row">
                <button
                  className="btn btn-sm btn-ghost"
                  disabled={disabled || busy !== null}
                  onClick={() =>
                    run(`rewrite-${index}`, async () => {
                      const response = await api.script.rewrite(
                        scene.text,
                        "Deixe mais natural, falado e direto.",
                      );
                      const scenes = [...script.scenes];
                      scenes[index] = { ...scene, text: response.result };
                      setScript({ ...script, scenes });
                    })
                  }
                >
                  {busy === `rewrite-${index}` ? <Spinner /> : null} reescrever
                </button>
                <button
                  className="btn btn-sm btn-ghost"
                  onClick={() =>
                    setScript({
                      ...script,
                      scenes: script.scenes.filter((_, i) => i !== index),
                    })
                  }
                >
                  remover
                </button>
              </div>
            </div>

            <textarea
              className="textarea"
              style={{ minHeight: 78 }}
              value={scene.text}
              onChange={(e) => {
                const scenes = [...script.scenes];
                scenes[index] = { ...scene, text: e.target.value };
                setScript({ ...script, scenes });
              }}
            />

            <div className="row mt-1">
              <input
                className="input"
                style={{ maxWidth: 320 }}
                placeholder="palavras a enfatizar (separadas por vírgula)"
                value={scene.emphasis_words.join(", ")}
                onChange={(e) => {
                  const scenes = [...script.scenes];
                  scenes[index] = {
                    ...scene,
                    emphasis_words: e.target.value
                      .split(",")
                      .map((w) => w.trim())
                      .filter(Boolean),
                  };
                  setScript({ ...script, scenes });
                }}
              />
              <input
                className="input"
                style={{ maxWidth: 320 }}
                placeholder="prompt de B-roll (opcional)"
                value={scene.broll_prompt ?? ""}
                onChange={(e) => {
                  const scenes = [...script.scenes];
                  scenes[index] = {
                    ...scene,
                    broll_prompt: e.target.value || null,
                  };
                  setScript({ ...script, scenes });
                }}
              />
            </div>
          </div>
        ))}

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
      </Panel>

      <Panel title="Publicação" desc="Gerado localmente; a publicação é sempre manual.">
        <Field label="CTA">
          <input
            className="input"
            value={script.cta}
            onChange={(e) => setScript({ ...script, cta: e.target.value })}
          />
        </Field>

        <Field label="Legenda do post">
          <textarea
            className="textarea"
            style={{ minHeight: 70 }}
            value={script.caption}
            onChange={(e) => setScript({ ...script, caption: e.target.value })}
          />
        </Field>

        <Field label="Hashtags">
          <input
            className="input"
            value={script.hashtags.join(" ")}
            onChange={(e) =>
              setScript({
                ...script,
                hashtags: e.target.value.split(/\s+/).filter(Boolean),
              })
            }
          />
        </Field>

        <div className="row row-wrap">
          {[
            ["cta", "Gerar CTA", () => api.script.cta(fullText)],
            ["caption", "Gerar legenda", () => api.script.caption(fullText)],
            ["title", "Gerar título", () => api.script.title(fullText)],
          ].map(([key, label, call]) => (
            <button
              key={key as string}
              className="btn btn-sm"
              disabled={disabled || busy !== null}
              onClick={() =>
                run(key as string, async () => {
                  const response = await (call as () => Promise<{ result: string }>)();
                  setScript({ ...script, [key as string]: response.result });
                })
              }
            >
              {busy === key ? <Spinner /> : null} {label as string}
            </button>
          ))}
          <button
            className="btn btn-sm"
            disabled={disabled || busy !== null}
            onClick={() =>
              run("hashtags", async () => {
                const response = await api.script.hashtags(fullText);
                setScript({ ...script, hashtags: response.result });
              })
            }
          >
            {busy === "hashtags" ? <Spinner /> : null} Gerar hashtags
          </button>
        </div>
      </Panel>
    </div>
  );
}
