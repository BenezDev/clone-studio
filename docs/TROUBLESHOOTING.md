# Problemas conhecidos

Comece sempre por:

```bash
./scripts/doctor.sh
```

No Windows, o equivalente é:

```powershell
.\scripts\doctor.ps1
```

Ele mostra o estado real de hardware, ambientes, modelos, portas e engines — e
diz o comando exato que falta rodar.

---

## Instalação

### `Python 3.x é antigo demais`

O projeto exige Python 3.10+. No Ubuntu/Mint:

```bash
sudo apt install python3 python3-venv python3-pip
```

### `O módulo 'venv' não está disponível`

```bash
sudo apt install python3-venv
```

### `ffmpeg ausente (obrigatório)`

```bash
sudo apt install ffmpeg
```

O instalador nunca roda `sudo` sozinho — ele mostra o comando e você decide.

No Windows, instale FFmpeg e Git pelo `winget` (ou outro gerenciador de sua
preferência), abra um novo PowerShell e confirme `ffmpeg -version` e
`git --version` antes de executar `install.ps1`.

### `falha ao instalar mmcv 2.0.1`

`mmcv` só tem wheel pré-compilada para Python 3.10/3.11. Se o `uv` não
conseguiu provisionar o CPython 3.10, ele tenta compilar do zero — o que leva
dezenas de minutos e costuma falhar.

Verifique:

```bash
uv python list | grep 3.10
.envs/musetalk/bin/python --version   # precisa dizer 3.10.x
```

No Windows, o interpretador fica em
`.envs\musetalk\Scripts\python.exe`. Se o índice da OpenMMLab não fornecer um
wheel Windows compatível, use o fallback WSL2 documentado no README; não
unifique o ambiente do MuseTalk com o backend.

### `falha ao compilar chumpy`

`chumpy 0.70` tem um `setup.py` legado que importa `pip` em tempo de build,
e o build isolado do `uv` não tem pip. Solução já embutida no instalador:

```bash
uv pip install --python .envs/musetalk/bin/python pip "setuptools==75.8.0" wheel
uv pip install --python .envs/musetalk/bin/python --no-build-isolation chumpy==0.70
```

### `ModuleNotFoundError: No module named 'pkg_resources'`

`setuptools >= 81` removeu `pkg_resources`, que o `mmengine 0.10.4` ainda usa.

```bash
uv pip install --python .envs/musetalk/bin/python "setuptools==75.8.0"
```

Isso pode reaparecer se algo reinstalar o setuptools mais novo como
dependência transitiva.

---

## Voz

### `Pesos de Qwen/... não baixados`

```bash
./scripts/models.sh install --essential
```

### `SoX could not be found!`

Aviso inofensivo. O `sox` só é usado pelo tokenizador de 25 Hz do `qwen-tts`;
o projeto usa o de 12 Hz.

### `Warning: flash-attn is not installed`

Esperado em CPU. O `flash-attn` só existe para GPU NVIDIA; o adapter cai
automaticamente para `sdpa` e, se preciso, `eager`.

### A voz não parece comigo

Em ordem de impacto:

1. **Qualidade da amostra.** Precisa de voz isolada, sem música, sem eco e sem
   ruído de fundo. Um celular perto da boca num cômodo com cortina rende mais
   que um microfone caro numa sala vazia.
2. **Transcrição exata.** Escreva palavra por palavra o que é dito. Sem ela, o
   modelo cai no `x_vector_only_mode` e a qualidade despenca.
3. **Duração.** 5 a 30 segundos de fala contínua e natural.
4. **Modelo maior.**
   ```bash
   ./scripts/models.sh install qwen3_tts_1_7b_base
   ```
   E em `config/local.yaml`:
   ```yaml
   tts:
     model: Qwen/Qwen3-TTS-12Hz-1.7B-Base
   ```
5. **A/B.** Gere 3 a 5 variantes na página Voice e fique com a melhor.

### A geração está lenta

Em CPU, o 0.6B leva cerca de 8 segundos de processamento por segundo de áudio.
Roteiros de 60 segundos levam ~8 minutos só de TTS. Reduza a duração ou use uma
máquina com GPU NVIDIA.

