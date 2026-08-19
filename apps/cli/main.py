"""CLI do Local Clone Studio.

A CLI e a interface web chamam a MESMA camada de domínio (`core/`, `services/`).
Nada de lógica de pipeline aqui — só apresentação.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.diagnostics.checks import (  # noqa: E402
    TESTS,
    Check,
    Level,
    run_diagnostics,
)

app = typer.Typer(
    name="clone-studio",
    help="Estúdio local de vídeos verticais com sua voz e seu rosto.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

_LEVEL_STYLE = {
    Level.OK: ("✓", "green"),
    Level.WARN: ("!", "yellow"),
    Level.ERROR: ("✗", "red"),
    Level.INFO: ("·", "dim"),
}


def _render_check(check: Check, indent: str = "  ") -> None:
    mark, style = _LEVEL_STYLE[check.level]
    line = Text(f"{indent}{mark} ", style=style)
    line.append(f"{check.name}: ", style="bold")
    line.append(check.summary)
    console.print(line)
    if check.detail:
        console.print(f"{indent}   [dim]{check.detail}[/dim]")
    if check.hint:
        console.print(f"{indent}   [yellow]→ {check.hint}[/yellow]")


# ---------------------------------------------------------------------------


@app.command()
def doctor(
    json_output: bool = typer.Option(False, "--json", help="saída em JSON"),
    group: Optional[str] = typer.Option(None, "--group", "-g", help="filtra um grupo"),
) -> None:
    """Diagnóstico completo do ambiente."""
    report = run_diagnostics()

    if json_output:
        import json

        console.print_json(json.dumps(report.to_dict(), ensure_ascii=False))
        raise typer.Exit(0 if report.healthy else 1)

    groups: dict[str, list[Check]] = {}
    for check in report.checks:
        if group and check.group != group:
            continue
        groups.setdefault(check.group, []).append(check)

    console.print()
    for name, checks in groups.items():
        console.print(f"[bold cyan]{name}[/bold cyan]")
        for check in checks:
            _render_check(check)
        console.print()

    if report.healthy:
        console.print(
            Panel.fit(
                "Ambiente saudável."
                + (
                    f"  [yellow]{len(report.warnings)} aviso(s)[/yellow]"
                    if report.warnings
                    else ""
                ),
                border_style="green",
            )
        )
    else:
        console.print(
            Panel.fit(
                f"[red]{len(report.errors)} problema(s) bloqueante(s).[/red]\n"
                + "\n".join(f"  • {c.name}: {c.summary}" for c in report.errors),
                border_style="red",
            )
        )
    raise typer.Exit(0 if report.healthy else 1)


@app.command()
def test(
    name: str = typer.Argument(
        ..., help=f"um de: {', '.join(TESTS)}"
    ),
) -> None:
    """Roda um teste específico (os mesmos botões da página Diagnostics)."""
    runner = TESTS.get(name)
    if runner is None:
        console.print(f"[red]Teste desconhecido:[/red] {name}")
        console.print(f"Disponíveis: {', '.join(TESTS)}")
        raise typer.Exit(2)
    console.print(f"\n[bold]Executando teste '{name}'…[/bold]\n")
    result = runner()
    _render_check(result)
    console.print()
    raise typer.Exit(0 if result.level is not Level.ERROR else 1)


@app.command()
def models(
    action: str = typer.Argument("list", help="list | status | install | remove"),
    names: list[str] = typer.Argument(None),
    essential: bool = typer.Option(False, "--essential"),
    force: bool = typer.Option(False, "--force"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Gerencia os pesos dos modelos."""
    from scripts import models as models_module

    argv = [action] + list(names or [])
    if essential:
        argv.append("--essential")
    if force:
        argv.append("--force")
    if yes:
        argv.append("--yes")
    raise typer.Exit(models_module.main(argv))


@app.command("voice-list")
def voice_list() -> None:
    """Lista os perfis de voz cadastrados."""
    from services.tts.qwen3_tts import list_voice_profiles

    profiles = list_voice_profiles()
    if not profiles:
        console.print(
            "\n[yellow]Nenhum perfil de voz cadastrado.[/yellow]\n"
            "Cadastre com: [bold]clone-studio voice-enroll "
            "--audio amostra.wav --text 'transcrição'[/bold]\n"
        )
        raise typer.Exit(1)

    table = Table(title="Perfis de voz", show_lines=False)
    table.add_column("id", style="bold")
    table.add_column("nome")
    table.add_column("amostras", justify="right")
    table.add_column("duração total", justify="right")
    table.add_column("idioma")
    for profile in profiles:
        total = sum(r.duration for r in profile.references)
        table.add_row(
            profile.id,
            profile.display_name,
            str(len(profile.references)),
            f"{total:.1f}s",
            profile.language,
        )
    console.print()
    console.print(table)
    console.print()


