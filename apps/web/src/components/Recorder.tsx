/**
 * Gravação de voz e de vídeo dentro do próprio app.
 *
 * Existe para tirar do caminho o passo "grave no celular, passe o arquivo para
 * o computador, ache a pasta". Quem só quer cadastrar a própria voz e o próprio
 * rosto não deveria precisar de um gerenciador de arquivos.
 *
 * O envio de arquivo continua ao lado, intacto: quem já tem material bom
 * gravado não pode ser obrigado a regravar.
 *
 * Privacidade: o MediaRecorder escreve num Blob em memória, que vira um File no
 * mesmo POST multipart que o upload já usava. Nada é gravado em disco pelo
 * navegador e nada sai da máquina. O dispositivo só é aberto quando o usuário
 * clica, e é fechado quando ele sai da tela.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Alert, Field, SafeZones, Spinner } from "./ui";

export type RecorderKind = "audio" | "video";

type Phase = "idle" | "opening" | "armed" | "countdown" | "recording" | "review";

/**
 * Ordem de preferência dos formatos.
 *
 * Opus é o melhor codec de voz disponível em navegador e é o que Chrome e
 * Firefox entregam. Safari só faz MP4/AAC. O backend aceita os três e converte
 * para WAV no cadastro — não há como pedir WAV ao MediaRecorder.
 */
const MIME_CANDIDATES: Record<RecorderKind, string[]> = {
  audio: [
    "audio/webm;codecs=opus",
    "audio/ogg;codecs=opus",
    "audio/webm",
    "audio/mp4",
  ],
  video: [
    "video/webm;codecs=vp9",
    "video/webm;codecs=vp8",
    "video/webm",
    "video/mp4",
  ],
};

function pickMimeType(kind: RecorderKind): string {
  if (typeof MediaRecorder === "undefined") return "";
  for (const candidate of MIME_CANDIDATES[kind]) {
    if (MediaRecorder.isTypeSupported(candidate)) return candidate;
  }
  return "";
}

function extensionFor(mimeType: string, kind: RecorderKind): string {
  if (mimeType.includes("ogg")) return ".ogg";
  if (mimeType.includes("mp4")) return kind === "audio" ? ".m4a" : ".mp4";
  return ".webm";
}

