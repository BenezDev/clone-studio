# Registro de decisões

Cada entrada explica o problema real encontrado, a decisão tomada e o que foi
descartado. Datas em ISO.

---

## D-001 · Ambientes Python isolados por engine (2026-08-11)

**Problema.** Um único ambiente não instala. `qwen-tts==0.1.1` fixa
`transformers==4.57.3` e `accelerate==1.12.0`; o MuseTalk exige
`transformers==4.39.2`, `numpy==1.23.5`, `torch==2.0.1` e `mmcv==2.0.1`. Além
do conflito de versões, `torch 2.0.1` e `numpy 1.23.5` **não publicam wheels
para Python 3.12**, que é o Python deste sistema.

**Decisão.** Um venv por engine, com Pythons diferentes quando necessário.
`uv` provisiona um CPython 3.10 isolado só para o MuseTalk, sem tocar no Python
do sistema.

**Descartado.**
- *Forçar tudo num ambiente*: não instala.
- *Docker para tudo*: exigiria daemon rodando e complicaria acesso à GPU e a
  arquivos locais; o MVP em Linux deve funcionar sem isso.
- *Compilar torch 2.0.1 para 3.12*: horas de build e alta chance de falha.

---

## D-002 · Workers em subprocesso, backend sem torch (2026-08-11)

**Problema.** Com engines no mesmo processo do FastAPI: a API demoraria ~10 s
para subir, a memória de um modelo grande não voltaria ao sistema de forma
confiável, e um segfault de biblioteca nativa derrubaria a API inteira.

**Decisão.** Cada etapa pesada é um subprocesso curto que recebe um JSON e
responde JSON Lines por stdout. `core/worker/protocol.py` é **stdlib-only** de
propósito, para poder ser importado dentro de qualquer um dos ambientes.

**Ganhos concretos.** Isolamento; memória devolvida ao SO no fim do processo;
API instantânea; e erros com comando exato, exit code e stderr — o que torna
possível a regra de "nunca mostrar apenas *Generation failed*".

---

## D-003 · Qwen3-TTS 0.6B como padrão em CPU (2026-08-11)

**Contexto.** A especificação pede o 1.7B como engine principal e o 0.6B como
modo Low VRAM. Esta máquina é CPU_ONLY com 15 GB de RAM.

**Decisão.** `tts.model: auto` resolve para 0.6B em `CPU_ONLY`/`LOW_VRAM` e
1.7B acima disso. O 1.7B continua instalável com um comando.

**Medido.** 0.6B em CPU (10 threads): 53 s para gerar 6,96 s de fala em
português — cerca de 8× o tempo real. O 1.7B seria proporcionalmente mais lento
e ocuparia 4,6 GB em vez de 2,6 GB.

---

## D-004 · Emoção por múltiplas referências, não por instrução (2026-08-11)

**Problema.** A especificação pede controle de emoção/prosódia. O modelo
`Qwen3-TTS-...-Base` **não aceita** o parâmetro `instruct` — isso é exclusivo
dos modelos CustomVoice e VoiceDesign, que usam timbres pré-definidos e não
clonam a sua voz.

**Decisão.** Emoção seleciona entre múltiplas amostras **suas**, rotuladas por
estilo ("calmo", "confiante", "empolgado"). Preserva a identidade e usa o
recurso de múltiplas referências que a especificação já pedia.

**Descartado.** *Trocar para o CustomVoice para ganhar `instruct`*: perderia a
clonagem da voz do usuário, que é o ponto central do projeto.

---

## D-005 · Velocidade de fala via `atempo` do FFmpeg (2026-08-11)

O modelo Base não expõe controle de velocidade. `atempo` altera o andamento
preservando o tom, e valores fora da faixa 0,5–2,0 são encadeados. Aplicado no
pós-processamento, no adapter — o worker não precisa saber disso.

---

## D-006 · `mmdet` é obrigatório, mesmo sem ser importado (2026-08-11)

**Sintoma.** `CSPNeXt is not in the mmpose::model registry`.

**Causa.** `musetalk/utils/preprocessing.py` importa apenas `mmpose`, então
parecia que `mmdet` era dispensável. Mas a config do DWPose declara o backbone
como `_scope_='mmdet', type='CSPNeXt'`, e o registro do mmpose só resolve esse
nome com o `mmdet` instalado.

**Lição.** Em MMLab, a lista de imports não descreve as dependências reais — o
registro é resolvido por strings em tempo de execução.

---

