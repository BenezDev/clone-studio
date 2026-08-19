import { useRef, useState } from "react";

import {
  Alert,
  Empty,
  ErrorBox,
  Field,
  Panel,
  Pill,
  Spinner,
  formatDuration,
  useAsync,
} from "../components/ui";
import { api } from "../lib/api";

/**
 * Biblioteca de B-roll.
 *
 * A prioridade é a biblioteca local; geração por IA (ComfyUI + Wan2.2) é
 * opcional e não é requisito para o funcionamento.
 */
export default function Assets() {
  const settings = useAsync(() => api.settings.get(), []);
  const [query, setQuery] = useState("");
  const library = useAsync(() => api.assets.broll(query), [query]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const generativeEnabled =
    settings.data?.settings.broll?.generative?.enabled === true;

  async function upload() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      await api.assets.upload(form);
      if (fileRef.current) fileRef.current.value = "";
      library.reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setBusy(true);
    try {
      await api.assets.remove(id);
      library.reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Assets</h1>
          <p className="page-desc">
            B-roll, memes, screenshots e gráficos usados como apoio visual. O
            trecho entra <strong>por cima</strong> da imagem por alguns
            segundos — o áudio segue e o lip-sync não é tocado.
          </p>
        </div>
      </div>

      <ErrorBox error={error || library.error} />

      <Panel
        title="Biblioteca local"
        desc="O nome do arquivo É a busca: as palavras dele são o que casa com o campo de B-roll de cada cena do roteiro."
      >
        <div className="row row-wrap">
          <input
            ref={fileRef}
            type="file"
            className="input"
            accept={library.data?.accepted.join(",")}
            style={{ maxWidth: 400 }}
          />
          <button className="btn btn-primary" onClick={upload} disabled={busy}>
            {busy ? <Spinner /> : null} Adicionar
          </button>
        </div>
        <div className="hint mt-1">
          Ou copie os arquivos direto para{" "}
          <span className="mono">{library.data?.directory}</span>. Nomes
          descritivos ganham:{" "}
          <span className="mono">bitcoin_grafico_queda.mp4</span> é melhor que{" "}
          <span className="mono">IMG_0421.mp4</span>.
        </div>

        <Field
          label="Testar uma busca"
          hint="Digite o que você escreveria no campo de B-roll da cena e veja o que a biblioteca devolveria."
        >
          <input
            className="input"
            value={query}
            placeholder="gráfico do bitcoin caindo"
            onChange={(e) => setQuery(e.target.value)}
          />
        </Field>
      </Panel>

      {library.loading && <Spinner />}

      {library.data?.assets.length === 0 ? (
        <Empty title="Biblioteca vazia">
          Sem assets, as cenas com pedido de B-roll seguem só com o seu vídeo.
        </Empty>
      ) : (
        <div className="grid grid-3">
          {library.data?.assets.map((asset) => (
            <div
              key={asset.id}
              className={`template-card ${
                library.data?.matched === asset.id ? "selected" : ""
              }`}
            >
              {asset.kind === "video" ? (
                <video
                  className="template-preview"
                  src={asset.preview_url}
                  muted
                  playsInline
                  preload="metadata"
                  onMouseEnter={(e) => void e.currentTarget.play().catch(() => {})}
                  onMouseLeave={(e) => {
                    e.currentTarget.pause();
                    e.currentTarget.currentTime = 0;
                  }}
                />
              ) : (
                <img className="template-preview" src={asset.preview_url} alt="" />
              )}
              <div className="template-body">
                <div className="row row-between">
                  <span className="small bold truncate">{asset.id}</span>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => remove(asset.id)}
                    disabled={busy}
                  >
                    ✕
                  </button>
                </div>
                <div className="row row-wrap mt-1" style={{ gap: 5 }}>
                  {library.data?.matched === asset.id && (
                    <Pill level="accent">melhor resultado</Pill>
                  )}
                  <Pill>{asset.kind}</Pill>
                  {asset.duration > 0 && (
                    <Pill>{formatDuration(asset.duration)}</Pill>
                  )}
                  <Pill>
                    {asset.width}×{asset.height}
                  </Pill>
                </div>
                <div className="tiny dim mt-1 truncate">
                  {asset.keywords.join(" · ")}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <Panel
        title="B-roll generativo"
        desc="Módulo opcional. O programa funciona por completo sem ele."
        className="mt-2"
      >
        <div className="row row-wrap" style={{ marginBottom: 12 }}>
          <Pill level={generativeEnabled ? "ok" : "info"}>
            {generativeEnabled ? "habilitado" : "desabilitado"}
          </Pill>
          <Pill>ComfyUI como runtime</Pill>
          <Pill>Wan2.2 opt-in</Pill>
        </div>

        <Alert level="warn" title="Hardware insuficiente para geração de vídeo">
          Este computador não tem GPU dedicada. Difusão de vídeo (Wan2.2) exige
          VRAM significativa e ficaria inviável aqui — por isso o módulo está
          desligado e o modelo não aparece como instalável. Todo o resto do
          pipeline funciona normalmente, e a biblioteca acima cobre o mesmo
          papel de produção.
        </Alert>

        <div className="small muted">
          Quando houver GPU compatível, o ComfyUI roda como serviço separado e a
          aplicação fala com ele por API — sem acoplar código aos internals dele.
          Os workflows ficam versionados em{" "}
          <span className="mono">workflows/comfyui/</span>.
        </div>
      </Panel>
    </div>
  );
}