@app.command("voice-enroll")
def voice_enroll(
    audio: Path = typer.Option(..., "--audio", "-a", exists=True,
                               help="WAV/MP3 com sua voz limpa"),
    text: str = typer.Option(..., "--text", "-t",
                             help="transcrição exata do que é falado no áudio"),
    profile_id: str = typer.Option("me", "--id", help="identificador do perfil"),
    name: str = typer.Option("", "--name", help="nome de exibição"),
) -> None:
    """Cadastra (ou adiciona uma amostra a) um perfil de voz."""
    from services.tts.qwen3_tts import Qwen3TTSEngine

    engine = Qwen3TTSEngine()
    with console.status("Processando amostra…"):
        profile = engine.clone_voice(
            audio_path=audio,
            transcript=text,
            profile_id=profile_id,
            display_name=name or profile_id,
        )
    reference = profile.references[-1]
    console.print(
        Panel.fit(
            f"[green]Perfil '{profile.id}' atualizado.[/green]\n"
            f"amostras: {len(profile.references)}\n"
            f"última: {reference.audio_path} ({reference.duration:.1f}s)",
            border_style="green",
        )
    )


@app.command("voice-test")
def voice_test(
    text: str = typer.Option(
        "A inteligência artificial não vai substituir programadores. "
        "Ela vai substituir programadores que não usam inteligência artificial.",
        "--text",
        "-t",
    ),
    profile_id: str = typer.Option("me", "--id"),
    output: Path = typer.Option(Path("exports/voice-test.wav"), "--output", "-o"),
    variants: int = typer.Option(1, "--variants", "-n", help="gera N versões (A/B)"),
    speed: float = typer.Option(1.0, "--speed"),
    emotion: str = typer.Option("", "--emotion"),
) -> None:
    """Gera um WAV com sua voz clonada — o milestone 1 do projeto."""
    from services.tts.base import SynthesisRequest
    from services.tts.qwen3_tts import Qwen3TTSEngine, load_voice_profile

    engine = Qwen3TTSEngine()
    try:
        profile = load_voice_profile(profile_id)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"\n[bold]Modelo:[/bold] {engine.model_repo}")
    console.print(f"[bold]Perfil:[/bold] {profile.display_name}")
    console.print(f"[bold]Texto :[/bold] {text[:90]}{'…' if len(text) > 90 else ''}\n")

    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("iniciando…", total=100)

        def on_progress(pct: float, message: str) -> None:
            progress.update(task, completed=pct * 100, description=message or "…")

        result = engine.synthesize(
            SynthesisRequest(
                text=text,
                voice_profile=profile,
                output_path=output,
                language=engine.settings.tts.language,
                speed=speed,
                emotion=emotion,
                variants=variants,
                max_new_tokens=engine.settings.tts.max_new_tokens,
            ),
            on_progress=on_progress,
        )
        progress.update(task, completed=100, description="concluído")

    table = Table(title="Variantes geradas")
    table.add_column("var")
    table.add_column("duração", justify="right")
    table.add_column("seed", justify="right")
    table.add_column("arquivo")
    for variant in result.variants:
        table.add_row(
            variant.label,
            f"{variant.duration:.2f}s",
            str(variant.seed if variant.seed is not None else "-"),
            str(variant.path),
        )
    console.print()
    console.print(table)
    console.print(
        f"\n[dim]tempo de geração: {result.duration_seconds:.1f}s"
        f" · modelo {result.model}[/dim]\n"
    )


@app.command()
def create(
    idea: str = typer.Argument(..., help="a ideia do vídeo"),
    title: str = typer.Option("", "--title", "-t"),
) -> None:
    """Cria um projeto novo."""
    from core.storage.project import create_project

    project = create_project(title or idea[:70], idea)
    console.print(
        Panel.fit(
            f"[green]Projeto criado.[/green]\n"
            f"id  : [bold]{project.id}[/bold]\n"
            f"dir : {project.directory}\n\n"
            f"Próximo passo:\n"
            f"  clone-studio render {project.id}",
            border_style="green",
        )
    )


@app.command("projects")
def projects_list() -> None:
    """Lista os projetos existentes."""
    from core.storage.project import list_projects

    items = list_projects()
    if not items:
        console.print("\n[yellow]Nenhum projeto ainda.[/yellow]\n")
        raise typer.Exit(1)

    table = Table(title="Projetos")
    table.add_column("id", style="bold")
    table.add_column("título")
    table.add_column("etapas", justify="right")
    table.add_column("renders", justify="right")
    table.add_column("atualizado")
    for project in items:
        done = sum(1 for s in project.stages.values() if s.status == "completed")
        renders = (
            len(list(project.renders_dir.glob("*.mp4")))
            if project.renders_dir.exists()
            else 0
        )
        table.add_row(
            project.id,
            project.title[:44],
            str(done),
            str(renders),
            project.updated_at[:16].replace("T", " "),
        )
    console.print()
    console.print(table)
    console.print()