## D-007 · Três correções para instalar o stack MMLab hoje (2026-08-11)

O stack é de 2023 e assume um ambiente daquela época:

1. **`setuptools >= 81` removeu `pkg_resources`**, que `mmengine 0.10.4` ainda
   importa → fixado em `setuptools==75.8.0`.
2. **`chumpy 0.70`** (dependência do mmpose) tem `setup.py` legado que importa
   `pip` em tempo de build; o build isolado do `uv` não tem pip → instalado com
   `--no-build-isolation`.
3. **`mmcv 2.0.1`** só tem wheel pré-compilada para cp310/cp311 → daí o
   Python 3.10, e o índice `download.openmmlab.com/mmcv/dist/cpu/torch2.0.0`.

Todas as três estão codificadas no `install.sh` para funcionar numa máquina
limpa.

---

## D-008 · Pesos baixados por URL entram no registro (2026-08-11)

**Problema.** O módulo `face_detection` do MuseTalk chama
`torch.hub.load_url` para buscar o detector S3FD (86 MB) de
`adrianbulat.com` na primeira execução, gravando em `~/.cache/torch`.

Isso quebrava duas promessas do projeto: funcionar offline depois da
instalação, e manter tudo dentro do diretório do projeto. Na prática, o
primeiro lip-sync levou 13 minutos só baixando esse arquivo de um servidor
lento.

**Decisão.** O registro ganhou `url_components`, o downloader busca esses
arquivos junto com o resto, e `TORCH_HOME` aponta para dentro dos pesos da
engine.

---

## D-009 · Espelho no HuggingFace em vez de Google Drive (2026-08-11)

O `download_weights.sh` oficial do MuseTalk usa `gdown` para buscar
`79999_iter.pth` do Google Drive — que impõe limites de taxa, muda de link e
falha em modo não interativo. O mesmo arquivo está em
`ManyOtherFunctions/face-parse-bisent` no HuggingFace, com download estável.

---

## D-010 · Só os pesos da v1.5, não os da v1.0 (2026-08-11)

O repositório do MuseTalk tem 6,8 GB porque guarda os pesos da v1.0 (3,4 GB) e
os da v1.5 (3,4 GB). Usamos a v1.5, então o download declara `include` explícito
por componente. Junto com o descarte do SyncNet (só treino), isso reduz o
download de ~8,5 GB para 4,4 GB.

---

## D-011 · faster-whisper como padrão, WhisperX como opção (2026-08-11)

A especificação prioriza WhisperX. Ele depende de torch + pyannote, enquanto o
faster-whisper usa CTranslate2 — sem torch, com quantização int8 e ambiente de
~250 MB. Em CPU, ele já entrega timestamps por palavra e roda em torno do tempo
real.

`transcription.engine: auto` escolhe faster-whisper em `CPU_ONLY` e tenta
WhisperX quando há GPU. As duas engines são independentes por trás da mesma
interface.

---

## D-012 · Destaque de legenda sem tags de karaokê (2026-08-11)

As tags `\k` do ASS renderizam de forma inconsistente entre players e não
permitem trocar a cor de uma palavra específica de modo previsível. Cada estado
de destaque vira um evento `Dialogue` próprio, com a palavra ativa estilizada
inline. O arquivo fica mais verboso e o resultado é exatamente igual em
qualquer libass.

---

## D-013 · Caixa alta de ênfase não é sigla (2026-08-11)

A primeira versão do normalizador soletrava qualquer sequência de 2 a 5
maiúsculas. Roteiros virais usam maiúsculas para ênfase, então
"isso NUNCA funciona" virava "isso ene u ene cê a funciona".

Regra atual: soletra apenas siglas conhecidas ou tokens de 2 a 4 letras **sem
vogal** (PDF, SMS, TV). Palavras com vogal em caixa alta são preservadas.

---

## D-014 · Modelos locais em vez de reaproveitar o cache das bibliotecas (2026-08-11)

O `faster-whisper` baixava o modelo de novo para o próprio cache mesmo com os
pesos já presentes em `models/` — meio giga duplicado e 2min42 de espera. O
adapter agora resolve o diretório do registro e passa o caminho local, o que
levou a transcrição do mesmo áudio de 2min42 para 6,8 s.

---

## D-015 · Degradação honesta quando o lip-sync não está instalado (2026-08-11)

Se o ambiente do MuseTalk não existe, o pipeline continua e entrega o vídeo
**sem** sincronia labial, avisando de forma explícita no resultado do job — em
vez de falhar o render inteiro. O aviso aparece na fila e no resultado, nunca
silenciosamente.

