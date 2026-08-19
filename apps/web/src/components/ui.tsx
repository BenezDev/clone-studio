/** Componentes de UI compartilhados. */

import { ReactNode, useEffect, useState } from "react";

import { ApiError, Level } from "../lib/api";

export function Panel({
  title,
  desc,
  action,
  children,
  className = "",
}: {
  title?: string;
  desc?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`panel ${className}`}>
      {title && (
        <div className="panel-title">
          <span>{title}</span>
          {action}
        </div>
      )}
      {desc && <div className="panel-desc">{desc}</div>}
      {children}
    </div>
  );
}

export function Pill({
  level = "info",
  children,
}: {
  level?: Level | "accent";
  children: ReactNode;
}) {
  return <span className={`pill pill-${level}`}>{children}</span>;
}

export function Dot({ level }: { level: Level }) {
  return <span className={`dot dot-${level}`} />;
}

export function Alert({
  level = "info",
  title,
  children,
}: {
  level?: "error" | "warn" | "info" | "ok";
  title?: string;
  children?: ReactNode;
}) {
  return (
    <div className={`alert alert-${level}`}>
      <div style={{ flex: 1 }}>
        {title && <div className="alert-title">{title}</div>}
        {children && <div className="alert-body">{children}</div>}
      </div>
    </div>
  );
}

/** Erro de API com dica acionável — nunca só "falhou". */
export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;

  const apiError = error instanceof ApiError ? error : null;
  const detail = apiError?.detail as Record<string, unknown> | undefined;
  const stage = detail?.stage as string | undefined;
  const hint = apiError?.hint ?? (detail?.hint as string | undefined);
  const command = detail?.command as string[] | undefined;
  const exitCode = detail?.exit_code as number | undefined;
  const logFile = detail?.log_file as string | undefined;

  return (
    <div className="alert alert-error">
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="alert-title">
          {stage ? `Falha na etapa "${stage}"` : "Erro"}
        </div>
        <div className="alert-body" style={{ marginBottom: hint ? 8 : 0 }}>
          {error instanceof Error ? error.message : String(error)}
        </div>
        {hint && (
          <div className="alert-body" style={{ color: "var(--warn)" }}>
            → {hint}
          </div>
        )}
        {typeof exitCode === "number" && (
          <div className="tiny dim mt-1">exit code: {exitCode}</div>
        )}
        {command && (
          <div className="code mt-1" style={{ maxHeight: 90 }}>
            {command.join(" ")}
          </div>
        )}
        {logFile && <div className="tiny dim mt-1">log: {logFile}</div>}
      </div>
    </div>
  );
}

export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      {children && <div className="small">{children}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label className="label">{label}</label>
      {children}
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

export function Progress({ value }: { value: number }) {
  return (
    <div className="progress">
      <div
        className="progress-bar"
        style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
      />
    </div>
  );
}

export function Spinner() {
  return <span className="spin">⟳</span>;
}

export function Stat({
  label,
  value,
  sub,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
}) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

/** Zonas seguras do TikTok/Reels — overlay de edição, fora do render final. */
export function SafeZones({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <div className="safe-zones">
      <div className="safe-zone safe-zone-top">
        <span className="safe-label" style={{ top: 4, left: 6 }}>
          topo — evite texto
        </span>
      </div>
      <div className="safe-zone-right">
        <span className="safe-label" style={{ top: 4, right: 6 }}>
          botões
        </span>
      </div>
      <div className="safe-zone safe-zone-bottom">
        <span className="safe-label" style={{ bottom: 4, left: 6 }}>
          legenda e perfil da plataforma
        </span>
      </div>
    </div>
  );
}

/** Hook de carregamento com estados de erro explícitos. */
export function useAsync<T>(
  loader: () => Promise<T>,
  deps: unknown[] = [],
): {
  data: T | null;
  error: unknown;
  loading: boolean;
  reload: () => void;
} {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    loader()
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err) => {
        if (!cancelled) setError(err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { data, error, loading, reload: () => setNonce((n) => n + 1) };
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  if (bytes >= 1e3) return `${(bytes / 1e3).toFixed(0)} KB`;
  return `${bytes} B`;
}

export function formatDuration(seconds: number): string {
  if (!seconds || seconds < 0) return "0s";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${rest}s`;
}

export function formatDate(iso: string): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("pt-BR", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
