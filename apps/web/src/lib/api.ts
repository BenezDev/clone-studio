/**
 * Cliente da API local.
 *
 * Todas as chamadas vão para o mesmo host (proxy do Vite em dev, mesma origem
 * em produção). Nenhuma requisição sai da máquina.
 */

const BASE = "/api";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  hint?: string;

  constructor(status: number, detail: unknown) {
    const message =
      typeof detail === "string"
        ? detail
        : (detail as { message?: string })?.message ?? `Erro ${status}`;
    super(message);
    this.status = status;
    this.detail = detail;
    this.hint = (detail as { hint?: string })?.hint;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        ...(init?.body instanceof FormData
          ? {}
          : { "Content-Type": "application/json" }),
        ...init?.headers,
      },
    });
  } catch (cause) {
    throw new ApiError(0, {
      message: "Não foi possível falar com o backend.",
      hint: "Verifique se start.sh (Linux) ou start.ps1 (Windows) está rodando.",
      cause: String(cause),
    });
  }

  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? body;
    } catch {
      /* resposta sem JSON */
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const get = <T>(path: string) => request<T>(path);
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });
const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
const put = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(body) });
const del = <T>(path: string) => request<T>(path, { method: "DELETE" });
const upload = <T>(path: string, form: FormData) =>
  request<T>(path, { method: "POST", body: form });

// --- tipos -----------------------------------------------------------------

export type Level = "ok" | "warn" | "error" | "info";

export interface Check {
  name: string;
  level: Level;
  summary: string;
  detail: string;
  hint: string;
  group: string;
  data: Record<string, unknown>;
}

export interface DiagnosticsReport {
  healthy: boolean;
  error_count: number;
  warning_count: number;
  checks: Check[];
}

export interface VoiceReference {
  audio_path: string;
  transcript: string;
  duration: number;
  sample_rate: number;
  label: string;
  primary: boolean;
}

export interface VoiceProfile {
  id: string;
  display_name: string;
  language: string;
  created_at: string;
  updated_at: string;
  references: VoiceReference[];
  default_speed: number;
  default_emotion: string;
  notes: string;
  preferred_settings: Record<string, unknown>;
  total_duration?: number;
}

export interface Template {
  id: string;
  duration: number;
  orientation: string;
  style: string;
  energy: string;
  gestures: string;
  camera: string;
  background: string;
  loopable: boolean;
  width: number;
  height: number;
  fps: number;
  has_audio: boolean;
  video_path: string;
  enabled: boolean;
  tags: string[];
  notes: string;
  preview_url: string;
}

export interface ModelInfo {
  key: string;
  display_name: string;
  purpose: string;
  repo: string;
  source: string;
  upstream_code: string;
  revision: string;
  code_revision: string;
  license: string;
  license_url: string;
  verified_at: string;
  commercial_status: string;
  commercial_ok: boolean;
  commercial_blockers: string[];
  dependencies: { name: string; license: string; commercial: string; note: string }[];
  size_gb: number;
  size_on_disk_bytes: number;
  install_dir: string;
  env: string;
  optional: boolean;
  notes: string;
  installed: boolean;
  hardware_compatible: boolean;
  hardware_reasons: string[];
  hardware_degraded: boolean;
}

export type JobState =
  | "queued" | "preparing" | "tts" | "lipsync" | "captions"
  | "editing" | "rendering" | "completed" | "failed" | "cancelled";

export interface Job {
  id: string;
  project_id: string;
  kind: string;
  state: JobState;
  progress: number;
  message: string;
  created_at: string;
  started_at: string;
  finished_at: string;
  error: { stage?: string; message?: string; hint?: string; log_file?: string } | null;
  result: Record<string, unknown>;
  log_file: string;
}

export interface Project {
  id: string;
  title: string;
  idea: string;
  created_at: string;
  updated_at: string;
  voice: { profile_id: string; emotion: string; speed: number; seed: number | null };
  template: { mode: string; template_id: string | null; plan: Record<string, unknown> };
  captions: { preset: string; uppercase: boolean | null; enabled: boolean };
  editing: { preset: string; auto_cut: boolean };
  render: { width: number; height: number; fps: number };
  engines: Record<string, string>;
  stages: Record<string, { status: string; output: string; duration_seconds: number }>;
  outputs: Record<string, string>;
  has_script: boolean;
  has_voice: boolean;
  renders: string[];
}