---

## D-016 · Wan2.2 declarado como incompatível, não escondido (2026-08-11)

Difusão de vídeo é inviável sem GPU dedicada. Em vez de esconder o módulo, o
registro declara a exigência real (12 GB de VRAM, 32 GB de RAM) e a interface
mostra por que ele está indisponível **nesta** máquina. Quando houver hardware,
ele aparece sozinho.

---

## D-017 · Cache de lip-sync com chave de plano, não de bytes (2026-08-11)

**Sintoma.** O preprocessamento de template do MuseTalk (~5 s por frame, o passo
mais caro do projeto) era refeito em toda execução, mesmo com o mesmo template.

**Causa.** A chave do cache era o hash do vídeo **composto** pelo template
engine. Mas o `render_plan` reencoda com x264 multi-thread, que **não é
determinístico**: duas composições idênticas produzem bytes diferentes.
Medido — mesma entrada, mesma duração, duas execuções:

```
5.0s exec1: 5f1c6e130ebcd9aa
5.0s exec2: eae843c25437da3d
```

Na prática o cache nunca acertava, exceto no resume do mesmo projeto (onde o
`template.mp4` já existe e não é regerado).

**Decisão.** `MuseTalkEngine.process()` aceita `cache_seed`, e o pipeline monta
esse seed a partir de `project.template.plan`: hash de **conteúdo dos vídeos de
origem** + como cada trecho foi cortado (`start`, `duration`) + fps. A chave
passou a ser estável entre execuções e continua invalidando quando o plano, o
`bbox_shift` ou o `extra_margin` mudam.

Sem seed (chamada direta do adapter, fora do pipeline), cai no hash do arquivo —
correto, só não reaproveita.

**Descartado.** *Tornar o x264 determinístico* (`-threads 1` ou
`x264-params sliced-threads`): custaria tempo de encode em todo render para
resolver um problema que é de chave de cache, não de encode.

**Pendente.** `render_plan` ainda reencoda mesmo quando a estratégia é `single`
cobrindo o template inteiro. É desperdício de CPU, mas não afeta mais a
correção do cache.

---

## D-018 · Comandos sugeridos são resolvidos por plataforma (2026-08-18)

**Sintoma.** O `models.ps1` terminava dizendo ao usuário de Windows:
`Comece com: ./scripts/models.sh install --essential`. Ele digita, recebe
"comando não encontrado" e conclui que o programa está quebrado.

Eram 25 pontos no código com comando fixo de Linux, incluindo dicas de erro do
pipeline — exatamente o texto que alguém lê quando já está com problema.

**Decisão.** `core/platform_hints.py` passa a ser a fonte única. Toda mensagem
que sugere um comando chama `hints.install()`, `hints.models()`, `hints.doctor()`.

No Windows o comando de instalação sai com `-ExecutionPolicy Bypass` na frente,
de propósito: a dica é lida na tela e colada num PowerShell **novo**, onde a
política volta ao padrão restritivo e o script seria recusado sem o prefixo.

**Armadilha encontrada na própria correção.** Ao substituir os 25 pontos, o
`import` não entrou em 6 arquivos. O código só quebraria com `NameError` no
momento de mostrar um erro — o pior momento possível. Um teste com AST agora
verifica que quem usa `hints.` importa `hints`.

---

## D-019 · Underscore inicial é identificador válido (2026-08-18)

**Sintoma.** O render falhava em `prepare` com "ID do perfil inválido" para o
perfil `_smoke` — que o próprio `voice-enroll` havia criado sem reclamar.

**Causa.** O validador de componente de caminho exigia primeiro caractere
alfanumérico (`^[A-Za-z0-9][...]`). O cadastro não aplicava a mesma checagem,
então o perfil era aceito na criação e recusado no uso.

**Decisão.** Underscore inicial passa a ser aceito. Não é risco de travessia
nem nome reservado do Windows. O **ponto** inicial continua barrado, porque
cria arquivo oculto e abre caminho para `.` e `..`.

O resto da proteção segue intacto e testado: `..`, separadores de caminho,
nomes reservados (`con`, `nul`, `com1`, `lpt9`, `PRN.txt`), espaço ou ponto
final (que o Windows remove silenciosamente), vazio e acima de 128 caracteres.

Um teste percorre os perfis e projetos já gravados no disco e exige que todos
continuem passando na validação — a incoerência entre criar e usar é o bug de
verdade, não o caractere em si.

