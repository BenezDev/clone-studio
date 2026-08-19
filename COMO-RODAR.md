# COMO RODAR

Guia único do **Local Clone Studio** — Linux e Windows, do zero ao primeiro
vídeo. Todos os comandos abaixo foram conferidos contra o projeto real.

---

## Índice

1. [O que este programa faz](#1-o-que-este-programa-faz)
2. [Como funciona por dentro](#2-como-funciona-por-dentro)
3. [O que a máquina precisa](#3-o-que-a-máquina-precisa)
4. [Instalar no Linux](#4-instalar-no-linux)
5. [Instalar no Windows](#5-instalar-no-windows)
6. [Ligar e desligar](#6-ligar-e-desligar)
7. [Fazer o primeiro vídeo](#7-fazer-o-primeiro-vídeo)
8. [Todos os comandos](#8-todos-os-comandos)
9. [Quanto tempo cada coisa leva](#9-quanto-tempo-cada-coisa-leva)
10. [Quando der errado](#10-quando-der-errado)
11. [Privacidade](#11-privacidade)

---

## 1. O que este programa faz

Você digita uma ideia. Ele devolve um vídeo vertical pronto para TikTok, Reels
e Shorts — **com a sua voz e o seu rosto**.

```
ideia → roteiro → voz clonada → vídeo seu → lip-sync → legendas → MP4 9:16
```

Saída: **MP4 1080×1920, H.264/AAC**.

Tudo roda **na sua máquina**. Depois que os modelos são baixados, funciona sem
internet. Nenhum arquivo seu é enviado para lugar nenhum.

### O que ele NÃO faz

- Não gera seu corpo por inteligência artificial. Ele usa **vídeos reais seus**
  como base e troca só a região da boca. É por isso que você continua parecendo
  você.
- Não publica nada automaticamente. O arquivo fica no seu disco.
- Não serve para burlar reconhecimento facial, KYC ou autenticação por voz.

---

## 2. Como funciona por dentro

### As duas coisas que você precisa cadastrar

| O quê | Quanto | Onde fica |
|---|---|---|
| **Sua voz** | 1 áudio de 5 a 30 s, limpo, + a transcrição exata | `data/identity/voice/` |
| **Seus vídeos** | 10 a 30 vídeos verticais seus | `data/identity/templates/` |

Os dois podem ser **gravados dentro do app**, pelo microfone e pela câmera, ou
enviados como arquivo se você já tiver o material.

Os vídeos são a matéria-prima. Grave você olhando para a câmera: em pé,
sentado, gesticulando, estilo podcast, estilo selfie, roupas e enquadramentos
diferentes. Quanto mais material, menos repetição perceptível.

### O caminho de um vídeo

1. **Roteiro** — você escreve, ou o Ollama gera (opcional) e você edita.
2. **Voz** — o Qwen3-TTS lê o roteiro com a sua voz clonada.
3. **Template** — o sistema escolhe qual vídeo seu combina com o tom do
   roteiro, e junta vários trechos se o áudio for mais longo que o vídeo.
4. **Lip-sync** — o MuseTalk faz a boca acompanhar o áudio.
5. **Legendas** — o faster-whisper transcreve e gera legenda palavra a palavra.
6. **Render** — o FFmpeg monta o MP4 final em 1080×1920.

Cada etapa é validada antes da seguinte. Se uma falhar, as anteriores ficam
salvas: rodar de novo **reaproveita** o que já deu certo.

### Por que tem quatro "ambientes Python"

Os modelos exigem versões incompatíveis entre si — o de voz precisa de uma
versão de biblioteca que quebra o de lip-sync. Cada um roda no seu próprio
ambiente isolado. Você não precisa se preocupar com isso; o instalador cuida.

---

## 3. O que a máquina precisa

| Item | Mínimo | Bom |
|---|---|---|
| Disco livre | 15 GB | 25 GB |
| RAM | 8 GB | 16 GB |
| Internet | só para instalar | — |
| GPU NVIDIA | não obrigatória | **muda tudo** |

**Sobre a GPU:** sem ela funciona, mas o lip-sync fica muito lento (28 minutos
para 7 segundos de vídeo). Voz e legendas são utilizáveis sem GPU.

### Programas necessários

Python 3.11+, Node 20+, FFmpeg e Git. O instalador confere e diz o que falta.

---

## 4. Instalar no Linux

Testado em **Linux Mint 22.3 / Ubuntu 24.04**.

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg git curl
```

Node 20+ (se ainda não tiver):

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm install -g pnpm
```

Agora o projeto:

```bash
cd ~/"Área de trabalho/Clone Studio"
chmod +x install.sh start.sh stop.sh scripts/*.sh scripts/clone-studio
./install.sh
```

Ele pergunta antes de baixar qualquer modelo. Leva de 15 a 40 minutos.

### Instalar como aplicativo (recomendado)

```bash
./scripts/install-desktop-entry.sh
```

Cria um ícone no menu e na área de trabalho. Um clique liga tudo e abre o
navegador — sem terminal. Botão direito no ícone → **Parar** ou
**Diagnóstico**.

> O boot leva de 30 a 60 segundos e só mostra uma notificação discreta.
> Clique **uma vez** e espere.

Para remover o ícone: `./scripts/install-desktop-entry.sh --uninstall`

---

## 5. Instalar no Windows

> **Aviso honesto:** os scripts do Windows foram escritos e revisados, mas
> nunca foram executados num Windows real. Espere encontrar algum problema na
> primeira vez.

### 5.1 Pré-requisitos

PowerShell **como Administrador**:

```powershell
winget install --id Python.Python.3.12 -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id Gyan.FFmpeg -e
winget install --id Git.Git -e
```

**Feche o PowerShell e abra outro** (sem administrador). Confira:

```powershell
python --version
node --version
ffmpeg -version
git --version
```

Os quatro precisam responder. Se algum falhar, reinicie o computador.

### 5.2 Colocar o projeto no lugar certo

Descompacte em um caminho **curto e sem acento**:

```
C:\CloneStudio
```

> Evite Área de Trabalho, Documentos e OneDrive. Isso elimina uma classe
> inteira de problemas do Windows.

### 5.3 Instalar

```powershell
cd C:\CloneStudio
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

O `-ExecutionPolicy Bypass` é necessário porque o Windows bloqueia scripts não
assinados. Vale só para esta execução — não muda a configuração da máquina.

### 5.4 No primeiro dia, pule o lip-sync

É a parte mais frágil no Windows e a mais lenta. Instale só voz e legendas:

```powershell
.\scripts\models.ps1 install --essential
.\start.ps1
```

O vídeo sai sem sincronia labial, com aviso — em vez de falhar.

Depois, com calma:

```powershell
winget install --id astral-sh.uv -e
# feche e abra o PowerShell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Only musetalk
.\scripts\models.ps1 install musetalk_15
.\scripts\clone-studio.ps1 test lipsync
```

---

## 6. Ligar e desligar

| | Linux | Windows |
|---|---|---|
| **Ligar** | `./start.sh` | `.\start.ps1` |
| **Desligar** | `./stop.sh` | `.\stop.ps1` |
| **Modo dev** | `./dev.sh` | `.\start.ps1 -Dev` |
| **Só a API** | `./start.sh --api-only` | `.\start.ps1 -ApiOnly` |

Depois de ligar, abra:

```
http://127.0.0.1:3000
```

Documentação da API (se precisar): <http://127.0.0.1:8756/api/docs>

---

## 7. Fazer o primeiro vídeo

### Passo 1 — cadastre sua voz

Grave 10 a 30 segundos falando naturalmente. **Voz limpa**: sem música, sem
eco, sem ruído. Um celular perto da boca num quarto com cortina rende mais que
um microfone caro numa sala vazia.

Pela interface: página **Voice** → aba **Gravar agora**, ligue o microfone,
grave, ouça, e escreva a **transcrição exata** do que você falou. A barra de
nível mostra na hora se está baixo demais ou estourando — os dois estragam a
clonagem, e só ouvindo depois você não perceberia.

Se preferir usar um áudio que já tem, a aba **Enviar arquivo** continua ali.

Ou pelo terminal:

```bash
# Linux
./scripts/clone-studio voice-enroll --audio ~/minha-voz.wav \
  --text "escreva aqui exatamente o que você falou no áudio" \
  --id me --name "Minha voz"
```

```powershell
# Windows
.\scripts\clone-studio.ps1 voice-enroll --audio C:\minha-voz.wav `
  --text "escreva aqui exatamente o que você falou no áudio" `
  --id me --name "Minha voz"
```

> **A transcrição exata é o que mais afeta a qualidade.** Sem ela o modelo usa
> um modo degradado e a voz fica ruim.

Teste:

```bash
./scripts/clone-studio voice-test --id me --variants 3
```

Gera 3 versões para você escolher a melhor.

### Passo 2 — adicione seus vídeos

Pela interface: página **Templates** → aba **Gravar agora**. O preview mostra o
corte 9:16 real com as zonas seguras por cima, então você se enquadra no quadro
que vai existir de verdade — e não naquele que a webcam mostra.

A webcam do notebook resolve para começar, mas a câmera do seu celular é melhor
para o material definitivo. Nesse caso, grave no celular e use a aba **Enviar
arquivo**.

Ou copie os vídeos direto para `data/identity/templates/` e rode:

```bash
./scripts/clone-studio templates --reindex
```

### Passo 3 — gere o vídeo

Pela interface (**New Video**), ou pelo terminal:

```bash
./scripts/clone-studio create "por que programadores deveriam aprender vendas"
./scripts/clone-studio projects          # veja o id gerado
./scripts/clone-studio preview 2026-08-12-por-que-programadores
./scripts/clone-studio render 2026-08-12-por-que-programadores
```

**Sempre faça o preview antes.** Ele gera 540×960 com os primeiros 10 segundos
— serve para descobrir erro sem esperar o render inteiro.

### Passo 4 — acabamento automático (opcional)

Três recursos que ligam na página **New Video**, no passo *Edição*. Todos
desligados por padrão, e nenhum deles mexe na sincronia labial: o vídeo está
sincronizado quadro a quadro com o áudio, então nada remove tempo.

| Recurso | O que faz | De onde vem |
|---|---|---|
| **Auto editor** | Fecha o enquadramento no rosto na frase que importa e abre quando ela acaba | Frases da transcrição e palavras enfatizadas do roteiro |
| **Animações gráficas** | Card de hook na abertura, barra de progresso e end card no fim | O hook e o CTA que você escreveu no roteiro |
| **B-roll** | Cobre a imagem por alguns segundos com um trecho de apoio | Sua biblioteca em `data/assets/broll/` |

O plano de edição sai em `edit.json`, dentro da pasta do projeto: dá para abrir
e conferir onde cada corte caiu e por quê, antes ou depois de renderizar.

Para o B-roll funcionar, duas coisas: arquivos com **nomes descritivos** na
biblioteca (`bitcoin_grafico_queda.mp4`, não `IMG_0421.mp4`), e o campo de
B-roll preenchido na cena do roteiro. A busca casa as palavras do pedido com as
do nome do arquivo. Sem pedido explícito, nada é inserido.

Seu @ do lower third fica em `config/local.yaml`:

```yaml
editing:
  handle: "@seu_usuario"
```

---

## 8. Todos os comandos

Troque `./scripts/clone-studio` por `.\scripts\clone-studio.ps1` no Windows.

### Diagnóstico

```bash
./scripts/doctor.sh                    # estado completo do sistema
./scripts/clone-studio test ffmpeg     # testa o FFmpeg
./scripts/clone-studio test voice      # testa a engine de voz
./scripts/clone-studio test captions   # testa as legendas
./scripts/clone-studio test lipsync    # testa o lip-sync
./scripts/clone-studio test ollama     # testa o gerador de roteiros
./scripts/clone-studio test comfyui    # testa o B-roll generativo
```

### Modelos

```bash
./scripts/models.sh list                        # catálogo com tamanho e licença
./scripts/models.sh status                      # o que já está instalado
./scripts/models.sh install --essential         # mínimo para o seu hardware
./scripts/models.sh install qwen3_tts_1_7b_base # voz em qualidade maior
./scripts/models.sh install musetalk_15         # lip-sync
./scripts/models.sh remove wan22_ti2v_5b        # apaga do disco
```

| Chave | Tamanho | Para quê |
|---|---|---|
| `qwen3_tts_0_6b_base` | 2,6 GB | voz — padrão sem GPU |
| `qwen3_tts_1_7b_base` | 4,6 GB | voz — melhor, precisa de GPU |
| `faster_whisper_small` | 0,5 GB | legendas — padrão |
| `faster_whisper_large_v3` | 3,1 GB | legendas — precisa de GPU |
| `musetalk_15` | 4,2 GB | lip-sync |
| `wan22_ti2v_5b` | 18 GB | B-roll por IA (opcional) |

### Voz

```bash
./scripts/clone-studio voice-list
./scripts/clone-studio voice-enroll --audio a.wav --text "..." --id me
./scripts/clone-studio voice-test --id me --variants 3 --speed 1.05
```

### Projetos

```bash
./scripts/clone-studio create "sua ideia aqui"
./scripts/clone-studio projects
./scripts/clone-studio preview <id-do-projeto>
./scripts/clone-studio render <id-do-projeto>
./scripts/clone-studio render <id> --force tts   # refaz a voz e o que vem depois
```

### Templates e configuração

```bash
./scripts/clone-studio templates              # lista
./scripts/clone-studio templates --reindex    # revarre a pasta
./scripts/clone-studio config                 # mostra a config efetiva
```

### Instalação parcial

```bash
./install.sh --only backend      # reinstala só um ambiente
./install.sh --only qwen-tts
./install.sh --only whisper
./install.sh --with-musetalk     # inclui o lip-sync
./install.sh --skip-frontend
./install.sh --no-models
./install.sh --yes               # não pergunta nada
```

### Testes (para desenvolvimento)

```bash
.envs/backend/bin/python -m pytest tests -q     # Linux
.envs\backend\Scripts\python.exe -m pytest tests -q   # Windows

cd apps/web && pnpm build
```

---

## 9. Quanto tempo cada coisa leva

Medido num i7-1355U **sem GPU**:

| Etapa | Sem GPU | Com GPU NVIDIA |
|---|---|---|
| Voz — 10 s de fala | ~80 s | segundos |
| Legendas | ~tempo real | rápido |
| Lip-sync — 7 s de vídeo | **28 min** | minutos |
| Render final | ~4 s | ~4 s |
| Preview | ~10 s | ~10 s |

**Leia assim:** sem GPU, o caminho *voz + legendas + render* é perfeitamente
utilizável no dia a dia. O lip-sync não é.

O preprocessamento de um template é feito **uma vez** e fica guardado. O
segundo vídeo com o mesmo template pula ~10 minutos.

---

## 10. Quando der errado

**Sempre comece por:**

```bash
./scripts/doctor.sh          # Linux
.\scripts\doctor.ps1         # Windows
```

Ele diz exatamente o que falta e o comando para resolver.

| Sintoma | Solução |
|---|---|
| Cliquei no ícone e nada aconteceu | Espere 60 s — o boot é lento e silencioso |
| `a porta 8756 já está em uso` | `./stop.sh` ou `.\stop.ps1` |
| `execução de scripts foi desabilitada` (Windows) | use `powershell -ExecutionPolicy Bypass -File ...` |
| `python não é reconhecido` (Windows) | feche o PowerShell e abra outro; se persistir, reinicie |
| `Pesos de Qwen/... não baixados` | `./scripts/models.sh install --essential` |
| `Nenhum rosto detectado no template` | vídeo mal iluminado, de perfil ou rosto pequeno demais |
| A voz não parece comigo | confira a transcrição exata; grave amostra mais limpa |
| A interface não abre | veja `logs/web.out` (Linux) ou `logs\web.err` (Windows) |
| Job preso em "renderizando" | rode de novo — as etapas prontas são reaproveitadas |

Casos mais específicos: **`docs/TROUBLESHOOTING.md`**.

Todos os logs ficam em `logs/`. Cada render tem um id, e o erro sempre traz
etapa, comando, código de saída e a provável solução.

---

## 11. Privacidade

- O backend escuta só em `127.0.0.1` e **recusa** conexões de outros
  computadores.
- Sem telemetria, sem analytics, sem envio automático. Isso é imposto pelo
  código — não dá para ligar editando configuração.
- A única saída de rede é o download dos modelos, e só quando você manda.
- Sua voz e seus vídeos ficam em `data/identity/`, fora do Git.

**Faça backup da pasta `data/`.** Perder ela significa perder o cadastro da sua
voz e seus templates.

---

## Documentação relacionada

| Arquivo | Conteúdo |
|---|---|
| `README.md` | visão geral do projeto |
| `docs/WINDOWS.md` | guia detalhado do Windows |
| `docs/TROUBLESHOOTING.md` | problemas conhecidos, um a um |
| `docs/ARCHITECTURE.md` | como o sistema é construído |
| `docs/DECISIONS.md` | por que cada decisão técnica foi tomada |
