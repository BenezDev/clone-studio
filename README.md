# Local Clone Studio

Estúdio **local** para produzir vídeos verticais (TikTok / Reels / Shorts) com
a sua própria voz e o seu próprio rosto.

```
ideia → roteiro → voz clonada → vídeo seu → lip-sync → legendas → render 9:16
```

Saída: **MP4 1080×1920, H.264/AAC**, pronto para publicar.

Depois que os modelos são baixados, o pipeline principal funciona **offline**.
Nenhum rosto, voz, vídeo ou roteiro é enviado para qualquer serviço externo.

---

## Estado atual

| Fase | Escopo | Situação |
|---|---|---|
| 1 | Estrutura, detecção de hardware, config, licenças, installer, diagnóstico | **pronto** |
| 2 | Qwen3-TTS — clonagem de voz | **pronto** |
| 3 | MuseTalk 1.5 — lip-sync | **pronto** |
| 4 | Pipeline voz + vídeo, jobs, resume | **pronto** |
| 5 | faster-whisper + legendas ASS | **pronto** |
| 6 | Render FFmpeg 9:16 | **pronto** |
| 7 | Interface React | **pronto** |
| 8 | Ollama — roteiros | **pronto** |
| 9 | Auto editor (EDL, cortes de enquadramento, zooms) | **pronto** |
| 10 | Remotion — animações gráficas | pendente |
| 11 | ComfyUI / Wan2.2 — B-roll generativo | adapter pronto, opt-in |
| 12 | Polimento e performance | em andamento |

---

## Instalação

Suportado em **Linux Mint/Ubuntu** e **Windows 10/11 x64**. Os dados e pesos
continuam dentro da pasta do projeto nas duas plataformas.

### Linux

```bash
./install.sh
```

O instalador:

1. detecta sistema, CPU, RAM, GPU, disco e ferramentas;
2. verifica Python, Node, pnpm, FFmpeg e Git — e **mostra** o comando `sudo`
   necessário em vez de executá-lo por conta própria;
3. cria um ambiente virtual **por engine**;
4. instala backend, frontend e engines;
5. **pergunta** antes de baixar qualquer modelo grande;
6. roda o diagnóstico no final.

O lip-sync é opcional na instalação padrão (precisa de Python 3.10 isolado e
~2 GB de dependências):

```bash
./install.sh --with-musetalk
```

Reinstalar um ambiente só:

```bash
./install.sh --only qwen-tts
```

### Windows (PowerShell)

Pré-requisitos no `PATH`: Python 3.11+, Git, FFmpeg e Node 20+. Depois:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
.\start.ps1
```

Para incluir lip-sync nativo, instale também o `uv` (`winget install
astral-sh.uv`) e rode:

```powershell
.\install.ps1 -WithMuseTalk
.\scripts\models.ps1 install --essential
```

O stack legado MMLab do MuseTalk é a parte mais sensível no Windows. Se o
wheel nativo do `mmcv` não estiver disponível para a combinação instalada,
use WSL2/Ubuntu para essa engine; backend, UI, TTS, Whisper e FFmpeg têm
launchers nativos. O diagnóstico mostra exatamente qual componente faltou.

### Modelos

```bash
./scripts/models.sh list                  # catálogo com tamanho, licença e compatibilidade
./scripts/models.sh install --essential   # conjunto mínimo para o seu hardware
./scripts/models.sh install musetalk_15   # lip-sync (~4,4 GB)
./scripts/models.sh status                # o que já está no disco
```

Nada é baixado sem comando explícito.

---

## Uso

### Como aplicativo (recomendado)

```bash
./scripts/install-desktop-entry.sh
```

Depois disso o **Local Clone Studio** aparece no menu de aplicativos e na área
de trabalho. Um clique sobe tudo e abre a interface — sem terminal.

- clicar de novo com o estúdio já aberto apenas reabre a aba;
- botão direito no ícone → **Parar** ou **Diagnóstico**;
- para remover: `./scripts/install-desktop-entry.sh --uninstall`.

Nada é instalado fora de `~/.local`, e nenhum comando pede `sudo`.

### Pelo terminal

```bash
./start.sh
```

Abra <http://127.0.0.1:3000>.

```bash
./stop.sh    # encerra tudo
./dev.sh     # hot reload no backend e no frontend
```

No Windows, use `.\start.ps1`, `.\start.ps1 -Dev` e `.\stop.ps1`.

### Primeiro vídeo

1. **Voice** — cadastre sua voz: 5 a 30 segundos limpos + a transcrição exata
   do que é falado. Grave direto pelo microfone, na própria página, ou envie um
   arquivo que você já tenha.
2. **Templates** — junte de 10 a 30 vídeos verticais seus: olhando para a
   câmera, em pé, sentado, gesticulando, estilo podcast, estilo selfie,
   enquadramentos e roupas diferentes. Grave pela câmera na própria página ou
   envie os arquivos.
3. **New Video** — digite a ideia, ajuste o roteiro, escolha o preset de
   legenda, gere o preview e mande o render final.

### CLI

A CLI e a interface chamam a mesma camada de domínio — não há lógica duplicada.

```bash
./scripts/clone-studio doctor                 # diagnóstico completo
./scripts/clone-studio test voice             # testa uma engine específica
./scripts/clone-studio voice-enroll --audio amostra.wav --text "..." --id me
./scripts/clone-studio voice-list
./scripts/clone-studio voice-test --id me --variants 3
./scripts/clone-studio models list
./scripts/clone-studio config
```

---

## Arquitetura

### Ambientes isolados

Cada engine de ML roda no seu próprio ambiente virtual, com versões
incompatíveis entre si:

```
.envs/
  backend/    Python 3.12 · FastAPI, sem nenhuma biblioteca de ML
  qwen-tts/   Python 3.12 · torch 2.9 + transformers 4.57
  whisper/    Python 3.12 · CTranslate2 (sem torch)
  musetalk/   Python 3.10 · torch 2.0.1 + mmcv 2.0.1 + mmpose