---

## Lip-sync

### `CSPNeXt is not in the mmpose::model registry`

Falta o `mmdet` — a config do DWPose declara o backbone com
`_scope_='mmdet'`.

```bash
uv pip install --python .envs/musetalk/bin/python "mmdet==3.1.0"
uv pip install --python .envs/musetalk/bin/python "setuptools==75.8.0"
```

### `Nenhum rosto detectado no template`

O MuseTalk precisa do rosto visível e razoavelmente frontal. Verifique se o
vídeo tem boa iluminação, se o rosto ocupa parte significativa do quadro e se
você não está de perfil ou muito longe da câmera.

### O primeiro lip-sync fica minutos "baixando"

O detector S3FD (86 MB) era buscado em tempo de execução de um servidor lento.
Ele agora faz parte do download do modelo:

```bash
./scripts/models.sh install musetalk_15 --force
```

### O lip-sync está insuportavelmente lento

É o custo real de CPU. Números medidos num i7-1355U:

- preprocessamento do template: ~5 s por frame — **uma vez por template**;
- inferência: vários segundos por frame.

O que ajuda de verdade:

1. **Reutilizar templates.** O preprocessamento é cacheado por conteúdo do
   vídeo; o segundo vídeo com o mesmo template pula essa etapa inteira.
2. **Templates curtos.** Um template de 15 s coberto por composição rende mais
   que um de 60 s.
3. **Roteiros curtos.** O custo é por frame de áudio.
4. **GPU NVIDIA.** É a diferença entre minutos e horas.

Para forçar o recálculo do cache de um template:

```bash
rm -rf cache/templates/
```

### Quero renderizar sem lip-sync

Se o ambiente do MuseTalk não estiver instalado, o pipeline entrega o vídeo sem
sincronia labial e avisa no resultado do job — em vez de falhar o render.

---

## Legendas

### `A transcrição não produziu timestamps por palavra`

Quase sempre significa que o áudio está mudo ou quase mudo. Confira:

```bash
ffplay projects/<id>/voice.wav
```

### O faster-whisper baixa o modelo toda vez

Corrigido: o adapter passa o caminho local do registro. Se ainda acontecer,
confirme que os pesos estão instalados:

```bash
./scripts/models.sh status
```

---

## Render

### `Unknown encoder 'libx264'`

O build do FFmpeg da distribuição não tem H.264:

```bash
ffmpeg -encoders | grep x264
sudo apt install ffmpeg
```

### O vídeo final está fora de 9:16

O render impõe 1080×1920 recortando para preencher. Se o enquadramento ficou
ruim, o template provavelmente é horizontal — a página Templates avisa quando
isso acontece. Grave na vertical.

---

## Interface

### A interface não abre

```bash
tail -50 logs/web.out
```

Node 20+ é necessário. Confira com `node -v`.

### `Não foi possível falar com o backend`

```bash
tail -50 logs/api.out
curl http://127.0.0.1:8756/api/health
```

### A porta já está em uso

```bash
./stop.sh
```

Ou mude em `config/local.yaml`:

```yaml
app:
  api_port: 8757
  web_port: 3001
```

---

## Jobs

### Um job ficou preso em "renderizando"

Se o backend foi reiniciado durante a execução, o job vira "falhou" com a
mensagem correspondente na próxima subida. Basta rodar de novo — as etapas
concluídas são reaproveitadas.

### Quero refazer só uma etapa

Na interface, o render normal já reaproveita tudo que estiver válido. Para
forçar, use a API:

```bash
curl -X POST http://127.0.0.1:8756/api/projects/<id>/render \
  -H 'Content-Type: application/json' \
  -d '{"force_stages":["tts"]}'
```

Forçar uma etapa invalida automaticamente todas as posteriores.

---

## Espaço em disco

```bash
./scripts/models.sh status          # pesos instalados
du -sh cache/ projects/ exports/
```

Limpar caches pela interface (Settings → Cache) ou:

```bash
rm -rf cache/tts cache/transcripts
```

O cache de templates (`cache/templates/`) é o mais caro de recalcular — apague
por último.
