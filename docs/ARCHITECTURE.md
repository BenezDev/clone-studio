# Arquitetura

## O problema central

Um estúdio de vídeo com IA local precisa orquestrar quatro modelos que não
convivem no mesmo interpretador Python, num hardware que pode ir de um notebook
sem GPU a uma workstation com 32 GB de VRAM, sem que nenhum arquivo pessoal
saia da máquina.

Quase todas as decisões deste projeto saem daí.

---

## Camadas

```
┌──────────────────────────────────────────────────────────┐
│  apps/web (React)          apps/cli (Typer)              │
└──────────────┬───────────────────────┬───────────────────┘
               │  HTTP                 │  import direto
┌──────────────▼───────────────────────▼───────────────────┐
│  apps/api (FastAPI)  — apresentação, sem regra de negócio│
└──────────────────────────┬───────────────────────────────┘
┌──────────────────────────▼───────────────────────────────┐
│  core/  — domínio                                        │
│  pipeline · jobs · storage · media · licensing · hardware│
└──────────────────────────┬───────────────────────────────┘
┌──────────────────────────▼───────────────────────────────┐
│  services/  — adapters de engine (interfaces abstratas)  │
│  tts · lipsync · transcription · llm · templates · video │
└──────────────────────────┬───────────────────────────────┘
                           │  subprocesso + JSON Lines
┌──────────────────────────▼───────────────────────────────┐
│  .envs/<engine>/  — ambientes virtuais isolados          │
│  qwen-tts · musetalk · whisper                           │
└──────────────────────────────────────────────────────────┘
```

**Regra de dependência:** as setas só apontam para baixo. `core/` não sabe que
existe FastAPI; `services/` não sabe que existe um pipeline; a CLI e a API são
duas apresentações da mesma camada de domínio.

---

## Por que ambientes separados

Não é preferência de organização — é a única forma de o projeto instalar.

| Ambiente | Python | Restrição real |
|---|---|---|
| `backend` | 3.12 | nenhuma biblioteca de ML, de propósito |
| `qwen-tts` | 3.12 | `qwen-tts` fixa `transformers==4.57.3` |
| `whisper` | 3.12 | CTranslate2, sem torch |
| `musetalk` | **3.10** | `torch==2.0.1` e `numpy==1.23.5` não têm wheel para 3.12; `mmcv==2.0.1` só tem wheel para cp310/cp311 |

O `uv` provisiona o CPython 3.10 sem tocar no Python do sistema.

---

## Protocolo de workers

`core/worker/protocol.py` é **stdlib-only**. Ele é importado dentro de cada
ambiente isolado, e esses ambientes têm árvores de dependências propositalmente
incompatíveis entre si — qualquer import externo ali quebraria alguma engine.

**Host → worker:** um arquivo JSON, cujo caminho vai por `argv`. Fica gravado em
`logs/`, então qualquer execução é reproduzível manualmente:

```bash
.envs/qwen-tts/bin/python -m services.tts.worker.qwen_worker \
  logs/tts-20260811-190000-abc123.request.json
```

**Worker → host:** JSON Lines por stdout.

```json
{"event":"progress","pct":0.42,"message":"Sintetizando bloco 2/5"}
{"event":"log","level":"info","message":"modelo carregado"}
{"event":"result","ok":true,"data":{"variants":[...]}}
```

`stderr` fica livre para tracebacks e ruído de bibliotecas; o host guarda tudo
em `logs/<stage>-<timestamp>-<id>.log`.

**Erros** nunca são só "falhou". `WorkerError` carrega estágio, mensagem,
dica de solução, comando exato, exit code, últimas linhas do stderr e o caminho
do log completo. Quando o worker não consegue classificar o erro, o host tenta
inferir a dica a partir de padrões conhecidos no stderr (`CUDA out of memory`,
`Killed`, `No module named`, …).

---

## Pipeline

```
prepare → tts → template → lipsync → captions → render → thumbnail
```

Cada etapa:

1. checa cancelamento antes de começar;
2. pula a si mesma se já concluída **e** o artefato ainda existe;
3. grava o resultado;
4. **valida o próprio arquivo de saída com ffprobe** antes de declarar sucesso.

O passo 4 é o que impede o erro clássico de uma etapa "passar" e o problema só
aparecer no render final, meia hora depois.

### Resume

`project.json` guarda um `StageRecord` por etapa concluída. Duas travas:

- uma etapa só conta como concluída se o arquivo dela ainda existe no disco;
- forçar uma etapa invalida **todas as posteriores** — reaproveitar um lip-sync
  feito com outro áudio produziria um vídeo dessincronizado.

### Progresso

Cada etapa tem um peso. O progresso local vira uma fração global ponderada, o
que evita a barra que fica 90 % do tempo parada em "renderizando".

```python
Stage("tts", "Gerando voz", stage_tts, weight=8.0)
Stage("lipsync", "Sincronizando lábios", stage_lipsync, weight=12.0)
```

---

## Production Mode

O modo principal **não** gera corpo e rosto por diffusion. Isso preserva melhor
a identidade e reduz o custo computacional em ordens de grandeza.

### Seleção de template

O tom do roteiro vira critério:

| Roteiro | energia | gestos |
|---|---|---|
| agressivo, urgente | high | high |
| explicativo | medium | medium |
| sério, calmo | low | low |

A pontuação combina adequação, orientação (vertical pesa forte — recortar
horizontal para 9:16 costuma decapitar o rosto) e cobertura da duração.

