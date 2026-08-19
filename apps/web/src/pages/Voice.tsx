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
import { Recorder } from "../components/Recorder";
import { api } from "../lib/api";

const DEFAULT_TEST_TEXT =
  "A inteligência artificial não vai substituir programadores. " +
  "Ela vai substituir programadores que não usam inteligência artificial.";

export default function Voice() {
  const profiles = useAsync(() => api.voice.profiles(), []);
  const health = useAsync(() => api.voice.health(), []);

  const [profileId, setProfileId] = useState("me");
  const [displayName, setDisplayName] = useState("Minha voz");
  const [transcript, setTranscript] = useState("");
  const [label, setLabel] = useState("default");
  const [enrolling, setEnrolling] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [source, setSource] = useState<"upload" | "record">("record");
  const [recorded, setRecorded] = useState<File | null>(null);
  // Remonta o gravador depois de cadastrar: zera o estado dele e devolve o
  // microfone ao sistema em vez de deixá-lo aberto entre uma amostra e outra.
  const [recorderNonce, setRecorderNonce] = useState(0);

  const [text, setText] = useState(DEFAULT_TEST_TEXT);
  const [speed, setSpeed] = useState(1.0);
  const [emotion, setEmotion] = useState("");
  const [variants, setVariants] = useState(1);
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState<
    { label: string; duration: number; seed: number | null; url: string }[] | null
  >(null);
  const [chosen, setChosen] = useState<string | null>(null);

  const selected = profiles.data?.profiles.find((p) => p.id === profileId);
  const engineOk = (health.data as { ok?: boolean } | null)?.ok === true;

  async function enroll() {
    const file = source === "record" ? recorded : fileRef.current?.files?.[0];
    if (!file) {
      setError(
        new Error(
          source === "record"
            ? "Grave uma amostra antes de cadastrar."
            : "Escolha um arquivo de áudio.",
        ),
      );
      return;
    }
    if (!transcript.trim()) {
      setError(
        new Error(
          "A transcrição é obrigatória — a clonagem fica muito melhor quando o modelo sabe exatamente o que é falado na amostra.",
        ),
      );
      return;
    }

    setEnrolling(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("audio", file);
      form.append("transcript", transcript);
      form.append("display_name", displayName);
      form.append("label", label);
      await api.voice.enroll(profileId, form);
      setTranscript("");
      if (fileRef.current) fileRef.current.value = "";
      setRecorded(null);
      setRecorderNonce((value) => value + 1);
      profiles.reload();
    } catch (err) {
      setError(err);
    } finally {
      setEnrolling(false);
    }
  }

  async function generate() {
    setGenerating(true);
    setError(null);
    setResult(null);
    setChosen(null);
    try {
      const response = await api.voice.synthesize({
        text,
        profile_id: profileId,
        speed,
        emotion,
        variants,
      });
      setResult(response.variants);
    } catch (err) {
      setError(err);
    } finally {
      setGenerating(false);
    }
  }

  async function keepPreference(variantLabel: string) {
    setChosen(variantLabel);
    const variant = result?.find((v) => v.label === variantLabel);
    await api.voice.setPreferred(profileId, {
      speed,
      emotion,
      seed: variant?.seed ?? null,
    });
    profiles.reload();
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Voice</h1>
          <p className="page-desc">
            Clonagem local da sua voz com Qwen3-TTS. As amostras ficam em{" "}
            <span className="mono">data/identity/voice/</span> e nunca saem da
            máquina.
          </p>
        </div>
      </div>

      <ErrorBox error={error} />
      <ErrorBox error={profiles.error} />

      {!engineOk && health.data && (
        <Alert level="warn" title="Engine de voz indisponível">
          {(health.data as { detail?: string }).detail}
          {((health.data as { hints?: string[] }).hints ?? []).map((hint) => (
            <div key={hint}>→ {hint}</div>
          ))}
        </Alert>
      )}

      <div className="grid grid-2">
        <Panel
          title="Cadastrar amostra"
          desc="Grave direto pelo microfone ou envie um arquivo. Voz isolada, sem música e sem reverberação; de 5 a 30 segundos funciona bem."
        >
          <Field label="Identificador do perfil" hint="minúsculas, sem espaços">
            <input
              className="input"
              value={profileId}
              onChange={(e) => setProfileId(e.target.value.replace(/\s/g, "_"))}
            />
          </Field>

          <Field label="Nome de exibição">
            <input
              className="input"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </Field>

          <Field
            label="Estilo desta amostra"
            hint="O modelo Base não aceita instrução de emoção. A variação de estilo vem de cadastrar amostras diferentes — grave uma calma, uma empolgada, uma séria."
          >
            <input
              className="input"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="default, confiante, calmo, empolgado…"
            />
          </Field>

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
            <Field label="Arquivo de áudio">
              <input
                ref={fileRef}
                type="file"
                className="input"
                accept="audio/*,.wav,.mp3,.m4a,.flac"
              />
            </Field>
          ) : (
            <Field
              label="Gravar do microfone"
              hint="Ambiente quieto, sem música e sem eco. Leia o texto que você vai escrever na transcrição abaixo, no ritmo em que você fala de verdade."
            >
              <Recorder
                key={recorderNonce}
                kind="audio"
                maxSeconds={120}
                idealRange={[5, 30]}
                onRecorded={setRecorded}
              />
            </Field>
          )}

          <Field
            label="Transcrição exata"
            hint="Escreva palavra por palavra o que é dito no áudio."
          >
            <textarea
              className="textarea"
              value={transcript}
              onChange={(e) => setTranscript(e.target.value)}
              placeholder="O que você fala na amostra, exatamente como fala."
            />
          </Field>

          <button className="btn btn-primary" onClick={enroll} disabled={enrolling}>
            {enrolling ? <Spinner /> : null} Cadastrar voz
          </button>
        </Panel>

        <Panel title="Perfis cadastrados">
          {profiles.loading && <Spinner />}
          {profiles.data?.profiles.length === 0 && (
            <Empty title="Nenhuma voz cadastrada">
              Cadastre ao menos uma amostra para gerar áudio.
            </Empty>
          )}
          {profiles.data?.profiles.map((profile) => (
            <div
              key={profile.id}
              className="panel"
              style={{
                background: "var(--surface-2)",
                marginBottom: 10,
                borderColor:
                  profile.id === profileId ? "var(--accent)" : "var(--border)",
                cursor: "pointer",
              }}
              onClick={() => setProfileId(profile.id)}
            >
              <div className="row row-between">
                <div>
                  <div className="bold">{profile.display_name}</div>
                  <div className="tiny dim mono">{profile.id}</div>
                </div>
                <Pill level="ok">
                  {formatDuration(profile.total_duration ?? 0)}
                </Pill>
              </div>

              <div className="mt-1">
                {profile.references.map((reference, index) => (
                  <div key={index} className="list-item" style={{ padding: "7px 0" }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="row" style={{ gap: 6 }}>
                        <span className="small bold">{reference.label}</span>
                        {reference.primary && <Pill level="accent">principal</Pill>}
                        <span className="tiny dim">
                          {reference.duration.toFixed(1)}s
                        </span>
                      </div>
                      <div className="tiny dim truncate">
                        {reference.transcript}
                      </div>
                    </div>
                    <audio
                      controls
                      preload="none"
                      style={{ height: 28, maxWidth: 170 }}
                      src={api.mediaUrl(reference.audio_path)}
                    />
                    <button
                      className="btn btn-ghost btn-sm"
                      onClick={async (e) => {
                        e.stopPropagation();
                        await api.voice.deleteReference(profile.id, index);
                        profiles.reload();
                      }}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </Panel>
      </div>

      <Panel
        title="Testar voz (A/B)"
        desc="Gere até 5 versões do mesmo texto e escolha a que soa mais como você. A escolha vira a configuração preferida do perfil."
      >
        <Field label="Texto">
          <textarea
            className="textarea"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </Field>

        <div className="grid grid-4" style={{ marginBottom: 14 }}>
          <Field label={`Velocidade — ${speed.toFixed(2)}x`}>
            <input
              type="range"
              className="slider"
              min={0.7}
              max={1.4}
              step={0.05}
              value={speed}
              onChange={(e) => setSpeed(Number(e.target.value))}
            />
          </Field>

          <Field label="Estilo" hint="usa a amostra com este rótulo">
            <select
              className="select"
              value={emotion}
              onChange={(e) => setEmotion(e.target.value)}
            >
              <option value="">principal</option>
              {[
                ...new Set(selected?.references.map((r) => r.label) ?? []),
              ].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Variantes">
            <select
              className="select"
              value={variants}
              onChange={(e) => setVariants(Number(e.target.value))}
            >
              {[1, 2, 3, 4, 5].map((n) => (
                <option key={n} value={n}>
                  {n === 1 ? "1 (rápido)" : `${n} versões`}
                </option>
              ))}
            </select>
          </Field>

          <Field label="&nbsp;">
            <button
              className="btn btn-primary"
              onClick={generate}
              disabled={generating || !selected}
            >
              {generating ? <Spinner /> : null}{" "}
              {variants > 1 ? `Gerar ${variants}` : "Gerar"}
            </button>
          </Field>
        </div>

        {generating && (
          <Alert level="info">
            Gerando em CPU — algo em torno de 8 segundos de processamento por
            segundo de áudio neste hardware.
          </Alert>
        )}

        {result?.map((variant) => (
          <div key={variant.label} className="list-item">
            <span
              className="pill"
              style={
                chosen === variant.label
                  ? { borderColor: "var(--accent)", color: "var(--accent)" }
                  : undefined
              }
            >
              {variant.label}
            </span>
            <audio controls src={variant.url} style={{ flex: 1, height: 34 }} />
            <span className="tiny dim">{variant.duration.toFixed(2)}s</span>
            {result.length > 1 && (
              <button
                className="btn btn-sm"
                onClick={() => keepPreference(variant.label)}
              >
                {chosen === variant.label ? "preferida ✓" : "prefiro esta"}
              </button>
            )}
          </div>
        ))}
      </Panel>
    </div>
  );
}
