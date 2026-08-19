# Workflows do ComfyUI

Os workflows deste diretório são **nossos** e ficam versionados junto com o
código. O ComfyUI roda como serviço separado; a aplicação fala com ele apenas
por API e nunca importa código dele.

## Formato

Salve sempre no formato **API** do ComfyUI (menu → `Save (API Format)`), não no
formato da interface. O formato da UI carrega posição de nós e metadados de
canvas que não servem para execução.

## Nós esperados

O adapter (`services/comfyui/wan22.py`) sobrepõe os campos por **id de nó**.
Para funcionar, o workflow precisa ter nós com estes ids:

| id | função | campos sobrescritos |
|---|---|---|
| `positive_prompt` | prompt positivo | `text` |
| `negative_prompt` | prompt negativo | `text` |
| `video_settings` | dimensões e duração | `width`, `height`, `num_frames`, `fps` |
| `sampler` | amostrador | `seed` |
| `input_image` | imagem de entrada (só no i2v) | `image` |

Um id ausente é tratado como **erro**, não como aviso: gerar um vídeo com o
prompt errado por causa de um id renomeado seria pior que falhar.

## Arquivos

| Arquivo | Uso |
|---|---|
| `wan22_t2v.json` | texto → vídeo |
| `wan22_i2v.json` | imagem + texto → vídeo |

Nenhum dos dois está incluído: eles dependem da versão da extensão do Wan2.2
instalada no seu ComfyUI. Exporte a partir da sua instalação e renomeie os nós
conforme a tabela acima.

## Antes de usar

Este módulo é **opcional** e exige GPU dedicada com VRAM significativa. Rode:

```bash
./scripts/clone-studio test comfyui
```

Se a resposta for `offline`, tudo o mais no programa continua funcionando — a
biblioteca local de B-roll é a fonte principal por design.