function timestampName(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `gravacao-${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}` +
    `-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
  );
}

function clock(seconds: number): string {
  const total = Math.floor(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** Erro de dispositivo nunca é só "falhou" — sempre com o que fazer a seguir. */
function describeError(
  cause: unknown,
  kind: RecorderKind,
): { message: string; hint: string } {
  const nome = kind === "audio" ? "o microfone" : "a câmera";
  const name = cause instanceof DOMException ? cause.name : "";

  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      return {
        message: `Permissão negada para usar ${nome}.`,
        hint:
          "Clique no ícone de cadeado ao lado do endereço, libere o acesso " +
          "para este site e clique de novo.",
      };
    case "NotFoundError":
    case "OverconstrainedError":
      return {
        message: `Nenhum dispositivo encontrado para ${nome}.`,
        hint: "Conecte o dispositivo, verifique se não está desativado e tente de novo.",
      };
    case "NotReadableError":
    case "AbortError":
      return {
        message: `Outro programa está usando ${nome}.`,
        hint:
          "Feche chamadas de vídeo, OBS, ou outra aba do navegador que esteja " +
          "com o dispositivo aberto.",
      };
    default:
      return {
        message: cause instanceof Error ? cause.message : String(cause),
        hint: "",
      };
  }
}

export function Recorder({
  kind,
  maxSeconds,
  idealRange,
  onRecorded,
}: {
  kind: RecorderKind;
  maxSeconds: number;
  /** Faixa de duração que produz bom resultado, em segundos. */
  idealRange?: [number, number];
  /** `null` quando a gravação é descartada. */
  onRecorded: (file: File | null) => void;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<{ message: string; hint: string } | null>(null);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [countdown, setCountdown] = useState(0);
  const [level, setLevel] = useState(0);
  const [clipped, setClipped] = useState(false);
  const [preview, setPreview] = useState<string | null>(null);
  const [showSafe, setShowSafe] = useState(true);

  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const frameRef = useRef(0);
  const previewRef = useRef<string | null>(null);
  const liveVideoRef = useRef<HTMLVideoElement>(null);
  const onRecordedRef = useRef(onRecorded);

  useEffect(() => {
    onRecordedRef.current = onRecorded;
  });

  /** Avisa o pai sem depender da identidade da função que ele passou. */
  const emit = useCallback((file: File | null) => onRecordedRef.current(file), []);

  const supported =
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== "undefined";

  // --- ciclo de vida dos recursos -----------------------------------------

  const releasePreview = useCallback(() => {
    if (previewRef.current) {
      URL.revokeObjectURL(previewRef.current);
      previewRef.current = null;
    }
    setPreview(null);
  }, []);

  const closeStream = useCallback(() => {
    cancelAnimationFrame(frameRef.current);
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
    recorderRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    void audioCtxRef.current?.close().catch(() => {});
    audioCtxRef.current = null;
    setLevel(0);
    setClipped(false);
  }, []);

  // Sair da página tem que apagar a luz da câmera. Sem isto o dispositivo fica
  // aberto em segundo plano, que é justamente o que um app local-first não pode
  // fazer.
  useEffect(() => {
    return () => {
      closeStream();
      if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    };
  }, [closeStream]);

  /** Medidor de nível: uma amostra fraca demais só se descobre ouvindo depois. */
  const watchLevel = useCallback((stream: MediaStream) => {
    const AudioCtor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    if (!AudioCtor) return;

    const context = new AudioCtor();
    audioCtxRef.current = context;
    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    context.createMediaStreamSource(stream).connect(analyser);
    const buffer = new Float32Array(analyser.fftSize);

    const tick = () => {
      analyser.getFloatTimeDomainData(buffer);
      let sum = 0;
      let peak = 0;
      for (const sample of buffer) {
        sum += sample * sample;
        peak = Math.max(peak, Math.abs(sample));
      }
      // RMS em escala logarítmica: em linear a barra quase não sai do lugar.
      const rms = Math.sqrt(sum / buffer.length);
      const db = 20 * Math.log10(rms || 1e-8);
      setLevel(Math.max(0, Math.min(1, (db + 60) / 60)));
      if (peak > 0.98) setClipped(true);
      frameRef.current = requestAnimationFrame(tick);
    };
    frameRef.current = requestAnimationFrame(tick);
  }, []);

  const openStream = useCallback(
    async (preferredDevice?: string) => {
      setError(null);
      setPhase("opening");
      closeStream();
      releasePreview();
      emit(null);

      try {
        const constraints: MediaStreamConstraints =
          kind === "audio"
            ? {
                // Os três processamentos abaixo existem para chamada de vídeo:
                // cortam ruído, mas também bombeiam e comem o final das
                // palavras. Numa amostra de referência isso vira sotaque
                // artificial no clone. Cru é melhor — daí virem desligados.
                audio: {
                  ...(preferredDevice ? { deviceId: { exact: preferredDevice } } : {}),
                  echoCancellation: false,
                  noiseSuppression: false,
                  autoGainControl: false,
                  channelCount: 1,
                },
                video: false,
              }
            : {
                // O áudio do template é descartado pelo pipeline: a fala do
                // vídeo final vem do clone de voz. Não gravar o microfone aqui
                // economiza bytes e evita captar som sem motivo.
                audio: false,
                video: {
                  ...(preferredDevice ? { deviceId: { exact: preferredDevice } } : {}),
                  width: { ideal: 1080 },
                  height: { ideal: 1920 },
                  frameRate: { ideal: 30 },
                  facingMode: "user",
                },
              };

        const stream = await navigator.mediaDevices.getUserMedia(constraints);
        streamRef.current = stream;

        // Os rótulos dos dispositivos só aparecem depois da permissão.
        const all = await navigator.mediaDevices.enumerateDevices();
        const wanted = kind === "audio" ? "audioinput" : "videoinput";
        setDevices(all.filter((device) => device.kind === wanted));
        setDeviceId(
          stream.getTracks()[0]?.getSettings().deviceId ?? preferredDevice ?? "",
        );

        if (kind === "audio") watchLevel(stream);
        setPhase("armed");
      } catch (cause) {
        closeStream();
        setError(describeError(cause, kind));
        setPhase("idle");
      }
    },
    [closeStream, emit, kind, releasePreview, watchLevel],
  );

  // --- gravação ------------------------------------------------------------

  const begin = useCallback(() => {
    const stream = streamRef.current;
    if (!stream) return;

    const mimeType = pickMimeType(kind);
    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    } catch (cause) {
      setError(describeError(cause, kind));
      setPhase("armed");
      return;
    }

    chunksRef.current = [];
    recorder.ondataavailable = (event: BlobEvent) => {
      if (event.data.size > 0) chunksRef.current.push(event.data);
    };
    recorder.onstop = () => {
      const type = recorder.mimeType || mimeType || "application/octet-stream";
      const blob = new Blob(chunksRef.current, { type });
      chunksRef.current = [];
      if (blob.size === 0) {
        setError({
          message: "A gravação saiu vazia.",
          hint: "Verifique se o dispositivo não foi desconectado e grave de novo.",
        });
        setPhase("armed");
        return;
      }
      const file = new File(
        [blob],
        `${timestampName()}${extensionFor(type, kind)}`,
        { type },
      );
      const url = URL.createObjectURL(blob);
      previewRef.current = url;
      setPreview(url);
      setPhase("review");
      emit(file);
    };

    recorderRef.current = recorder;
    setElapsed(0);
    setClipped(false);
    // Fatias de 1s: numa gravação longa, os dados já chegam particionados em
    // vez de um bloco único no final.
    recorder.start(1000);
    setPhase("recording");
  }, [emit, kind]);

  const stop = useCallback(() => {
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
  }, []);

  const discard = useCallback(() => {
    releasePreview();
    emit(null);
    setElapsed(0);
    setPhase(streamRef.current ? "armed" : "idle");
  }, [emit, releasePreview]);

  const shutdown = useCallback(() => {
    closeStream();
    releasePreview();
    emit(null);
    setDevices([]);
    setElapsed(0);
    setPhase("idle");
  }, [closeStream, emit, releasePreview]);

  // Contagem regressiva: sem ela a gravação começa com a pessoa procurando o
  // botão, e o primeiro segundo sempre sai perdido.
  useEffect(() => {
    if (phase !== "countdown") return;
    if (countdown <= 0) {
      begin();
      return;
    }
    const id = window.setTimeout(() => setCountdown((value) => value - 1), 1000);
    return () => window.clearTimeout(id);
  }, [begin, countdown, phase]);

  useEffect(() => {
    if (phase !== "recording") return;
    const started = Date.now();
    const id = window.setInterval(() => {
      const seconds = (Date.now() - started) / 1000;
      setElapsed(seconds);
      if (seconds >= maxSeconds) stop();
    }, 100);
    return () => window.clearInterval(id);
  }, [maxSeconds, phase, stop]);

  useEffect(() => {
    const element = liveVideoRef.current;
    if (element && streamRef.current) element.srcObject = streamRef.current;
  }, [phase]);

  // --- render --------------------------------------------------------------

  if (!supported) {
    return (
      <Alert level="warn" title="Gravação indisponível neste navegador">
        Use o envio de arquivo ao lado. A gravação precisa de um navegador com
        suporte a <span className="mono">MediaRecorder</span> e de um endereço
        seguro — abra o estúdio por{" "}
        <span className="mono">http://127.0.0.1:3000</span>, não pelo IP da rede.
      </Alert>
    );
  }

  const [idealMin, idealMax] = idealRange ?? [0, maxSeconds];
  const dentroDaFaixa = elapsed >= idealMin && elapsed <= idealMax;
  const live = phase === "armed" || phase === "countdown" || phase === "recording";

  return (
    <div className="recorder">
      {error && (
        <Alert level="error" title={error.message}>
          {error.hint && <div>→ {error.hint}</div>}
        </Alert>
      )}

      {phase === "idle" && (
        <div className="recorder-start">
          <button className="btn btn-primary" onClick={() => void openStream()}>
            {kind === "audio" ? "Ligar microfone" : "Ligar câmera"}
          </button>
          <div className="hint mt-1">
            {kind === "audio"
              ? "O microfone só é aberto agora, com este clique, e fecha quando você sair desta tela."
              : "A câmera só é aberta agora, com este clique, e fecha quando você sair desta tela. O áudio não é gravado — a fala do vídeo final vem do seu clone de voz."}
          </div>
        </div>
      )}

      {phase === "opening" && (
        <div className="row">
          <Spinner /> <span className="small">Aguardando permissão do navegador…</span>
        </div>
      )}

      {phase !== "idle" && phase !== "opening" && (
        <>
          {kind === "video" ? (
            <div className="video-frame recorder-frame">
              {live ? (
                <video
                  ref={liveVideoRef}
                  className="recorder-mirror"
                  muted
                  playsInline
                  autoPlay
                />
              ) : (
                preview && <video src={preview} controls playsInline />
              )}
              <SafeZones show={showSafe && live} />
              {phase === "countdown" && (
                <div className="recorder-countdown">{countdown}</div>
              )}
              {phase === "recording" && (
                <div className="recorder-rec">● {clock(elapsed)}</div>
              )}
            </div>
          ) : (
            <div className="recorder-meter-wrap">
              <div className="recorder-meter">
                <div
                  className="recorder-meter-fill"
                  style={{
                    width: `${level * 100}%`,
                    background: clipped
                      ? "var(--error)"
                      : level > 0.25
                        ? "var(--ok)"
                        : "var(--warn)",
                  }}
                />
              </div>
              <div className="row row-between mt-1">
                <span className="tiny dim">
                  {clipped
                    ? "Está estourando — afaste-se do microfone ou baixe o ganho."
                    : level > 0.25
                      ? "Nível bom."
                      : "Fale mais perto do microfone: o nível está baixo."}
                </span>
                {phase === "recording" && (
                  <span
                    className="tiny mono"
                    style={{ color: dentroDaFaixa ? "var(--ok)" : "var(--warn)" }}
                  >
                    ● {clock(elapsed)}
                  </span>
                )}
              </div>
              {phase === "countdown" && (
                <div className="recorder-countdown recorder-countdown-inline">
                  {countdown}
                </div>
              )}
              {phase === "review" && preview && (
                <audio className="mt-2" controls src={preview} style={{ width: "100%" }} />
              )}
            </div>
          )}

          <div className="row row-wrap mt-2">
            {phase === "armed" && (
              <button
                className="btn btn-primary"
                onClick={() => {
                  setCountdown(3);
                  setPhase("countdown");
                }}
              >
                Gravar
              </button>
            )}

            {(phase === "recording" || phase === "countdown") && (
              <button className="btn btn-primary" onClick={stop}>
                Parar
              </button>
            )}

            {phase === "review" && (
              <>
                <button className="btn" onClick={discard}>
                  Regravar
                </button>
                <span className="pill pill-ok">
                  gravação pronta · {clock(elapsed)}
                </span>
              </>
            )}

            <button className="btn btn-ghost" onClick={shutdown}>
              {kind === "audio" ? "Desligar microfone" : "Desligar câmera"}
            </button>
          </div>

          {devices.length > 1 && live && phase === "armed" && (
            <Field label={kind === "audio" ? "Microfone" : "Câmera"}>
              <select
                className="select"
                value={deviceId}
                onChange={(event) => {
                  setDeviceId(event.target.value);
                  void openStream(event.target.value);
                }}
              >
                {devices.map((device, index) => (
                  <option key={device.deviceId} value={device.deviceId}>
                    {device.label || `Dispositivo ${index + 1}`}
                  </option>
                ))}
              </select>
            </Field>
          )}

          {kind === "video" && live && (
            <label className="checkbox mt-1">
              <input
                type="checkbox"
                checked={showSafe}
                onChange={(event) => setShowSafe(event.target.checked)}
              />
              zonas seguras (guia de enquadramento, não entra no vídeo)
            </label>
          )}

          {phase === "armed" && idealRange && (
            <div className="hint mt-1">
              Alvo: de {idealMin} a {idealMax} segundos. A gravação para sozinha
              em {clock(maxSeconds)}.
            </div>
          )}
        </>
      )}
    </div>
  );
}