export interface LibraryScript {
  id: string;
  niche: string;
  title: string;
  hook: string;
  scenes: string[];
  cta: string;
  caption: string;
  hashtags: string[];
  preset: string;
  emotion: string;
  speed: number;
  tags: string[];
  is_template: boolean;
  slots: string[];
  notes: string;
  disclaimer: string;
  word_count: number;
}

export interface LibraryNiche {
  key: string;
  label: string;
  count: number;
}

export interface Scene {
  start: number;
  text: string;
  visual: string;
  broll_prompt: string | null;
  emphasis_words: string[];
}

export interface Script {
  title: string;
  hook: string;
  estimated_duration: number;
  voice_style: { emotion: string; speed: number };
  scenes: Scene[];
  cta: string;
  caption: string;
  hashtags: string[];
  preset: string;
  model: string;
}

// --- API -------------------------------------------------------------------

export const api = {
  health: () => get<{ status: string; hardware_profile: string }>("/health"),

  diagnostics: {
    run: (services = true) =>
      get<DiagnosticsReport>(`/diagnostics?services=${services}`),
    hardware: () => get<Record<string, unknown>>("/diagnostics/hardware"),
    refreshHardware: () => post<Record<string, unknown>>("/diagnostics/hardware/refresh"),
    listTests: () => get<{ tests: string[] }>("/diagnostics/tests"),
    runTest: (name: string) => post<Check>(`/diagnostics/tests/${name}`),
  },

  settings: {
    get: () =>
      get<{
        settings: Record<string, any>;
        active_profile: string;
        paths: Record<string, string>;
        config_file: string;
      }>("/settings"),
    patch: (overrides: Record<string, unknown>) =>
      patch<{ settings: Record<string, any> }>("/settings", overrides),
    presets: () =>
      get<{
        captions: { key: string; label: string; uppercase: boolean; words_per_cue: number }[];
        editing: string[];
        script: { key: string; label: string }[];
      }>("/settings/presets"),
    cache: () =>
      get<{ namespaces: { name: string; size_bytes: number; path: string }[]; total_bytes: number }>(
        "/settings/cache",
      ),
    clearCache: (name: string) =>
      del<{ entries_removed: number }>(`/settings/cache/${name}`),
  },

  models: {
    list: (mode: "personal" | "commercial" = "personal") =>
      get<{ models: ModelInfo[]; recommended: Record<string, string[]>; profile: string }>(
        `/models?license_mode=${mode}`,
      ),
    install: (key: string, force = false) =>
      post<{ started: boolean; size_gb: number; log_file: string; detail: string }>(
        "/models/install",
        { key, force },
      ),
    installLog: (key: string) => get<{ log: string }>(`/models/${key}/install-log`),
  },

  voice: {
    profiles: () => get<{ profiles: VoiceProfile[] }>("/voice/profiles"),
    enroll: (profileId: string, form: FormData) =>
      upload<VoiceProfile>(`/voice/profiles/${profileId}/enroll`, form),
    deleteReference: (profileId: string, index: number) =>
      del<VoiceProfile>(`/voice/profiles/${profileId}/references/${index}`),
    synthesize: (payload: {
      text: string;
      profile_id: string;
      emotion?: string;
      speed?: number;
      variants?: number;
      seed?: number | null;
    }) =>
      post<{
        engine: string;
        model: string;
        duration_seconds: number;
        variants: { label: string; duration: number; seed: number | null; url: string }[];
      }>("/voice/synthesize", payload),
    health: () => get<Record<string, unknown>>("/voice/health"),
    setPreferred: (profileId: string, settings: Record<string, unknown>) =>
      post<VoiceProfile>(`/voice/profiles/${profileId}/prefer`, settings),
  },

  templates: {
    list: (includeDisabled = false) =>
      get<{ templates: Template[]; total_duration: number; directory: string }>(
        `/templates?include_disabled=${includeDisabled}`,
      ),
    index: () => post<{ indexed: number; templates: Template[] }>("/templates/index"),
    upload: (form: FormData) =>
      upload<{ template: Template; warnings: string[] }>("/templates/upload", form),
    update: (id: string, payload: Record<string, unknown>) =>
      patch<Template>(`/templates/${id}`, payload),
    remove: (id: string, removeFile = false) =>
      del<{ deleted: boolean }>(`/templates/${id}?remove_file=${removeFile}`),
    plan: (payload: { duration: number; energy?: string; gestures?: string }) =>
      post<{
        strategy: string;
        warnings: string[];
        total_duration: number;
        segments: { template_id: string; start: number; duration: number }[];
        ranking: { id: string; score: number }[];
      }>("/templates/plan", payload),
  },

  projects: {
    list: () => get<{ projects: Project[] }>("/projects"),
    create: (payload: { title?: string; idea?: string }) =>
      post<Project>("/projects", payload),
    get: (id: string) => get<Project>(`/projects/${id}`),
    update: (id: string, payload: Record<string, unknown>) =>
      patch<Project>(`/projects/${id}`, payload),
    getScript: (id: string) =>
      get<{ script: Script | null; idea?: string }>(`/projects/${id}/script`),
    saveScript: (id: string, script: Script) =>
      put<{ script: Script }>(`/projects/${id}/script`, { script }),
    render: (id: string, payload: { preview?: boolean; force_stages?: string[] }) =>
      post<Job>(`/projects/${id}/render`, payload),
    stages: (id: string) =>
      get<{ order: string[]; stages: Record<string, { completed: boolean }> }>(
        `/projects/${id}/stages`,
      ),
  },

  jobs: {
    list: (projectId?: string) =>
      get<{ jobs: Job[]; current: string | null }>(
        `/jobs${projectId ? `?project_id=${projectId}` : ""}`,
      ),
    get: (id: string) => get<Job>(`/jobs/${id}`),
    cancel: (id: string) => post<{ cancelled: boolean; detail: string }>(`/jobs/${id}/cancel`),
    log: (id: string) => get<{ log: string; path?: string }>(`/jobs/${id}/log`),
    stream: (id: string) => new EventSource(`${BASE}/jobs/${id}/stream`),
  },

  script: {
    status: () =>
      get<{
        available: boolean;
        base_url: string;
        models: { name: string; size_gb: number; parameter_size: string }[];
        selected: string | null;
        detail?: string;
      }>("/script/status"),
    presets: () =>
      get<{ presets: { key: string; label: string; guidance: string }[] }>("/script/presets"),
    generate: (payload: {
      idea: string;
      preset: string;
      duration: number;
      model?: string | null;
    }) => post<{ script: Script }>("/script/generate", payload),
    hook: (text: string, model?: string | null) =>
      post<{ result: string }>("/script/hook", { text, model }),
    rewrite: (text: string, instruction: string, model?: string | null) =>
      post<{ result: string }>("/script/rewrite", { text, instruction, model }),
    shorten: (text: string, duration: number, model?: string | null) =>
      post<{ result: string }>("/script/shorten", { text, duration, model }),
    cta: (text: string, model?: string | null) =>
      post<{ result: string }>("/script/cta", { text, model }),
    caption: (text: string, model?: string | null) =>
      post<{ result: string }>("/script/caption", { text, model }),
    hashtags: (text: string, model?: string | null) =>
      post<{ result: string[] }>("/script/hashtags", { text, model }),
    library: (params: { niche?: string; q?: string; templates?: boolean } = {}) => {
      const qs = new URLSearchParams();
      if (params.niche) qs.set("niche", params.niche);
      if (params.q) qs.set("q", params.q);
      if (params.templates !== undefined) qs.set("templates", String(params.templates));
      const suffix = qs.toString() ? `?${qs}` : "";
      return get<{
        total: number;
        count: number;
        niches: LibraryNiche[];
        scripts: LibraryScript[];
      }>(`/script/library${suffix}`);
    },
    libraryBuild: (id: string, values: Record<string, string>) =>
      post<{ script: Script; disclaimer: string }>(
        `/script/library/${id}/build`,
        { values },
      ),
    title: (text: string, model?: string | null) =>
      post<{ result: string }>("/script/title", { text, model }),
  },

  mediaUrl: (path: string) => `${BASE}/media/file?path=${encodeURIComponent(path)}`,
};
