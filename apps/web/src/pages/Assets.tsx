import { Alert, Panel, Pill, useAsync } from "../components/ui";
import { api } from "../lib/api";

/**
 * Biblioteca de B-roll.
 *
 * A prioridade do projeto é a biblioteca local de assets; geração por IA
 * (ComfyUI + Wan2.2) é opcional e não é requisito para o funcionamento.
 */
export default function Assets() {
  const settings = useAsync(() => api.settings.get(), []);
  const brollDir = settings.data
    ? `${settings.data.paths.root}/data/assets/broll`
    : "data/assets/broll";

  const generativeEnabled =
    settings.data?.settings.broll?.generative?.enabled === true;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Assets</h1>
          <p className="page-desc">
            B-roll, memes, screenshots, gráficos e logos usados como apoio
            visual nos cortes.
          </p>
        </div>
      </div>

      <Panel
        title="Biblioteca local"
        desc="A fonte principal de B-roll. Coloque os arquivos na pasta e eles ficam disponíveis para o editor automático."
      >
        <div className="code">{brollDir}</div>
        <div className="hint mt-1">
          Formatos aceitos: mp4, mov, webm, jpg, png, webp. Nomes descritivos
          ajudam a busca — <span className="mono">bitcoin_grafico_queda.mp4</span>{" "}
          é melhor que <span className="mono">IMG_0421.mp4</span>.
        </div>

        <Alert level="info" title="Indexação">
          A indexação semântica da biblioteca entra junto com o editor
          automático (FASE 9). Hoje os arquivos podem ser referenciados
          manualmente pelo campo de B-roll de cada cena, na página de roteiro.
        </Alert>
      </Panel>

      <Panel
        title="B-roll generativo"
        desc="Módulo opcional. O programa funciona por completo sem ele."
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
          pipeline funciona normalmente.
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
