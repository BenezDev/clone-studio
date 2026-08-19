# Instalar no Windows 10/11

> **Leia antes de começar.** Os scripts `.ps1` foram escritos e revisados, mas
> **nunca foram executados num Windows real** — todo o desenvolvimento e teste
> aconteceu no Linux. Espere encontrar algum problema na primeira vez. Este
> guia inclui o que fazer quando isso acontecer.

---

## 1. O que a máquina precisa ter

| Item | Mínimo | Como conferir |
|---|---|---|
| Windows | 10 ou 11, 64 bits | — |
| Disco livre | **15 GB** (25 GB com lip-sync) | — |
| RAM | 8 GB (16 GB confortável) | — |
| Internet | sim, para baixar ~7 GB | — |

**GPU NVIDIA muda tudo.** Com uma, o lip-sync leva minutos; sem, leva horas.
O instalador detecta sozinho — você não precisa fazer nada.

---

## 2. Instalar os pré-requisitos

Abra o **PowerShell como Administrador** (tecla Windows → digite `powershell` →
botão direito → *Executar como administrador*) e rode:

```powershell
winget install --id Python.Python.3.12 -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id Gyan.FFmpeg -e
winget install --id Git.Git -e
```

Se o `winget` não existir nessa máquina, instale pela loja da Microsoft
("App Installer") ou baixe cada um do site oficial.

**Feche o PowerShell e abra outro** (sem administrador desta vez). Isso é
necessário para o PATH atualizar. Confirme:

```powershell
python --version
node --version
ffmpeg -version
git --version
```

Os quatro precisam responder. Se algum falhar, o PATH não atualizou — reinicie
o computador e tente de novo.

---

## 3. Copiar o projeto

Leve a pasta do projeto num pen drive e coloque num caminho **curto e sem
acento**, por exemplo:

```
C:\CloneStudio
```

> Evite `Área de Trabalho`, `Documentos` ou OneDrive. Caminho curto e ASCII
> elimina uma classe inteira de problemas no Windows.

**Não copie estas pastas** (são grandes e serão recriadas):
`.envs\`, `node_modules\`, `cache\`, `logs\`, `external\`

A pasta `models\` você **pode** copiar — economiza ~7 GB de download.

---

## 4. Instalar

**Opção A — 1 Clique (Recomendado para o Cliente):**
Dê **dois cliques em `install.bat`**. O instalador abre e executa tudo automaticamente sem precisar digitar comandos.

**Opção B — No PowerShell:**
Dentro de `C:\CloneStudio`:

```powershell
cd C:\CloneStudio
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

O `-ExecutionPolicy Bypass` é necessário: o Windows bloqueia scripts não
assinados por padrão. Ele vale só para esta execução, não muda a configuração
da máquina.

Leva **15 a 40 minutos** dependendo da internet. No final ele pergunta se quer
baixar os modelos.

### Quanto vai baixar

| O quê | Tamanho | Precisa? |
|---|---|---|
| Ambientes Python | ~2,5 GB | sim |
| Voz + legendas (essencial) | **3,1 GB** | sim |
| Lip-sync (MuseTalk) | 4,2 GB | **não no primeiro dia** |

---

## 5. Primeiro uso — pule o lip-sync

**Recomendação forte:** no primeiro dia, instale só voz + legendas. O lip-sync
é a parte mais frágil no Windows e a mais lenta. Deixe para depois, com calma.

```powershell
.\scripts\models.ps1 install --essential
.\start.ps1
```
*(ou simplesmente dê **dois cliques em `start.bat`**)*

Abra <http://127.0.0.1:3000>.

Isso já entrega: roteiro → **voz clonada** → vídeo vertical → **legendas
palavra a palavra** → MP4 1080×1920. Sem sincronia labial ainda — o pipeline
avisa e entrega o vídeo mesmo assim, em vez de falhar.

### Comandos e atalhos do dia a dia

| Ação | 1 Clique (Mais Fácil) | No PowerShell |
|---|---|---|
| **Ligar o estúdio** | Dois cliques em `start.bat` | `.\start.ps1` |
| **Desligar** | Dois cliques em `stop.bat` | `.\stop.ps1` |
| **Diagnóstico** | Dois cliques em `doctor.bat` | `.\scripts\doctor.ps1` |


---

## 6. Lip-sync (depois, sem pressa)

```powershell
winget install --id astral-sh.uv -e
# feche e abra o PowerShell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Only musetalk
.\scripts\models.ps1 install musetalk_15
.\scripts\clone-studio.ps1 test lipsync
```

Todas as dependências têm pacote pronto para Windows (`mmcv`, `xtcocotools`,
`pycocotools`, `torch 2.0.1` — todos com wheel `cp310 win_amd64`), então em
teoria funciona sem compilar nada. **Em teoria** — nunca foi executado.

Se falhar, o erro exato está em `docs/TROUBLESHOOTING.md`. O plano B é rodar o
lip-sync no WSL2; o resto continua no Windows.

---

## 7. Quando der errado

**Sempre comece por:**

```powershell
.\scripts\doctor.ps1
```

Ele diz exatamente o que falta e o comando para resolver.

| Sintoma | Causa provável | Solução |
|---|---|---|
| `não pode ser carregado porque a execução de scripts foi desabilitada` | política do Windows | use `powershell -ExecutionPolicy Bypass -File .\install.ps1` |
| `python não é reconhecido` | PATH não atualizou | feche o PowerShell, abra outro; se persistir, reinicie |
| `ffmpeg não encontrado no PATH` | idem | idem |
| `A porta 8756 já está em uso` | sobrou instância | `.\stop.ps1` |
| Nada acontece ao rodar `start.ps1` | ainda subindo | espere 60 s e abra <http://127.0.0.1:3000> |
| A interface não abre | frontend falhou | veja `logs\web.err` |
| Erro de acento/caminho | pasta com nome complicado | mova para `C:\CloneStudio` |

**Log de tudo:** a pasta `logs\` guarda a saída completa de cada etapa. Se
precisar de ajuda, é dela que sai a informação útil.

---

## 8. Expectativa realista de desempenho

Medido num i7 sem GPU:

| Etapa | Sem GPU | Com GPU NVIDIA |
|---|---|---|
| Voz (10 s de fala) | ~80 s | segundos |
| Legendas | ~tempo real | rápido |
| Lip-sync (7 s de vídeo) | **28 min** | minutos |
| Render final | segundos |segundos |

Sem GPU, o caminho **voz + legendas + render** é perfeitamente utilizável. O
lip-sync não é, para uso diário.

---

## 9. O que levar no pen drive

```
CloneStudio\          o projeto (sem .envs, node_modules, cache, logs, external)
models\               opcional — economiza 7 GB de download
docs\WINDOWS.md       este guia
```

---

## 10. Privacidade — não muda no Windows

Continua tudo local: backend em `127.0.0.1`, sem telemetria, sem nuvem. A
única saída de rede é o download dos pesos, e só quando você manda.

A voz e os vídeos do seu tio ficam na máquina dele, em `data\identity\`.