```

Isso não é preferência de estilo: o `qwen-tts` **fixa** `transformers==4.57.3`,
enquanto o MuseTalk exige `transformers==4.39.2`, `numpy==1.23.5` e
`torch==2.0.1` — que não têm wheels para Python 3.12. Num único ambiente, o
projeto simplesmente não instala.

### Workers em subprocesso

O backend **nunca importa torch**. Cada etapa pesada roda como um subprocesso
curto que fala JSON Lines por stdout:

```
backend (sem torch)
   └─ subprocesso: .envs/<engine>/(bin|Scripts)/python -m <worker> request.json
        ├── {"event":"progress", "pct":0.42, "message":"..."}
        ├── {"event":"log", ...}
        └── {"event":"result", "ok":true, "data":{...}}
```

Quatro problemas resolvidos de uma vez:

- **isolamento** de dependências;
- **memória** devolvida ao sistema quando o processo termina — sem depender de
  `del model` e coleta de lixo;
- **API instantânea**: o FastAPI sobe em menos de um segundo;
- **erros observáveis**: comando exato, exit code, stderr e provável solução.

### Production Mode

O modo principal **não** gera seu corpo por diffusion. Ele usa vídeos reais
seus como matéria-prima:

1. escolhe o template pelo tom do roteiro (energia, gestos, enquadramento);
2. gera o áudio com o seu clone de voz;
3. compõe vários trechos se o áudio for mais longo que o vídeo — cortando em
   pontos naturais e evitando repetição perceptível, sem esticar o vídeo;
4. aplica lip-sync só na região necessária, preservando rosto, barba, cabelo,
   pele, iluminação, corpo, fundo e gestos;
5. legenda, renderiza e valida.

### Auto editor

Opcional, desligado por padrão. Ele **não corta tempo**: o vídeo está
lip-sincronizado quadro a quadro com o áudio, e remover trechos dessincronizaria
a boca da fala. O que ele corta é *enquadramento* — fecha no rosto quando a
frase importa e abre quando ela acaba, que é o que um editor faz com uma câmera
só. A duração fica intacta e a sincronia não é tocada.

O plano sai como dado, em `edit.json`: cada enquadramento com início, fim, zoom
e o motivo do corte. Dá para ler e comparar entre execuções antes de renderizar.
As legendas ficam fixas no quadro — só a imagem se move.

### Resume

Cada etapa grava seu artefato e o registra em `project.json`. Se o TTS terminou
e o lip-sync falhou, a próxima execução reaproveita o áudio. Uma etapa só conta
como concluída se o arquivo dela **ainda existe** — e forçar uma etapa invalida
todas as posteriores, para nunca misturar áudio novo com lip-sync antigo.

### Cache

Cacheado por hash de conteúdo dos inputs, não por data:

- preprocessamento de template do MuseTalk (landmarks + latentes do VAE) — o
  passo mais caro, e o que mais compensa reaproveitar entre vídeos;
- síntese de voz;
- transcrições;
- prompts de voz.

---

## Estrutura

```
apps/
  api/          backend FastAPI
  web/          interface React + Vite + TypeScript
  cli/          CLI (mesma camada de domínio da API)
core/
  config/       esquema e carregamento (default.yaml + local.yaml)
  hardware/     detecção e perfis (só stdlib — roda antes de qualquer venv)
  licensing/    registro de modelos e gestor de licenças
  storage/      caminhos, cache, formato de projeto
  jobs/         fila em SQLite
  pipeline/     etapas, contexto e executor
  media/        FFmpeg e validação
  audio/        normalização de texto para fala
  worker/       protocolo host↔subprocesso
  diagnostics/  verificações compartilhadas por CLI e UI
