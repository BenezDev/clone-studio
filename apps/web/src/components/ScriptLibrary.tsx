import { useMemo, useState } from "react";

import { Alert, Field, Panel, Pill, Spinner, useAsync } from "./ui";
import { LibraryScript, Script, api } from "../lib/api";

/**
 * Seletor da biblioteca local de roteiros.
 *
 * Existe para o estúdio ser utilizável sem o Ollama. Os roteiros vêm de
 * arquivos versionados no projeto — sem rede, sem modelo de linguagem.
 */
export function ScriptLibrary({
  onPick,
}: {
  onPick: (script: Script, disclaimer: string) => void;
}) {
  const catalog = useAsync(() => api.script.library(), []);
  const [niche, setNiche] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<LibraryScript | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const visible = useMemo(() => {
    const all = catalog.data?.scripts ?? [];
    const term = query.trim().toLowerCase();
    return all.filter(
      (s) =>
        (!niche || s.niche === niche) &&
        (!term ||
          s.title.toLowerCase().includes(term) ||
          s.hook.toLowerCase().includes(term) ||
          s.tags.some((t) => t.toLowerCase().includes(term))),
    );
  }, [catalog.data, niche, query]);

  const missing = selected
    ? selected.slots.filter((s) => !(values[s] ?? "").trim())
    : [];

  async function apply() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const response = await api.script.libraryBuild(selected.id, values);
      onPick(response.script, response.disclaimer);
      setSelected(null);
      setValues({});
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (catalog.loading) return <Spinner />;

  return (
    <Panel
      title="Biblioteca de roteiros"
      desc={`${catalog.data?.total ?? 0} roteiros prontos, sem precisar de Ollama nem de internet.`}
    >
      <div className="row row-wrap" style={{ marginBottom: 12 }}>
        <button
          className={`btn btn-sm ${!niche ? "btn-primary" : ""}`}
          onClick={() => setNiche("")}
        >
          todos
        </button>
        {catalog.data?.niches.map((n) => (
          <button
            key={n.key}
            className={`btn btn-sm ${niche === n.key ? "btn-primary" : ""}`}
            onClick={() => setNiche(n.key)}
          >
            {n.label} ({n.count})
          </button>
        ))}
      </div>

      <input
        className="input"
        placeholder="buscar por título, hook ou tema…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        style={{ marginBottom: 14 }}
      />

      {!selected && (
        <div style={{ maxHeight: 380, overflowY: "auto" }}>
          {visible.map((item) => (
            <div
              key={item.id}
              className="list-item"
              style={{ cursor: "pointer" }}
              onClick={() => {
                setSelected(item);
                setValues({});
                setError(null);
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="small bold truncate">{item.title}</div>
                <div className="tiny dim truncate">{item.hook}</div>
              </div>
              {item.is_template ? (
                <Pill level="warn">{item.slots.length} lacunas</Pill>
              ) : (
                <Pill level="ok">pronto</Pill>
              )}
              <span className="tiny dim">
                ~{Math.round(item.word_count / 2.6)}s
              </span>
            </div>
          ))}
          {visible.length === 0 && (
            <div className="tiny dim">Nenhum roteiro com esse filtro.</div>
          )}
        </div>
      )}

      {selected && (
        <div>
          <div className="row row-between" style={{ marginBottom: 8 }}>
            <span className="bold">{selected.title}</span>
            <button
              className="btn btn-sm btn-ghost"
              onClick={() => setSelected(null)}
            >
              ← voltar
            </button>
          </div>

          {selected.disclaimer && (
            <Alert level="warn">{selected.disclaimer}</Alert>
          )}
          {selected.notes && (
            <Alert level="info" title="Como usar">
              {selected.notes}
            </Alert>
          )}

          <div className="code" style={{ marginBottom: 12, maxHeight: 200 }}>
            {selected.hook}
            {"\n\n"}
            {selected.scenes.join("\n\n")}
          </div>

          {selected.slots.length > 0 && (
            <>
              <div className="label" style={{ marginBottom: 6 }}>
                Preencha as lacunas — o vídeo não é gerado sem isso
              </div>
              {selected.slots.map((slot) => (
                <Field key={slot} label={slot.replace(/_/g, " ").toLowerCase()}>
                  <input
                    className="input"
                    value={values[slot] ?? ""}
                    onChange={(e) =>
                      setValues({ ...values, [slot]: e.target.value })
                    }
                  />
                </Field>
              ))}
            </>
          )}

          {error && <Alert level="error">{error}</Alert>}

          <button
            className="btn btn-primary"
            onClick={apply}
            disabled={busy || missing.length > 0}
            title={
              missing.length
                ? `Faltam: ${missing.join(", ")}`
                : "Usar este roteiro"
            }
          >
            {busy ? <Spinner /> : null}{" "}
            {missing.length > 0
              ? `Faltam ${missing.length} lacuna(s)`
              : "Usar este roteiro"}
          </button>
        </div>
      )}
    </Panel>
  );
}
