import { Link } from "react-router-dom";

import {
  Alert,
  ErrorBox,
  Panel,
  Pill,
  Stat,
  formatDuration,
  useAsync,
} from "../components/ui";
import { api } from "../lib/api";

export default function Identity() {
  const voices = useAsync(() => api.voice.profiles(), []);
  const templates = useAsync(() => api.templates.list(true), []);
  const settings = useAsync(() => api.settings.get(), []);

  const totalVoice =
    voices.data?.profiles.reduce(
      (sum, profile) => sum + (profile.total_duration ?? 0),
      0,
    ) ?? 0;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Identity</h1>
          <p className="page-desc">
            Sua voz, seu rosto e seus vídeos. Estes arquivos ficam apenas nesta
            máquina, fora do Git e fora de qualquer serviço externo.
          </p>
        </div>
      </div>

      <ErrorBox error={voices.error || templates.error} />

      <Alert level="ok" title="Uso previsto">
        Este software existe para produzir conteúdo audiovisual com a sua
        própria aparência e a sua própria voz. Ele não deve ser usado para
        autenticação nem para contornar verificação de identidade,
        reconhecimento facial ou sistemas antifraude.
      </Alert>

      <div className="grid grid-4" style={{ marginBottom: 18 }}>
        <Stat
          label="Vozes"
          value={voices.data?.profiles.length ?? "—"}
          sub={`${formatDuration(totalVoice)} de amostras`}
        />
        <Stat
          label="Templates"
          value={templates.data?.templates.length ?? "—"}
          sub={`${formatDuration(templates.data?.total_duration ?? 0)} de material`}
        />
        <Stat
          label="Fotos"
          value="—"
          sub="usadas só no modo avatar generativo"
        />
        <Stat label="Local" value="100%" sub="nada sai daqui" />
      </div>

      <div className="grid grid-2">
        <Panel
          title="Vozes"
          action={
            <Link to="/voice" className="btn btn-ghost btn-sm">
              gerenciar
            </Link>
          }
        >
          {voices.data?.profiles.length === 0 && (
            <div className="tiny dim">Nenhum perfil cadastrado.</div>
          )}
          {voices.data?.profiles.map((profile) => (
            <div key={profile.id} className="list-item">
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="small bold">{profile.display_name}</div>
                <div className="tiny dim mono">{profile.id}</div>
              </div>
              <Pill>{profile.references.length} amostra(s)</Pill>
              <span className="tiny dim">
                {formatDuration(profile.total_duration ?? 0)}
              </span>
            </div>
          ))}
        </Panel>

        <Panel
          title="Templates de vídeo"
          action={
            <Link to="/templates" className="btn btn-ghost btn-sm">
              gerenciar
            </Link>
          }
        >
          {templates.data?.templates.length === 0 && (
            <div className="tiny dim">Nenhum template cadastrado.</div>
          )}
          {templates.data?.templates.slice(0, 12).map((template) => (
            <div key={template.id} className="list-item">
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="small truncate">{template.id}</div>
                <div className="tiny dim">
                  {template.style} · {template.energy} · {template.width}×
                  {template.height}
                </div>
              </div>
              <span className="tiny dim">
                {formatDuration(template.duration)}
              </span>
              {!template.enabled && <Pill level="warn">off</Pill>}
            </div>
          ))}
        </Panel>
      </div>

      <Panel title="Onde ficam os arquivos">
        <div className="code">
          {`${settings.data?.paths.identity ?? "data/identity"}/
  voice/       amostras e voice_profile.json
  templates/   seus vídeos verticais + metadados
  photos/      fotos (modo avatar generativo, futuro)
  videos/      material bruto
  metadata/    índices auxiliares`}
        </div>
        <div className="hint mt-1">
          Todo o diretório <span className="mono">data/</span> está no
          .gitignore. Faça backup por conta própria: apagá-lo significa perder o
          cadastro da sua voz.
        </div>
      </Panel>
    </div>
  );
}