---

## D-020 · Implementação tem que honrar a interface (2026-08-18)

**Sintoma.** `MuseTalkEngine.process() got an unexpected keyword argument
'should_cancel'`, na etapa de lip-sync, depois de o TTS já ter gastado minutos.

**Causa.** O pipeline ganhou cancelamento. A interface `LipSyncEngine` e o
adapter de TTS foram atualizados; o do MuseTalk não. Numa correção parcial
seguinte, o corpo passou a usar `should_cancel` sem declará-lo na assinatura —
o que trocaria o `TypeError` por um `NameError` no mesmo lugar.

**Decisão.** `tests/unit/test_engine_contracts.py` compara, para as quatro
engines, a assinatura da implementação com a da interface abstrata, e analisa o
corpo por AST para pegar parâmetro usado sem ser declarado.

Cancelar durante o lip-sync importa mais que nas outras etapas: é a que roda
por dezenas de minutos em CPU.

---

## D-021 · Gravar dentro do app, sem substituir o envio de arquivo (2026-08-18)

**Problema.** Cadastrar voz e template exigia sair do estúdio: gravar em outro
programa, exportar, achar a pasta, voltar e enviar. Para o caso mais comum —
"quero cadastrar a minha voz e o meu rosto agora" — o gerenciador de arquivos
era o passo mais difícil de um app que roda inteiro em `127.0.0.1`.

**Decisão.** `MediaRecorder` do navegador, com o Blob virando `File` no mesmo
POST multipart que o upload já usava. O envio de arquivo continua ao lado, em
pé de igualdade: quem já tem material bom gravado não pode ser obrigado a
regravar, e webcam é pior que celular para template.

**Armadilha que isto esconde.** O `MediaRecorder` escreve em stream. Quem grava
WebM/Matroska numa saída não-buscável não pode voltar ao início para preencher
o cabeçalho, então o arquivo sai **sem duração e sem índice de busca**. O
`ffprobe` devolve `duration: N/A`, `probe()` converte para `0.0` e
`validate_video`/`clone_voice` recusam uma gravação perfeita dizendo "0.0s" — o
mesmo sintoma de um arquivo truncado. Não é um caso de borda: acontece em
**toda** gravação.

`ffmpeg.ensure_container_metadata()` remuxa com `-c copy` antes de qualquer
validação. O ffmpeg percorre os timestamps, calcula a duração real e grava um
cabeçalho completo, sem reencodar — nenhum custo de CPU e nenhuma perda de
qualidade, o que importa numa máquina sem GPU. Arquivo íntegro não é tocado, e
o reparo é idempotente. `tests/conftest.py` fabrica o defeito escrevendo num
pipe, para o teste rodar contra o arquivo real e não contra um bem formado.

**Duas escolhas de captura que parecem detalhe e não são.**

- *Áudio sem `echoCancellation`, `noiseSuppression` e `autoGainControl`.* Os
  três existem para chamada de vídeo: cortam ruído, mas também bombeiam o ganho
  e comem o final das palavras. Numa amostra de referência isso vira sotaque
  artificial no clone. Cru é melhor.
- *Template gravado sem áudio.* A fala do vídeo final vem do clone de voz e o
  `render_plan` descarta o áudio do template com `-an`. Capturar o microfone
  ali seria gravar som sem motivo.

**Descartado.**
- *Transcodificar a gravação para MP4/H.264 na chegada.* Custaria minutos de
  CPU e uma segunda perda de qualidade no material que alimenta o lip-sync. O
  `render_plan` já normaliza cada segmento para H.264 antes de concatenar, então
  o contêiner de origem não precisa ser uniforme.
- *Trocar o envio de arquivo pela gravação.* Webcam de notebook é pior que a
  câmera de qualquer celular, e há quem já tenha o material pronto.

**Fecha o dispositivo.** A câmera e o microfone só abrem no clique e são
fechados ao sair da tela ou ao terminar o cadastro. Um app local-first não pode
deixar a luz da câmera acesa em segundo plano.

---

## D-022 · O auto editor corta enquadramento, não tempo (2026-08-19)

**Problema.** A fase 9 estava especificada como "auto editor (EDL, cortes,
zooms)". Cortar, num editor comum, significa remover tempo.

**Por que remover tempo é impossível aqui.** O áudio é gerado pelo TTS e o vídeo
foi lip-sincronizado quadro a quadro contra ele. Tirar meio segundo da imagem
dessincroniza a boca da fala, e refazer o lip-sync custa os ~20 minutos de CPU
que a etapa inteira leva. Não existe corte de tempo que não quebre o resultado —
a especificação pedia algo que o resto da arquitetura proíbe.