services/
  tts/          interface + adapter Qwen3-TTS + worker
  lipsync/      interface + adapter MuseTalk + worker
  transcription/interface + adapter faster-whisper + worker
  llm/          adapter Ollama + script engine
  templates/    biblioteca, seleção e composição
  video/        legendas ASS/SRT, thumbnail
config/         default.yaml, model_registry.yaml
requirements/   um arquivo por ambiente
data/           SEUS arquivos (fora do Git)
models/         pesos baixados
projects/       um diretório por vídeo
exports/        saídas
tests/          unit, integration, smoke
```

---

## Privacidade

- Backend em `127.0.0.1`. Bind externo exige **duas** confirmações explícitas
  (config + variável de ambiente) e o esquema recusa qualquer outro valor.
- O backend recusa requisições que não venham deste computador, mesmo se a
  porta for exposta por engano.
- Telemetria, analytics e envio de mídia são bloqueados **pelo esquema de
  configuração** — não é possível ligá-los editando um YAML.
- `HF_HOME` e `TORCH_HOME` apontam para dentro do projeto: nenhuma biblioteca
  grava pesos em `~/.cache` sem que você saiba.
- A gravação de voz e de vídeo roda dentro do navegador, contra o backend
  local. Câmera e microfone só abrem quando você clica, e são fechados ao sair
  da tela.
- Todo o diretório `data/` está no `.gitignore`.
- A única saída de rede do projeto é o download de pesos, sempre sob comando
  explícito.

---

## Licenças dos modelos

`config/model_registry.yaml` registra, para cada modelo: fonte oficial, versão
fixada, tamanho, licença, data em que ela foi verificada e o que ela permite.

Nada é assumido em silêncio. Em **modo comercial**, um modelo só aparece como
recomendado quando a licença dele **e a de todas as suas dependências** permite
uso comercial.

Exemplo real: o MuseTalk é MIT e o modelo treinado é declarado como disponível
para qualquer finalidade, inclusive comercial — mas ele depende do DWPose, cujos
checkpoints herdam restrições dos datasets de treino. Por isso ele **não**
aparece como recomendado em modo comercial até que você verifique esse ponto.

Isto é orientação técnica a partir do que está registrado, não aconselhamento
jurídico.

---

## Segurança de identidade

Este software existe para produzir conteúdo audiovisual com a **sua própria**
aparência e a **sua própria** voz.

Ele não deve ser usado para autenticação, nem para contornar verificação de
identidade, KYC, reconhecimento facial, autenticação por voz ou sistemas
antifraude.

---

## Testes

```bash
.envs/backend/bin/python -m pytest tests/unit         # rápidos, sem modelos
.envs/backend/bin/python -m pytest tests/integration  # exigem FFmpeg
.envs/backend/bin/python -m pytest tests/smoke        # caminho completo
```

No Windows, troque o executável por `.envs\backend\Scripts\python.exe`.
Os testes que precisam de pesos são pulados com uma mensagem
dizendo exatamente o que falta instalar.

---

## Desempenho

Os números abaixo são medidos em **CPU_ONLY** (Intel i7-1355U, 15 GB de RAM,
sem GPU dedicada):

| Etapa | Custo |
|---|---|
| TTS (Qwen3-TTS 0.6B) | ~8 s de processamento por segundo de áudio |
| Transcrição (faster-whisper small, int8) | ~1× tempo real |
| Lip-sync — preprocessamento do template | ~5 s por frame, **uma vez por template** |
| Lip-sync — inferência | vários segundos por frame |
| Render FFmpeg | segundos |

O preprocessamento de template é cacheado: o primeiro vídeo com um template
novo é caro, os seguintes com o mesmo template pulam essa etapa por completo.

Com GPU NVIDIA, o instalador troca automaticamente para as wheels CUDA e o
perfil de hardware passa a escolher os modelos maiores.

---

## Documentação

- [`COMO-RODAR.md`](COMO-RODAR.md) — manual do zero ao primeiro vídeo
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — decisões técnicas em detalhe
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — registro de decisões e por quê
- [`docs/WINDOWS.md`](docs/WINDOWS.md) — guia detalhado do Windows
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) — problemas conhecidos

---

## Licença

Software proprietário. Copyright © 2026 João Victor Kattwinkel. Todos os
direitos reservados.

Este projeto **não** é open source e o acesso a este repositório não concede
licença de uso. Os termos completos estão em [`LICENSE`](LICENSE). Para
licenciamento, entre em contato.

As licenças dos modelos e bibliotecas de terceiros são independentes desta e
estão registradas em [`config/model_registry.yaml`](config/model_registry.yaml).
