"""Etapas do pipeline de produção.

Fluxo do Production Mode:

    roteiro -> voz clonada -> template meu -> lip-sync -> legendas -> edição
    -> render 9:16

Cada etapa:
  * checa cancelamento antes de começar;
  * pula a si mesma se já concluída e o artefato ainda existe (resume);
  * VALIDA o próprio arquivo de saída com ffprobe antes de declarar sucesso.

A validação de saída é o que impede o erro clássico de um estágio "passar"
e o problema só aparecer no render final, 20 minutos depois.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core import platform_hints as hints
from core.media import ffmpeg
from core.pipeline.context import PipelineCancelled, PipelineContext, StageFailed
from core.storage.paths import get_paths
from core.worker.runner import EngineNotInstalled, WorkerCancelled, WorkerError

StageFunction = Callable[[PipelineContext], None]


@dataclass(frozen=True)
class Stage:
    name: str
    label: str
    run: StageFunction
    weight: float = 1.0
    optional: bool = False


# ---------------------------------------------------------------------------
# 1. Preparação
# ---------------------------------------------------------------------------


def stage_prepare(ctx: PipelineContext) -> None:
    ctx.check_cancelled()
    ctx.emit(0.0, "Preparando projeto…")

    project = ctx.project
    project.ensure_dirs()

    from services.tts.qwen3_tts import load_voice_profile

    try:
        profile = load_voice_profile(project.voice.profile_id)
    except FileNotFoundError as exc:
        raise StageFailed(
            "prepare",
            f"Perfil de voz '{project.voice.profile_id}' não encontrado.",
            hint="Cadastre sua voz na página Voice ou com "
                 "`clone-studio voice-enroll`.",
        ) from exc

    if not profile.references:
        raise StageFailed(
            "prepare",
            f"O perfil '{profile.id}' não tem nenhuma amostra de voz.",
            hint="Adicione ao menos 3 segundos de fala limpa.",
        )

    ctx.set("voice_profile", profile)

    script_text = _load_script_text(ctx)
    if not script_text.strip():
        raise StageFailed(
            "prepare",
            "O roteiro está vazio.",
            hint="Escreva ou gere o roteiro antes de renderizar.",
        )
    ctx.set("script_text", script_text)
    ctx.emit(1.0, "Projeto pronto.")


def _load_script_text(ctx: PipelineContext) -> str:
    """Lê o texto a ser falado — do script.json ou da ideia bruta.

    Guarda as cenas em `script_scenes` de passagem: elas carregam as palavras
    enfatizadas, que o auto editor usa para decidir onde fechar o quadro. O
    autor já marcou o que importa; não faz sentido adivinhar depois.
    """
    import json

    project = ctx.project
    if project.script_file.exists():
        try:
            raw = json.loads(project.script_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StageFailed(
                "prepare",
                f"script.json inválido: {exc}",
                hint="Corrija ou apague o arquivo para regerar o roteiro.",
            ) from exc

        scenes = raw.get("scenes") or []
        if scenes:
            ctx.set("script_scenes", scenes)
            return " ".join(s.get("text", "").strip() for s in scenes).strip()
        if raw.get("text"):
            return str(raw["text"])
    return project.idea


# ---------------------------------------------------------------------------
# 2. Voz
# ---------------------------------------------------------------------------


def stage_tts(ctx: PipelineContext) -> None:
    ctx.check_cancelled()
    project = ctx.project
    output = project.voice_file

    if not ctx.should_run("tts") and output.exists():
        ffmpeg.validate_audio(output, min_duration=0.15)
        ctx.set("voice_audio", output)
        ctx.emit(1.0, "Áudio reaproveitado da execução anterior.")
        return

    from services.tts.base import SynthesisRequest
    from services.tts.qwen3_tts import Qwen3TTSEngine

    engine = Qwen3TTSEngine()
    started = time.monotonic()

    try:
        result = engine.synthesize(
            SynthesisRequest(
                text=ctx.require("script_text"),
                voice_profile=ctx.require("voice_profile"),
                output_path=output,
                language=ctx.settings.tts.language,
                speed=project.voice.speed,
                emotion=project.voice.emotion,
                seed=project.voice.seed,
                variants=1,
                max_new_tokens=ctx.settings.tts.max_new_tokens,
            ),
            on_progress=ctx.emit,
            should_cancel=ctx.should_cancel,
        )
    except WorkerCancelled as exc:
        raise PipelineCancelled(str(exc)) from exc
    except EngineNotInstalled as exc:
        raise StageFailed(
            "tts", str(exc), hint=f"Rode {hints.install('qwen-tts')}."
        ) from exc
    except WorkerError as exc:
        raise StageFailed(
            "tts",
            exc.message,
            hint=exc.hint,
            detail=exc.stderr_tail,
            log_file=exc.log_file,
            command=exc.command,
            exit_code=exc.exit_code,
        ) from exc

    info = ffmpeg.validate_audio(output, min_duration=0.3)
    ctx.set("voice_audio", output)
    ctx.set("audio_duration", info.duration)
    project.engines["tts"] = f"{result.engine}:{result.model}"
    project.record_stage(
        "tts", output=output, duration_seconds=time.monotonic() - started
    )
    project.save()


# ---------------------------------------------------------------------------
# 3. Template
# ---------------------------------------------------------------------------


def stage_template(ctx: PipelineContext) -> None:
    ctx.check_cancelled()
    project = ctx.project
    audio = ctx.require("voice_audio")
    duration = ctx.get("audio_duration") or ffmpeg.probe(audio).duration
    ctx.set("audio_duration", duration)

    output = project.stages_dir / "template.mp4"
    if not ctx.should_run("template") and output.exists():
        ffmpeg.validate_video(output, min_duration=0.2)
        ctx.set("template_video", output)
        ctx.emit(1.0, "Template reaproveitado.")
        return

    from services.templates.library import (
        TemplateError,
        criteria_from_script,
        load_templates,
        plan_composition,
        render_plan,
    )

    ctx.emit(0.1, "Escolhendo template…")
    templates = load_templates()
    if not templates:
        raise StageFailed(
            "template",
            "Nenhum template de vídeo cadastrado.",
            hint="Grave vídeos verticais seus e coloque em "
                 "data/identity/templates/ (a página Templates faz a indexação).",
        )

    if project.template.mode == "manual" and project.template.template_id:
        chosen = next(
            (t for t in templates if t.id == project.template.template_id), None
        )
        if chosen is None:
            raise StageFailed(
                "template",
                f"Template '{project.template.template_id}' não existe mais.",
                hint="Escolha outro na página Templates ou volte para o modo auto.",
            )
        templates = [chosen]

    criteria = criteria_from_script(
        emotion=project.voice.emotion, audio_duration=duration
    )

    try:
        plan = plan_composition(duration, criteria, templates)
    except TemplateError as exc:
        raise StageFailed("template", str(exc)) from exc

    for warning in plan.warnings:
        ctx.emit(0.2, warning)

    ctx.emit(0.3, f"Compondo vídeo base ({plan.strategy}, {len(plan.segments)} trecho(s))…")
    started = time.monotonic()
    render_plan(plan, output, fps=ctx.settings.lipsync.fps)

    info = ffmpeg.validate_video(output, min_duration=min(0.2, duration))
    ctx.set("template_video", output)
    ctx.set("template_plan", plan)

    project.template.plan = {
        "strategy": plan.strategy,
        "segments": [
            {
                "template_id": s.template_id,
                "start": s.start,
                "duration": s.duration,
            }
            for s in plan.segments
        ],
        "warnings": plan.warnings,
    }
    project.record_stage(
        "template", output=output, duration_seconds=time.monotonic() - started
    )
    project.save()
    ctx.emit(1.0, f"Base pronta: {info.duration:.1f}s")


# ---------------------------------------------------------------------------
# 4. Lip-sync
# ---------------------------------------------------------------------------


def _template_cache_seed(ctx: PipelineContext) -> dict | None:
    """Descreve a composição do template de forma estável.

    O vídeo entregue ao lip-sync é reencodado pelo template engine, e o x264
    multi-thread não é determinístico: duas composições idênticas produzem
    bytes diferentes. Cachear pelo hash do arquivo composto faria o
    preprocessamento (~5 s por frame) ser refeito sempre.

    A chave passa a ser: conteúdo dos vídeos de origem + como foram cortados.
    """
    from core.storage.cache import hash_file

    plan = ctx.project.template.plan
    segments = plan.get("segments") if isinstance(plan, dict) else None
    if not segments:
        return None

    paths = get_paths()
    sources: list[dict[str, Any]] = []
    for segment in segments:
        template_id = segment.get("template_id", "")
        video = next(
            (
                paths.root / t.video_path
                for t in _cached_templates()
                if t.id == template_id and t.video_path
            ),
            None,
        )
        if video is None or not video.exists():
            # Sem conseguir identificar a origem, o hash do arquivo composto
            # continua sendo correto — só não reaproveita entre execuções.
            return None
        sources.append(
            {
                "template_id": template_id,
                "content": hash_file(video),
                "start": segment.get("start"),
                "duration": segment.get("duration"),
            }
        )

    return {"fps": ctx.settings.lipsync.fps, "segments": sources}


def _cached_templates():
    from services.templates.library import load_templates

    return load_templates(only_enabled=False)


def stage_lipsync(ctx: PipelineContext) -> None:
    ctx.check_cancelled()
    project = ctx.project
    output = project.stages_dir / "lipsync.mp4"

    if not ctx.should_run("lipsync") and output.exists():
        ffmpeg.validate_video(output, min_duration=0.2)
        ctx.set("lipsync_video", output)
        ctx.emit(1.0, "Lip-sync reaproveitado.")
        return

    from core.worker.runner import engine_available

    template_video = ctx.require("template_video")
    audio = ctx.require("voice_audio")

    if not engine_available("musetalk"):
        # Degradação honesta: sem a engine, o vídeo sai sem sincronia labial.
        # Melhor entregar isso avisando do que falhar o render inteiro.
        ctx.emit(
            0.5,
            "MuseTalk não instalado — seguindo sem lip-sync "
            f"(instale com {hints.install(musetalk=True)}).",
        )
        ctx.set("lipsync_video", template_video)
        ctx.set("lipsync_skipped", True)
        return

    from services.lipsync.musetalk import MuseTalkEngine

    engine = MuseTalkEngine()
    started = time.monotonic()
    try:
        engine.process(
            video_path=template_video,
            audio_path=audio,
            output_path=output,
            on_progress=ctx.emit,
            cache_seed=_template_cache_seed(ctx),
            should_cancel=ctx.should_cancel,
        )
    except WorkerCancelled as exc:
        raise PipelineCancelled(str(exc)) from exc
    except EngineNotInstalled as exc:
        raise StageFailed(
            "lipsync", str(exc), hint=hints.install(musetalk=True)
        ) from exc
    except WorkerError as exc:
        raise StageFailed(
            "lipsync",
            exc.message,
            hint=exc.hint,
            detail=exc.stderr_tail,
            log_file=exc.log_file,
            command=exc.command,
            exit_code=exc.exit_code,
        ) from exc

    ffmpeg.validate_video(output, min_duration=0.2)
    ctx.set("lipsync_video", output)
    project.engines["lipsync"] = "musetalk:v15"
    project.record_stage(
        "lipsync", output=output, duration_seconds=time.monotonic() - started
    )
    project.save()


# ---------------------------------------------------------------------------
# 5. Legendas
# ---------------------------------------------------------------------------


def stage_captions(ctx: PipelineContext) -> None:
    ctx.check_cancelled()
    project = ctx.project

    if not project.captions.enabled:
        ctx.set("captions_ass", None)
        ctx.emit(1.0, "Legendas desativadas para este projeto.")
        return

    output = project.captions_ass
    if not ctx.should_run("captions") and output.exists():
        ctx.set("captions_ass", output)
        # A transcrição também precisa voltar ao contexto: o auto editor corta
        # nos limites de frase, e sem ela cairia no modo por tempo em toda
        # execução que reaproveita legendas — que é o caso comum de um resume.
        transcript_file = project.directory / "transcript.json"
        if transcript_file.exists():
            from services.transcription.base import Transcript

            try:
                ctx.set("transcript", Transcript.load(transcript_file))
            except Exception:  # noqa: BLE001 - transcrição ilegível não derruba o render
                ctx.emit(0.5, "transcript.json ilegível; o auto editor cortará por tempo.")
        ctx.emit(1.0, "Legendas reaproveitadas.")
        return

    from services.transcription.faster_whisper import get_transcription_engine
    from services.video.captions import write_captions

    audio = ctx.require("voice_audio")
    started = time.monotonic()

    ctx.emit(0.05, "Transcrevendo o áudio gerado…")
    engine = get_transcription_engine()
    try:
        transcript = engine.transcribe(
            audio,
            language=ctx.settings.transcription.language,
            # O roteiro é o texto exato falado: usá-lo como prompt melhora
            # nomes próprios e termos técnicos.
            initial_prompt=ctx.get("script_text", "")[:800],
            on_progress=lambda p, m: ctx.emit(0.05 + p * 0.8, m),
            should_cancel=ctx.should_cancel,
        )
    except WorkerCancelled as exc:
        raise PipelineCancelled(str(exc)) from exc
    except EngineNotInstalled as exc:
        raise StageFailed(
            "captions", str(exc), hint=hints.install("whisper")
        ) from exc
    except WorkerError as exc:
        raise StageFailed(
            "captions",
            exc.message,
            hint=exc.hint,
            detail=exc.stderr_tail,
            log_file=exc.log_file,
        ) from exc

    if not transcript.words:
        raise StageFailed(
            "captions",
            "A transcrição não produziu timestamps por palavra.",
            hint="Verifique se o áudio tem fala audível "
                 "(faster-whisper devolve vazio em silêncio).",
        )

    ctx.emit(0.9, "Montando legendas…")
    render = project.render
    files = write_captions(
        transcript,
        project.directory,
        preset_key=project.captions.preset,
        width=render.width,
        height=render.height,
        uppercase=project.captions.uppercase,
        words_per_cue=project.captions.words_per_cue,
        safe_bottom=ctx.settings.captions.safe_bottom,
        basename="captions",
    )
    transcript.save(project.directory / "transcript.json")

    ctx.set("captions_ass", files["ass"])
    ctx.set("transcript", transcript)
    project.engines["transcription"] = f"{transcript.engine}:{transcript.model}"
    project.record_stage(
        "captions", output=files["ass"], duration_seconds=time.monotonic() - started
    )
    project.save()
    ctx.emit(1.0, f"{len(transcript.words)} palavras legendadas.")


# ---------------------------------------------------------------------------
# 6. Edição automática
# ---------------------------------------------------------------------------


def _plan_broll(ctx: PipelineContext, duration: float) -> tuple[list, list[str]]:
    """Escolhe os trechos de apoio para as cenas que pediram B-roll.

    Só entra onde a cena tem `broll_prompt` preenchido. Enfiar imagem por conta
    própria em cima do rosto de alguém seria adivinhação cara de desfazer.
    """
    from services.video.broll import index_broll, plan_broll

    if not ctx.settings.broll.enabled:
        return [], []

    cenas = ctx.get("script_scenes", []) or []
    if not any((c.get("broll_prompt") or "").strip() for c in cenas):
        return [], []

    biblioteca = index_broll()
    if not biblioteca:
        return [], [
            "O roteiro pede B-roll, mas a biblioteca está vazia "
            "(data/assets/broll/)."
        ]

    cortes, avisos = plan_broll(
        cenas,
        library=biblioteca,
        duration=duration,
        transcript=ctx.get("transcript"),
    )
    ctx.set("broll_library", biblioteca)
    return cortes, avisos


def _plan_graphics(ctx: PipelineContext) -> None:
    """Planeja os overlays e grava `graphics.ass`.

    Separado do enquadramento porque são interruptores independentes: dá para
    querer barra de progresso sem punch-in, e vice-versa.

    Os textos vêm do roteiro — hook e CTA que o autor escreveu. Inventar texto
    para preencher um card seria pior que não ter card.
    """
    from services.video.graphics import plan_overlays, write_graphics

    project = ctx.project
    if not project.editing.graphics:
        return

    video = ctx.require("lipsync_video")
    duracao = ffmpeg.probe(video).duration

    roteiro: dict[str, Any] = {}
    if project.script_file.exists():
        import json

        try:
            roteiro = json.loads(project.script_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            roteiro = {}

    plano = plan_overlays(
        duracao,
        hook=str(roteiro.get("hook", "")),
        cta=str(roteiro.get("cta", "")),
        handle=ctx.settings.editing.handle,
    )
    caminho = write_graphics(
        plano,
        project.graphics_ass,
        width=project.render.width,
        height=project.render.height,
        preset_key=project.captions.preset,
    )
    ctx.set("graphics_ass", caminho)
    for aviso in plano.warnings:
        ctx.emit(0.5, aviso)
    if caminho:
        ctx.emit(0.6, f"{len(plano.overlays)} gráfico(s) planejado(s).")


def stage_edit(ctx: PipelineContext) -> None:
    """Planeja os enquadramentos e grava a EDL.

    Etapa separada do render de propósito. A EDL é o **plano** e fica em
    `edit.json`, legível e comparável entre execuções; o render só executa. Se
    fosse tudo no render, não daria para ver por que um corte caiu onde caiu.

    Barata (não toca em vídeo), então nunca é reaproveitada por resume: o
    roteiro ou a transcrição podem ter mudado, e replanejar custa milissegundos.
    """
    ctx.check_cancelled()
    project = ctx.project
    from services.video.editor import plan_edit

    ctx.set("edl", None)
    ctx.set("graphics_ass", None)
    _plan_graphics(ctx)

    video = ctx.require("lipsync_video")
    info = ffmpeg.probe(video)
    cortes_broll, avisos_broll = _plan_broll(ctx, info.duration)
    ctx.set("broll_cuts", cortes_broll)
    for aviso in avisos_broll:
        ctx.emit(0.4, aviso)

    if not project.editing.auto_cut:
        if cortes_broll:
            ctx.emit(1.0, f"{len(cortes_broll)} trecho(s) de apoio; enquadramento fixo.")
        else:
            ctx.emit(1.0, "Auto editor desativado; enquadramento fixo.")
        return

    # As palavras enfatizadas vêm do roteiro: são os pontos onde o autor já
    # disse que a frase importa. Melhor sinal que qualquer heurística de áudio.
    emphasis: list[str] = []
    for scene in ctx.get("script_scenes", []) or []:
        emphasis.extend(scene.get("emphasis_words", []) or [])

    edl = plan_edit(
        info.duration,
        transcript=ctx.get("transcript"),
        emphasis=emphasis,
        preset=project.editing.preset,
        min_shot_seconds=ctx.settings.editing.min_shot_seconds,
        max_shot_seconds=ctx.settings.editing.max_shot_seconds,
        punch_in_strength=ctx.settings.editing.punch_in_strength,
    )
    edl.broll = [c.to_dict() for c in cortes_broll]
    edl.save(project.edit_file)
    ctx.set("edl", edl)
    project.record_stage("edit", output=project.edit_file)
    project.save()

    for aviso in edl.warnings:
        ctx.emit(0.9, aviso)
    ctx.emit(
        1.0,
        f"{len(edl.shots)} enquadramento(s) planejado(s)."
        if edl.has_movement
        else "Nenhum movimento aplicável; enquadramento fixo.",
    )


# ---------------------------------------------------------------------------
# 7. Render
# ---------------------------------------------------------------------------


def stage_render(ctx: PipelineContext) -> None:
    ctx.check_cancelled()
    project = ctx.project
    video = ctx.require("lipsync_video")
    audio = ctx.require("voice_audio")
    captions = ctx.get("captions_ass")

    # O B-roll é queimado num passo próprio, antes do render, em vez de virar
    # mais um filtro: `overlay` exige entradas extras e trocaria o `-vf` do
    # render por um `filter_complex` — mudança de risco no caminho que produz o
    # arquivo final. Como passo separado, o artefato fica em disco, validável e
    # inspecionável como o de qualquer outra etapa.
    cortes = ctx.get("broll_cuts") or []
    if cortes:
        from services.video.broll import compose

        composto = project.stages_dir / "broll.mp4"
        origem = ffmpeg.probe(video).video
        try:
            compose(
                video,
                cortes,
                ctx.get("broll_library") or [],
                composto,
                width=(origem.width if origem else project.render.width) or project.render.width,
                height=(origem.height if origem else project.render.height) or project.render.height,
                fps=int(round((origem.fps if origem else 0) or project.render.fps)),
            )
            ffmpeg.validate_video(composto, min_duration=0.5)
            video = composto
            ctx.emit(0.15, f"{len(cortes)} trecho(s) de apoio aplicados.")
        except (ffmpeg.FFmpegError, ffmpeg.ValidationError, ValueError) as exc:
            # Apoio visual é acréscimo; perder o render inteiro por causa dele
            # seria trocar o essencial pelo acessório.
            composto.unlink(missing_ok=True)
            ctx.emit(0.15, f"B-roll não aplicado ({exc}); seguindo sem apoio visual.")

    suffix = "preview" if ctx.preview else "final"
    output = project.renders_dir / f"{project.id}-{suffix}.mp4"
    record_name = f"render_{suffix}"
    started = time.monotonic()

    if (
        "render" not in ctx.force_stages
        and project.stage_completed(record_name)
        and output.exists()
    ):
        ffmpeg.validate_video(output, min_duration=0.5, require_audio=True)
        ctx.set("render_output", output)
        ctx.emit(1.0, f"{suffix.capitalize()} reaproveitado.")
        return

    # As legendas foram dimensionadas com project.render; o render precisa usar
    # exatamente os mesmos valores, senão o texto sai com o tamanho errado.
    video_config = ctx.settings.video.model_copy(
        update={
            "width": project.render.width,
            "height": project.render.height,
            "fps": project.render.fps,
            "video_codec": project.render.codec,
            "audio_sample_rate": project.render.audio_sample_rate,
            "crf": project.render.crf,
            "preset": project.render.preset,
            "loudness_lufs": project.render.loudness_lufs,
            "loudness_true_peak": project.render.loudness_true_peak,
        }
    )

    ctx.emit(0.05, "Normalizando loudness…")
    normalized = project.stages_dir / "voice_normalized.wav"
    try:
        ffmpeg.loudnorm(
            audio,
            normalized,
            lufs=video_config.loudness_lufs,
            true_peak=video_config.loudness_true_peak,
            sample_rate=video_config.audio_sample_rate,
        )
    except ffmpeg.FFmpegError:
        # Loudness é polimento; não vale perder o render por causa disso.
        ctx.emit(0.1, "Normalização de loudness falhou; usando o áudio original.")
        normalized = audio

    # O filtro é montado aqui, e não na etapa de edição, porque só o render
    # sabe se a saída é preview ou final — e o `zoompan` REESCREVE a cadência.
    # Passar o fps do lip-sync (25) num render configurado para 30 derruba o
    # vídeo inteiro para 25 sem nenhum aviso. O preview é o contrário: não força
    # fps nenhum, então tem que herdar o da origem.
    edit_filters: list[str] = []
    edl = ctx.get("edl")
    if edl is not None:
        from services.video.editor import build_zoom_filter

        if ctx.preview:
            origem = ffmpeg.probe(video).video
            alvo_fps = int(round((origem.fps if origem else 0) or project.render.fps))
            largura, altura = ctx.settings.preview.width, ctx.settings.preview.height
        else:
            alvo_fps = project.render.fps
            largura, altura = project.render.width, project.render.height

        zoom = build_zoom_filter(
            edl, width=largura, height=altura, fps=alvo_fps
        )
        # O zoom entra ANTES das legendas: texto que escala junto com a imagem
        # sai da zona segura e fica ilegível no pico do movimento.
        if zoom:
            edit_filters = [zoom]

    # Os gráficos vêm depois do zoom, pelo mesmo motivo, e antes das legendas,
    # que ficam por cima de tudo.
    graphics = ctx.get("graphics_ass")
    if graphics is not None and Path(graphics).exists():
        edit_filters.append(ffmpeg.subtitles_filter(graphics))

    ctx.emit(0.2, f"Renderizando {suffix}…")
    try:
        if ctx.preview:
            ffmpeg.render_preview(
                video,
                normalized,
                output,
                ctx.settings.preview,
                subtitles=captions,
                extra_filters=edit_filters,
            )
        else:
            ffmpeg.render_final(
                video,
                normalized,
                output,
                video_config,
                subtitles=captions,
                extra_filters=edit_filters,
            )
    except ffmpeg.FFmpegError as exc:
        raise StageFailed(
            "render",
            "Falha no render final.",
            hint=exc.hint,
            detail=str(exc),
            command=exc.command,
            exit_code=exc.exit_code,
        ) from exc

    expected = (
        (ctx.settings.preview.width, ctx.settings.preview.height)
        if ctx.preview
        else (video_config.width, video_config.height)
    )
    info = ffmpeg.validate_video(
        output, min_duration=0.5, expected_resolution=expected, require_audio=True
    )

    ctx.set("render_output", output)
    project.outputs[suffix] = str(output.relative_to(project.directory))
    project.record_stage(
        record_name, output=output, duration_seconds=time.monotonic() - started
    )
    project.save()
    ctx.emit(
        1.0,
        f"{output.name} · {info.resolution[0]}x{info.resolution[1]} · "
        f"{info.duration:.1f}s · {info.size_bytes / 1e6:.1f} MB",
    )


# ---------------------------------------------------------------------------
# 7. Thumbnail
# ---------------------------------------------------------------------------


def stage_thumbnail(ctx: PipelineContext) -> None:
    ctx.check_cancelled()
    render = ctx.get("render_output")
    if render is None:
        return
    output = ctx.project.directory / "thumbnail.jpg"
    try:
        from services.video.thumbnail import generate_thumbnail

        generate_thumbnail(render, output)
        ctx.project.outputs["thumbnail"] = "thumbnail.jpg"
        ctx.project.save()
        ctx.emit(1.0, "Thumbnail gerada.")
    except Exception as exc:  # noqa: BLE001 - thumbnail nunca derruba o render
        ctx.emit(1.0, f"Thumbnail não gerada: {exc}")


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------

PRODUCTION_STAGES: list[Stage] = [
    Stage("prepare", "Preparando", stage_prepare, weight=0.5),
    Stage("tts", "Gerando voz", stage_tts, weight=8.0),
    Stage("template", "Montando base", stage_template, weight=2.0),
    Stage("lipsync", "Sincronizando lábios", stage_lipsync, weight=12.0),
    Stage("captions", "Legendando", stage_captions, weight=3.0),
    Stage("edit", "Editando", stage_edit, weight=0.3),
    Stage("render", "Renderizando", stage_render, weight=3.0),
    Stage("thumbnail", "Thumbnail", stage_thumbnail, weight=0.5, optional=True),
]

STAGE_ORDER = [s.name for s in PRODUCTION_STAGES]