**Decisão.** Corte de *enquadramento*. É o que um editor humano faz quando tem
**uma câmera só**, que é exatamente o caso do Production Mode: fecha no rosto na
frase que importa, abre quando ela termina. Lê como corte, preserva a duração e
não encosta na sincronia.

**A EDL é dado, não filtro.** `services/video/editor.py` planeja e grava
`edit.json` com início, fim, zoom e motivo de cada enquadramento; o render só
executa. Um filtro montado direto no render seria impossível de inspecionar, e
"por que o corte caiu aqui" é a primeira pergunta de quem revisa uma edição
automática.

**Três detalhes que separam isto de edição que parece automática.**

- *Distância mínima entre cortes, aplicada antes da quebra por duração.* Na
  ordem inversa, um corte inserido por tempo seria seguido de outro por frase
  200 ms depois — o tremor que denuncia a máquina.
- *Zoom alternado.* Dois enquadramentos seguidos no mesmo valor não leriam como
  corte, seriam um enquadramento longo.
- *Âncora acima do centro.* Para manter um ponto fixo enquanto a janela encolhe
  o deslocamento é `a * tamanho * (1 - 1/zoom)`; com `a` no centro isso reduz à
  fórmula conhecida e puxa a imagem para o tronco, cortando a testa. Em 0.42 o
  rosto fica no lugar.

**Duas armadilhas do `zoompan`, ambas encontradas rodando, não lendo.**

1. **Ele reescreve a cadência.** Sem `fps` explícito assume 25. A primeira
   versão passava o fps do vídeo de lip-sync (25) para um render configurado a
   30: o arquivo saiu com 122 quadros em vez de 146, sem erro e sem aviso. O fps
   tem que vir do **destino**, e o filtro por isso é montado no render, que é
   quem sabe se a saída é preview ou final. O teste que existia não pegava
   porque origem e destino tinham o mesmo fps.
2. **A expressão é somatório, não `if` aninhado.** Cada troca vira
   `delta*clip((it-corte)/rampa, 0, 1)` e o valor num instante é a soma do que
   já disparou. Cresce em comprimento, não em profundidade — dezenas de `if`
   encaixados estourariam o parser. As vírgulas vão escapadas, senão separam
   filtros e quebram a cadeia inteira.

**Ordem na cadeia.** O zoom entra antes das legendas. Texto que escala junto com
a imagem sai da zona segura e fica ilegível no pico do movimento.

**Descartado.**
- *`crop` com expressões de tempo*: a saída teria tamanho variável, o que o
  encoder não aceita.
- *Presets `podcast`, `viral` e `cinematic`*, que o endpoint de presets já
  anunciava sem nada por trás. Ficaram três que descrevem o que existe:
  `clean`, `sutil` e `dinamico`.

---

## D-023 · Animações gráficas em ASS, não em Remotion (2026-08-19)

**Contexto.** A fase 10 estava escrita como "Remotion — animações gráficas". O
Remotion é a ferramenta anotada no README, não o objetivo.

**O problema de licença.** Remotion é gratuito para indivíduos e empresas de até
três pessoas; a partir de quatro exige Company License paga, e há cláusula de
agregação somando funcionários de todas as partes numa colaboração. A FAQ diz
que *"a responsabilidade de comprar a licença recai sobre a entidade que em
última instância detém a IP do projeto Remotion"* — num software feito para ser
vendido, isso significa que o **comprador** herda a obrigação. Este projeto
recusa recomendar o MuseTalk em modo comercial por causa das dependências do
DWPose; embutir o Remotion em silêncio seria incoerente com a própria regra.

**Decisão.** ASS, desenhado pelo libass — que o projeto **já** usa para as
legendas. O formato suporta formas vetoriais (`\p1`), transformações animadas
(`\t`), movimento (`\move`), fades e recortes: suficiente para card de hook,
lower third, barra de progresso e end card. Zero dependência nova, zero
Chromium (~400 MB), custo de render irrelevante numa máquina sem GPU, e nenhuma
licença viajando com o produto.

**Custo aceito.** Design complexo em React fica fora do alcance. Para vídeo
vertical curto com elementos templatizados, não faz falta.

**Os textos vêm do roteiro.** Hook e CTA que o autor escreveu. Sem eles, só a
barra de progresso aparece — card com texto genérico é pior que card nenhum.