### Composição por duração

Três estratégias, em ordem de preferência:

1. **`single`** — um template cobre tudo. Melhor resultado, sem emendas. O
   trecho é tirado do miolo, onde a fala é mais estável.
2. **`multi`** — encadeia templates diferentes. O corte fica disfarçado porque
   o enquadramento muda junto.
3. **`revisit`** — reaproveita trechos distintos do mesmo template, começando
   de pontos diferentes a cada revisita. Avisa explicitamente que falta
   material.

As pontas de cada trecho são aparadas em 0,35 s, porque é ali que ficam os
piscares e gestos interrompidos que denunciam a emenda.

O vídeo **nunca** é esticado.

---

## Cache

Chave derivada do conteúdo dos inputs, nunca de data — assim o cache sobrevive
a copiar o projeto ou reinstalar a aplicação.

| Namespace | O que guarda | Por que compensa |
|---|---|---|
| `templates` | landmarks + latentes do VAE | ~5 s por frame em CPU, e independe do áudio |
| `tts` | variantes de áudio | ~8× tempo real em CPU |
| `transcripts` | transcrições com timestamps | evita reprocessar no ajuste de legenda |
| `lipsync` | diretórios de trabalho | permite inspecionar frames |

Arquivos grandes usam hash parcial (início, meio, fim + tamanho): identifica
mídia sem varrer gigabytes a cada render.

---

## Gestão de memória

Em CPU_ONLY os modelos vivem na RAM, e 15 GB acabam rápido. O pipeline carrega
uma engine, gera, e **encerra o processo** — o sistema recupera a memória sem
depender de coleta de lixo.

Dentro do worker do MuseTalk, o Whisper é descarregado logo após extrair as
features, e a UNet/VAE são liberadas antes da etapa de composição, que é
puramente de imagem.

O preprocessamento de landmarks roda em lotes de 16 frames: o código upstream
carregaria todos os frames na RAM de uma vez, o que passaria de 3 GB num vídeo
de um minuto.

---

## Licenciamento

`config/model_registry.yaml` é a fonte da verdade. Cada entrada registra fonte
oficial, revisão fixada, tamanho, licença, data de verificação e exigências de
hardware.

`commercial_allowed` tem três estados, não dois:

- `true` — a licença do peso libera uso comercial;
- `false` — proíbe;
- `check_model_license` — depende do peso específico e exige verificação manual.

Um modelo só é **recomendado** em modo comercial quando ele e **todas as suas
dependências** estão em `true`. É por isso que o MuseTalk (MIT, modelo liberado
para qualquer finalidade) não aparece como recomendado comercialmente: ele
depende do DWPose, cujos checkpoints herdam restrições dos datasets de treino.

---

## Privacidade

Defesa em camadas:

1. **Esquema.** `PrivacyConfig` recusa `telemetry: true`; `AppConfig` recusa
   qualquer host fora do loopback sem dupla confirmação.
2. **Middleware.** O backend responde 403 a qualquer cliente que não seja
   `127.0.0.1`, mesmo que a porta seja exposta por engano.
3. **Servidor de arquivos.** `/api/media/file` só entrega arquivos com extensão
   permitida e dentro de diretórios permitidos, com o caminho resolvido — sem
   isso, um parâmetro `path` viraria leitura arbitrária do disco.
4. **Ambiente dos subprocessos.** `HF_HOME` e `TORCH_HOME` apontam para dentro
   do projeto; `HF_HUB_DISABLE_TELEMETRY=1` e `DO_NOT_TRACK=1` são impostos.
5. **`.gitignore`.** `data/`, `projects/`, `exports/`, `models/`, `logs/` e
   `config/local.yaml` ficam fora do Git.

---

## Escolhas de mídia

**FFmpeg** é a engine de mídia: encode, decode, concat, crop, scale, áudio,
loudness, legendas, thumbnails, proxies e cortes. MoviePy não é usado — ele
decodifica em Python e é ordens de grandeza mais lento.

**Legendas em ASS**, queimadas no vídeo. SRT não tem posicionamento, contorno,
sombra nem destaque por palavra, e legendas automáticas de plataforma não são
confiáveis nem controláveis. O SRT é gerado como arquivo auxiliar.

O destaque palavra a palavra usa um evento `Dialogue` por estado, em vez de
tags de karaokê — que renderizam de forma inconsistente entre players.

**Loudness** em dois passos (medir, depois aplicar), alvo −14 LUFS / −1,5 dBTP,
que é o padrão de redes sociais.

**Preview** em 540×960, CRF 30, limitado aos primeiros 10 segundos. Existe para
descobrir o erro antes de esperar o render inteiro.

---

## O que ainda não existe

- **Auto editor** (FASE 9): a `EditDecisionList` está desenhada, mas cortes,
  punch-ins e inserção automática de B-roll ainda não estão implementados.
- **Remotion** (FASE 10): animações gráficas, lower thirds, callouts.
- **ComfyUI / Wan2.2** (FASE 11): o adapter `GenerativeVideoEngine` está
  previsto; nesta máquina o módulo está desativado por falta de GPU.
- **Avatar generativo** (futuro): foto + áudio → movimento completo. Não bloqueia
  o MVP e depende de verificação de licença por engine.
- **Busca semântica de B-roll**: hoje a referência é manual, por cena.

Nenhum desses itens tem mock na implementação final. Onde a funcionalidade não
existe, ela é declarada como ausente — na interface e nesta documentação.