def _run_pipeline_cli(project_id: str, preview: bool, force: list[str]) -> None:
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    from core.pipeline.context import PipelineCancelled, StageFailed
    from core.pipeline.runner import run_pipeline
    from core.storage.project import Project

    try:
        project = Project.load(project_id)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    modo = "preview" if preview else "render final"
    console.print(f"\n[bold]{project.title}[/bold]  [dim]{project.id}[/dim]")
    console.print(f"[dim]modo: {modo}[/dim]\n")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=32),
        TextColumn("{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("iniciando…", total=100)

        def on_progress(fraction: float, message: str) -> None:
            progress.update(task, completed=fraction * 100, description=message[:64])

        try:
            result = run_pipeline(
                project,
                preview=preview,
                force_stages=set(force),
                on_progress=on_progress,
            )
        except PipelineCancelled:
            console.print("\n[yellow]Cancelado.[/yellow]")
            raise typer.Exit(130) from None
        except StageFailed as exc:
            progress.stop()
            lines = [f"[red]Falha na etapa '{exc.stage}'[/red]", exc.message]
            if exc.hint:
                lines.append(f"\n[yellow]Provável solução:[/yellow] {exc.hint}")
            if exc.exit_code is not None:
                lines.append(f"[dim]exit code: {exc.exit_code}[/dim]")
            if exc.command:
                lines.append(f"[dim]comando: {' '.join(exc.command)}[/dim]")
            if exc.log_file:
                lines.append(f"[dim]log: {exc.log_file}[/dim]")
            console.print(Panel("\n".join(lines), border_style="red"))
            raise typer.Exit(1) from exc

    for warning in result.warnings:
        console.print(f"[yellow]! {warning}[/yellow]")

    table = Table(title="Etapas")
    table.add_column("etapa")
    table.add_column("tempo", justify="right")
    for name, seconds in result.stages.items():
        table.add_row(name, f"{seconds:.1f}s")
    console.print()
    console.print(table)

    console.print(
        Panel.fit(
            f"[green]{result.output}[/green]\n"
            f"tempo total: {result.duration_seconds:.1f}s",
            border_style="green",
            title="pronto",
        )
    )


@app.command()
def render(
    project_id: str = typer.Argument(...),
    force: list[str] = typer.Option(
        [], "--force", "-f", help="reexecuta esta etapa (e as posteriores)"
    ),
) -> None:
    """Renderiza o vídeo final em 1080x1920."""
    _run_pipeline_cli(project_id, preview=False, force=force)


@app.command()
def preview(
    project_id: str = typer.Argument(...),
    force: list[str] = typer.Option([], "--force", "-f"),
) -> None:
    """Gera um preview rápido (540x960, primeiros segundos)."""
    _run_pipeline_cli(project_id, preview=True, force=force)


@app.command("templates")
def templates_cmd(
    reindex: bool = typer.Option(False, "--reindex", help="revarre o diretório"),
) -> None:
    """Lista (ou reindexa) os templates de vídeo."""
    from services.templates.library import index_templates, load_templates

    items = index_templates() if reindex else load_templates()
    if not items:
        console.print(
            "\n[yellow]Nenhum template.[/yellow]\n"
            "Coloque vídeos verticais seus em data/identity/templates/ e rode "
            "com --reindex.\n"
        )
        raise typer.Exit(1)

    table = Table(title="Templates")
    table.add_column("id", style="bold")
    table.add_column("duração", justify="right")
    table.add_column("resolução")
    table.add_column("estilo")
    table.add_column("energia")
    for item in items:
        table.add_row(
            item.id,
            f"{item.duration:.1f}s",
            f"{item.width}x{item.height}",
            item.style,
            item.energy,
        )
    console.print()
    console.print(table)
    console.print(
        f"\n[dim]total: {sum(t.duration for t in items):.1f}s de material[/dim]\n"
    )


@app.command("scripts")
def scripts_cmd(
    action: str = typer.Argument("list", help="list | show | use"),
    script_id: str = typer.Argument("", help="id do roteiro (show/use)"),
    niche: str = typer.Option("", "--niche", "-n", help="filtra por nicho"),
    query: str = typer.Option("", "--query", "-q", help="busca por texto"),
    project: str = typer.Option("", "--project", "-p", help="projeto de destino"),
    fill: list[str] = typer.Option(
        [], "--fill", "-f", help="preenche lacuna: -f NOME=valor"
    ),
) -> None:
    """Biblioteca local de roteiros prontos (não precisa de Ollama)."""
    from services.llm.library import NICHE_LABELS, LibraryError, get_library

    biblioteca = get_library()

    if action == "list":
        itens = biblioteca.filter(niche=niche or None, query=query)
        if not itens:
            console.print("\n[yellow]Nenhum roteiro encontrado.[/yellow]\n")
            raise typer.Exit(1)

        table = Table(title=f"Biblioteca de roteiros ({len(itens)} de {len(biblioteca)})")
        table.add_column("id", style="bold")
        table.add_column("nicho")
        table.add_column("título")
        table.add_column("tipo")
        table.add_column("~s", justify="right")
        for item in itens:
            table.add_row(
                item.id,
                NICHE_LABELS.get(item.niche, item.niche),
                item.title[:46],
                f"[yellow]{len(item.slots)} lacunas[/yellow]" if item.is_template else "pronto",
                f"{item.word_count / 2.6:.0f}",
            )
        console.print()
        console.print(table)
        console.print(
            "\n[dim]clone-studio scripts show <id>   ver o texto completo[/dim]"
        )
        console.print(
            "[dim]clone-studio scripts use <id> -p <projeto>   aplicar[/dim]\n"
        )
        return

    if not script_id:
        console.print("[red]Informe o id do roteiro.[/red]")
        raise typer.Exit(2)

    try:
        item = biblioteca.require(script_id)
    except LibraryError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if action == "show":
        console.print(f"\n[bold]{item.title}[/bold]")
        console.print(f"[dim]{item.id} · {NICHE_LABELS.get(item.niche, item.niche)}"
                      f" · preset {item.preset}[/dim]\n")
        console.print(f"[bold cyan]HOOK[/bold cyan]  {item.hook}\n")
        for indice, cena in enumerate(item.scenes, start=1):
            console.print(f"[bold]{indice}.[/bold] {cena}\n")
        if item.cta:
            console.print(f"[bold cyan]CTA[/bold cyan]   {item.cta}")
        if item.hashtags:
            console.print(f"[dim]{' '.join(item.hashtags)}[/dim]")
        if item.slots:
            console.print(f"\n[yellow]Lacunas a preencher:[/yellow] "
                          f"{', '.join(item.slots)}")
        if item.notes:
            console.print(f"\n[dim]Nota: {item.notes}[/dim]")
        if item.disclaimer:
            console.print(f"\n[yellow]{item.disclaimer}[/yellow]")
        console.print()
        return

    if action == "use":
        valores: dict[str, str] = {}
        for par in fill:
            if "=" not in par:
                console.print(f"[red]Formato inválido: {par}. Use NOME=valor.[/red]")
                raise typer.Exit(2)
            chave, _, valor = par.partition("=")
            valores[chave.strip()] = valor.strip()

        faltando = item.missing_slots(valores)
        if faltando:
            console.print(
                f"\n[red]Faltam lacunas:[/red] {', '.join(faltando)}\n"
                f"[dim]Use -f NOME=valor para cada uma.[/dim]\n"
            )
            raise typer.Exit(1)

        roteiro = item.to_script(valores)

        if not project:
            console.print(f"\n[bold]{roteiro.title}[/bold]\n")
            console.print(roteiro.full_text)
            console.print(
                "\n[dim]Use -p <projeto> para gravar num projeto.[/dim]\n"
            )
            return

        from core.pipeline.stages import STAGE_ORDER
        from core.storage.project import Project

        try:
            alvo = Project.load(project)
        except (FileNotFoundError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc

        roteiro.save(alvo.script_file)
        alvo.title = roteiro.title or alvo.title
        alvo.voice.emotion = roteiro.voice_style.emotion
        alvo.voice.speed = roteiro.voice_style.speed
        alvo.invalidate_from("tts", STAGE_ORDER)
        alvo.save()

        console.print(
            Panel.fit(
                f"[green]Roteiro aplicado.[/green]\n"
                f"projeto: [bold]{alvo.id}[/bold]\n"
                f"origem : {item.id}\n\n"
                f"Próximo passo:\n  clone-studio preview {alvo.id}",
                border_style="green",
            )
        )
        if item.disclaimer:
            console.print(f"\n[yellow]{item.disclaimer}[/yellow]\n")
        return

    console.print(f"[red]Ação desconhecida: {action}[/red] (list | show | use)")
    raise typer.Exit(2)


@app.command()
def config(
    show: bool = typer.Option(True, "--show"),
) -> None:
    """Mostra a configuração efetiva (default + local)."""
    import json

    from core.config.loader import active_profile, load_settings

    settings = load_settings()
    console.print(f"\n[bold]Perfil de hardware ativo:[/bold] {active_profile()}\n")
    console.print_json(json.dumps(settings.model_dump(), ensure_ascii=False))


def run() -> None:
    app()


if __name__ == "__main__":
    run()