**Três armadilhas, todas encontradas renderizando.**

1. **O libass descarta linha malformada em silêncio.** Nenhum erro, nenhum
   aviso, só um gráfico que não aparece. Por isso os testes de integração
   renderizam sobre fundo preto e conferem **pixel**: string bem formada e
   arquivo válido provam nada.
2. **Python come as tags.** `\a` é BEL, `\b` é backspace, `\f` é formfeed —
   sem raw string, `\an5`, `\bord` e `\fscx` viram bytes de controle. Um teste
   varre a saída atrás de caractere de controle.
3. **`\c` e a cor de estilo são formatos diferentes.** O estilo usa
   `&HAABBGGRR`, a tag usa `&HBBGGRR&`. O libass lê o número e mascara, então
   passar os oito dígitos funciona *enquanto o alfa for `00`* e erra a cor
   depois — um bug que aparece longe da causa.

**Duas correções de dimensionamento.** A quebra de linha era um número fixo de
caracteres: estourava o quadro no preset de fonte grande, porque `WrapStyle: 2`
desliga a quebra automática do libass. Agora sai da fonte e da largura reais. E
escapar **depois** de quebrar transformava o próprio `\N` em texto visível no
meio da frase — a ordem é escapar, depois quebrar.

**Teto de duração.** Hook e end card ficam limitados a 40% do vídeo. Sem isso,
num clipe curto o end card cobria o clipe inteiro em vez de fechar.

---

## D-024 · B-roll de biblioteca; o generativo continua fora (2026-08-19)

**Contexto.** A fase 11 estava escrita como "ComfyUI / Wan2.2 — B-roll
generativo". Wan2.2 é difusão de vídeo: sem GPU dedicada são horas por segundo
gerado. O adapter já existia e já declarava a incompatibilidade corretamente.

**Decisão.** Implementar o B-roll que **funciona** neste hardware: biblioteca
local de arquivos, escolhidos por palavra-chave e compostos por cima da imagem.
O generativo fica exatamente onde estava — opt-in, desligado, declarado
incompatível. Resolve o mesmo problema de produção sem prometer o que a máquina
não entrega.

**Cobertura, não corte.** Mesma restrição do auto editor (D-022): o vídeo está
lip-sincronizado quadro a quadro com o áudio, então o apoio entra por cima e a
duração fica intacta. É o que B-roll é num vídeo de uma câmera só.

**Correspondência por palavra-chave, e o nome disso não é "semântica".** A
página de Assets prometia "indexação semântica"; isso exigiria um modelo de
embeddings, outro peso para baixar e outro ambiente. A busca compara os termos
do pedido com as palavras do nome do arquivo, sem acento e sem palavra vazia.
Para uma biblioteca pessoal de dezenas de arquivos com nomes descritivos,
resolve — e o texto da página foi corrigido para dizer o que o código faz.

**Nada entra sem o autor pedir.** Só há B-roll onde a cena tem `broll_prompt`
preenchido, e só acima de nota mínima de correspondência. Um falso positivo
cobre o rosto da pessoa com a imagem errada no meio da frase, e ela só descobre
assistindo ao render pronto — daí boa parte dos testes ser sobre o que **não**
pode casar.

**Passo próprio, não mais um filtro.** `overlay` exige entradas extras e
trocaria o `-vf` do render por um `filter_complex` — mudança de risco no
caminho que produz o arquivo final. Como passo separado, o artefato fica em
`stages/broll.mp4`, validável e inspecionável como o de qualquer outra etapa. E
falha de B-roll não derruba o render: apoio visual é acréscimo, e perder o
render inteiro por causa dele seria trocar o essencial pelo acessório.

**Duas armadilhas do `overlay`.**

1. Sem `eof_action=pass` e `shortest=0`, o primeiro trecho de apoio que termina
   **encerra a saída inteira** e o vídeo sai truncado. Num pipeline onde a
   duração precisa bater com o áudio ao quadro, isso quebra a sincronia.
2. Imagem parada precisa de `-loop 1 -t <duração>` na entrada. Sem isso o
   overlay recebe um quadro só e a imagem some no seguinte.

**Aparar, não descartar.** A primeira versão descartava o corte inteiro quando
ele não cabia no teto de cobertura — e num vídeo curto o primeiro corte já
estoura o teto, então o roteiro que pediu apoio visual não recebia nenhum.
Agora o trecho é encurtado para caber, e só é recusado se o que sobra fica
abaixo do mínimo.
