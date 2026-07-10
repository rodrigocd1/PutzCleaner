# Plano de implementação executável — PutzCleaner

> Versão do plano: 1.0  
> Data de elaboração: 09/07/2026  
> Plataforma-alvo: Windows 10/11 x64  
> Estado: planejamento completo; o software ainda não foi implementado  
> Idioma da interface e da documentação: português do Brasil

## 1. Como usar este documento

Este arquivo é a especificação operacional para outra IA ou pessoa implementar o PutzCleaner sem precisar reinterpretar o pedido original. Ele define decisões técnicas, contratos entre módulos, algoritmos, ordem de execução, validações, mensagens, testes e critérios de aceite.

A execução deve seguir estas regras:

1. Ler este documento inteiro antes de alterar qualquer arquivo.
2. Auditar novamente o repositório; o estado pode ter mudado depois da criação deste plano.
3. Ler integralmente qualquer arquivo-alvo que já exista antes de editá-lo.
4. Se algum arquivo-alvo contiver trabalho que possa ser sobrescrito, parar e explicar o conflito. Não substituir silenciosamente.
5. Não executar `git add`, `commit`, `push`, `pull`, `merge`, `rebase`, criação de branch ou qualquer outra mutação Git.
6. Não executar comandos destrutivos no repositório.
7. Criar somente os arquivos de entrega descritos neste plano. Diretórios de ambiente, modelos e FFmpeg só podem ser gerados pelo setup ou pelo programa porque são necessários ao funcionamento.
8. Fazer alterações locais com patches pequenos e revisáveis.
9. Executar realmente cada teste que for declarado como aprovado.
10. Se não houver um vídeo real de entrevista fornecido, nunca afirmar que um vídeo real foi processado.

## 2. Auditoria inicial já realizada

Na criação deste plano, o repositório `C:\GIT\Repository\PutzCleaner` continha somente a pasta oculta `.git`.

- Não havia código, configuração, README ou scripts existentes.
- A branch local era `main`, sem commits.
- Nenhum arquivo foi sobrescrito.
- O ambiente observado possuía Python 3.11.9 x64, FFmpeg 8.1 e ffprobe 8.1.
- Nenhum vídeo foi fornecido ou processado.

Esses dados são apenas o retrato de 09/07/2026. Antes da implementação, repetir:

```powershell
Get-Location
rg --files -g '!**/.git/**'
Get-ChildItem -Force
git status --short --branch
```

O uso de `git status` é somente leitura. Não consultar nem alterar o remoto.

## 3. Objetivo e resultado esperado

O PutzCleaner será um aplicativo desktop local para Windows que:

1. recebe um vídeo `.mp4` de entrevista;
2. transcreve o primeiro áudio em português localmente;
3. identifica vícios de fala configurados pelo usuário com timestamps por palavra;
4. rejeita ocorrências de baixa confiabilidade ou com timestamps inválidos;
5. calcula cortes seguros, com margens e proteção das palavras vizinhas;
6. remove os intervalos de vídeo e áudio com FFmpeg;
7. gera um novo MP4 H.264/AAC, sem modificar o original;
8. gera um relatório JSON auditável;
9. mantém a interface responsiva durante todo o processamento.

Fluxo do usuário:

```text
Abrir abrir_putzcleaner.bat
  → selecionar um MP4
  → revisar palavras/modelo/margens/pasta de saída
  → clicar em “Processar vídeo”
  → acompanhar progresso e logs
  → receber os caminhos do vídeo limpo e do relatório
```

## 4. Escopo e não objetivos

### 4.1 Incluído no MVP

- Aplicação desktop de tela única.
- Tkinter com widgets `ttk`.
- Windows 10/11 x64.
- CPython 3.11 x64.
- `faster-whisper` local em CPU/int8.
- Modelos `small`, `medium` e `large` na interface.
- Português fixo (`pt`).
- MP4 com pelo menos uma faixa de vídeo e uma de áudio.
- Primeiro stream de áudio do arquivo.
- H.264 (`libx264`) e AAC.
- Relatório JSON.
- Configuração persistida.
- Instalação por `setup.bat` e abertura por `abrir_putzcleaner.bat`.
- Download inicial gratuito dos pacotes, FFmpeg e pesos do modelo.
- Processamento offline depois que as dependências e o modelo escolhido estiverem armazenados.

### 4.2 Fora do escopo

- Aplicação web, servidor, Flask, FastAPI, Electron ou navegador.
- APIs de transcrição pagas ou envio do vídeo para serviços externos.
- Download de vídeos do YouTube.
- Diarização de falantes.
- Revisão visual/timeline dos cortes antes de renderizar.
- Legendas, capítulos ou múltiplas faixas de áudio na saída.
- Aceleração CUDA/GPU no MVP.
- Atualizador automático.
- Empacotamento em instalador `.exe` ou PyInstaller.
- Edição semântica capaz de distinguir automaticamente “tipo”/“assim” legítimos de vícios de fala.
- Crossfade automático. Os cortes são secos no MVP.

## 5. Decisões técnicas fechadas

| Tema | Decisão | Motivo |
|---|---|---|
| GUI | Tkinter + `ttk` | Já vem no Python oficial para Windows e evita dependência visual adicional. |
| Transcrição | `faster-whisper==1.2.1` | Timestamps por palavra, CPU/int8 e execução local. |
| Dispositivo | `device="cpu"`, `compute_type="int8"` | Maior compatibilidade sem CUDA/cuDNN. |
| Idioma | `language="pt"`, `task="transcribe"` | Evita detecção desnecessária e tradução acidental. |
| Modelo grande | `large` na GUI mapeia para `large-v3` | Nome amigável sem deixar a versão interna ambígua. |
| FFmpeg | Binário local validado, com fallback para PATH | Não requer administrador nem alteração global de PATH. |
| FFprobe | Binário local irmão do FFmpeg | Inspeção estruturada em JSON e validação final. |
| Renderização | `trim`/`atrim`, `setpts`/`asetpts`, `concat` | Corta áudio e vídeo na mesma linha de tempo e recomeça PTS em zero. |
| Saída | `libx264`, CRF 20, preset medium, `yuv420p`, AAC 192k | Equilíbrio entre qualidade, compatibilidade e tamanho. |
| Relatório | JSON UTF-8 | Estruturado, legível e testável. |
| Colisão de nome | Parar com erro claro | O requisito proíbe risco de sobrescrita; não numerar nem substituir silenciosamente. |
| Confiança mínima | 0,60 | Política conservadora: perder um vício é melhor que remover fala legítima. |
| União próxima | 0,12 s, salvo se houver palavra protegida | Evita flashes/microsegmentos sem atravessar fala válida. |
| Temporários | Diretório único por execução e arquivo final temporário no destino | Limpeza rastreável e publicação segura no mesmo volume. |
| Modelos | Cache dentro do projeto | Mantém os artefatos operacionais no escopo do projeto. |

Não usar `hotwords`, `initial_prompt` ou `prefix` com a lista de vícios. Isso pode induzir o modelo a produzir exatamente os termos que causam cortes.

## 6. Estrutura final

Arquivos-fonte obrigatórios:

```text
PutzCleaner/
├── PLANO_IMPLEMENTACAO_PUTZCLEANER.md
├── README.md
├── requirements.txt
├── setup.bat
├── abrir_putzcleaner.bat
├── config.json
└── src/
    ├── main.py
    ├── gui.py
    ├── transcriber.py
    ├── cutter.py
    └── report.py
```

Diretórios operacionais que podem ser gerados, mas não devem ser criados manualmente antes de serem necessários:

```text
.venv/                     # ambiente virtual criado pelo setup
models/                    # pesos e cache dos modelos
tools/ffmpeg/bin/          # ffmpeg.exe e ffprobe.exe locais
```

Não adicionar módulos, assets, banco de dados, ícones ou diretórios persistentes de teste sem uma necessidade comprovada e autorização de escopo. Não é necessário `src/__init__.py`, pois o launcher executará `src/main.py` diretamente.

## 7. Arquitetura e dependências entre módulos

```text
main.py
  └── gui.py
       ├── transcriber.py
       ├── cutter.py
       │    └── usa tipos de WordToken de transcriber.py
       └── report.py
            └── recebe objetos/dicionários já calculados
```

Não criar importações no sentido contrário. Em especial:

- `transcriber.py`, `cutter.py` e `report.py` não importam `gui.py`.
- Nenhum módulo de domínio acessa widgets Tkinter.
- O worker chama funções de domínio e emite eventos; somente a thread principal altera a GUI.

Fluxo interno:

```text
GUI captura snapshot imutável das opções
  → valida apenas campos/configuração de forma instantânea
  → worker valida arquivo/toolchain/pasta de saída
  → ffprobe inspeciona a mídia
  → FFmpeg extrai WAV canônico alinhado à linha do tempo do vídeo
  → Transcriber produz WordToken[] a partir do WAV
  → cutter.build_cut_plan produz ocorrências/cortes/keeps
  → cutter.render_video cria MP4 temporário
  → cutter.verify_output valida H.264/AAC/duração
  → report monta JSON temporário
  → publicação sem sobrescrita dos dois arquivos
  → limpeza
```

Estados explícitos do processamento:

```text
IDLE
  → VALIDATING
  → EXTRACTING_AUDIO
  → LOADING_MODEL
  → TRANSCRIBING
  → PLANNING
  → RENDERING
  → VERIFYING
  → REPORTING
  → DONE
```

Qualquer fase pode terminar em `FAILED` ou `CANCELLED`, sempre passando pela limpeza em `finally`.

## 8. Constantes e invariantes

Definir as constantes em um único local coerente, preferencialmente `cutter.py` para regras de corte e `transcriber.py` para ASR:

```python
MODEL_MAP = {
    "small": "small",
    "medium": "medium",
    "large": "large-v3",
}

MIN_WORD_PROBABILITY = 0.60
MIN_WORD_DURATION_SEC = 0.02
MAX_WORD_DURATION_SEC = 3.00
TIMESTAMP_TOLERANCE_SEC = 0.02
MAX_SEGMENT_NO_SPEECH = 0.60
MIN_SEGMENT_AVG_LOGPROB = -1.00
MERGE_GAP_SEC = 0.12
MAX_MARGIN_SEC = 2.00
MAX_TERMS = 200
MAX_TERM_LENGTH = 50
MAX_KEEPS_PER_GRAPH = 100
EPSILON = 1e-6
```

Invariantes que devem ser verificadas com `if` e exceção clara, não apenas comentários:

- Nenhum tempo que entre em cálculo de intervalo é ausente, NaN, infinito ou negativo; tokens brutos podem carregar `None` apenas até serem classificados como ignorados.
- Todo intervalo usa a convenção semiaberta `[início, fim)`.
- Todo intervalo possui `fim > início`.
- Cortes finais estão ordenados, dentro de `[0, duração]` e não se sobrepõem.
- Segmentos preservados estão ordenados, dentro de `[0, duração]` e não se sobrepõem.
- A soma aproximada dos cortes e dos segmentos preservados cobre a duração total.
- A saída resolvida nunca é igual à entrada em comparação case-insensitive do Windows.
- O arquivo final nunca é aberto com opção de sobrescrita.
- Sucesso só ocorre depois de vídeo e relatório existirem e o vídeo ter sido validado.

## 9. `config.json`

Conteúdo inicial exato:

```json
{
  "palavras_removidas": [
    "né",
    "neh",
    "eee",
    "ééé",
    "ã",
    "hã",
    "hum",
    "tipo",
    "assim"
  ],
  "modelo_padrao": "small",
  "margem_antes": 0.05,
  "margem_depois": 0.08,
  "pasta_saida": ""
}
```

Semântica:

- `pasta_saida == ""`: usar a pasta do vídeo de entrada.
- Quando escolhida, `pasta_saida` deve ser salva como caminho absoluto normalizado; não persistir caminho relativo dependente do diretório corrente.
- `modelo_padrao`: somente `small`, `medium` ou `large`.
- Margens: números finitos entre `0` e `2` segundos.
- Palavras: lista não vazia, no máximo 200 entradas, uma palavra/som por linha.

Regras de leitura:

1. Resolver `config.json` a partir de `Path(__file__).resolve().parent.parent`, nunca a partir do diretório corrente.
2. Abrir com UTF-8.
3. Validar cada chave e tipo.
4. Chave ausente ou valor inválido usa o padrão correspondente e gera aviso no log.
5. JSON sintaticamente inválido não é sobrescrito automaticamente. Carregar padrões em memória, mostrar aviso e pedir confirmação antes da primeira tentativa de salvar uma configuração corrigida.
6. Campos desconhecidos podem ser ignorados; ao salvar, escrever somente o schema canônico.

Regras de escrita:

1. Validar todos os valores primeiro.
2. Escrever JSON temporário na mesma pasta, com nome único.
3. Usar `ensure_ascii=False`, `indent=2`, `allow_nan=False` e newline final.
4. Executar `flush` e `os.fsync`.
5. Substituir apenas o `config.json` conhecido pelo aplicativo com `os.replace`.
6. Em erro, apagar somente o temporário criado por essa operação.

Essa substituição é permitida porque o arquivo de configuração pertence ao aplicativo e a gravação é uma ação explícita do usuário. Ela não deve ser usada para vídeo ou relatório final.

## 10. Normalização segura das palavras

Implementar uma única função pública em `transcriber.py`:

```python
def normalize_token(value: str) -> str:
    ...
```

Algoritmo:

1. Exigir `str`.
2. Aplicar Unicode NFC com `unicodedata.normalize("NFC", value)`.
3. Remover espaços externos.
4. Aplicar `casefold()`.
5. Remover somente caracteres de pontuação/símbolos nas extremidades, usando a categoria Unicode; não remover caracteres internos.
6. Remover novamente espaços externos.
7. Não remover acentos.
8. Não colapsar vogais repetidas.
9. Não usar substring, regex aproximada ou fuzzy matching.

Exemplos obrigatórios:

```text
" Né, "  → "né"
"NEH!"   → "neh"
"hum..." → "hum"
"ã"      ≠ "a"
"hã"     ≠ "ha"
"ééé"    ≠ "é"
"hum"    não corresponde a "humano"
"tipo"   não corresponde a "tipologia"
```

Motivo da preservação de acentos e repetições:

- Se `ã` fosse normalizado para `a`, o programa poderia cortar o artigo/preposição “a” por toda a entrevista.
- Se `ééé` fosse colapsado para `é`, o programa poderia cortar o verbo “é”.

Validação da lista editável:

- Uma entrada por linha.
- Remover linhas vazias.
- Rejeitar uma entrada com whitespace interno; mensagem: `Use apenas uma palavra ou som por linha.`
- Rejeitar entrada que normalize para vazio.
- Rejeitar mais de 50 caracteres.
- Deduplicar pela forma normalizada, preservando a primeira forma e a ordem.
- Permitir lista padrão exatamente como especificada, inclusive `eee` e `ééé` como alvos distintos.

## 11. Modelo de dados

Usar `@dataclass(frozen=True)` para dados imutáveis que atravessam fases.

### 11.1 Em `transcriber.py`

```python
@dataclass(frozen=True)
class WordToken:
    text: str
    normalized: str
    start: float | None
    end: float | None
    probability: float | None
    segment_id: int
    segment_avg_logprob: float | None
    segment_no_speech_prob: float | None

@dataclass(frozen=True)
class TranscriptionResult:
    words: tuple[WordToken, ...]
    audio_duration: float
    language: str
    language_probability: float
    model_requested: str
    model_resolved: str
```

### 11.2 Em `src/cutter.py`

```python
@dataclass(frozen=True)
class Toolchain:
    ffmpeg: Path
    ffprobe: Path
    ffmpeg_version: str
    ffprobe_version: str
    filter_file_option: str

@dataclass(frozen=True)
class MediaStream:
    global_index: int
    codec_type: str
    codec_name: str
    start_time: float | None
    duration: float | None
    attached_picture: bool

@dataclass(frozen=True)
class MediaInfo:
    timeline_duration: float
    format_duration: float | None
    format_start_time: float | None
    video_stream: MediaStream
    audio_stream: MediaStream
    width: int
    height: int
    fps: float | None

@dataclass(frozen=True)
class IgnoredOccurrence:
    text: str
    normalized: str
    start: float | None
    end: float | None
    probability: float | None
    reason: str

@dataclass(frozen=True)
class CutOccurrence:
    configured_term: str
    recognized_text: str
    word_start: float
    word_end: float
    probability: float
    candidate_start: float
    candidate_end: float

@dataclass(frozen=True)
class CutInterval:
    id: int
    start: float
    end: float
    occurrence_indexes: tuple[int, ...]

@dataclass(frozen=True)
class KeepInterval:
    start: float
    end: float

@dataclass(frozen=True)
class CutPlan:
    occurrences: tuple[CutOccurrence, ...]
    ignored: tuple[IgnoredOccurrence, ...]
    cuts: tuple[CutInterval, ...]
    keeps: tuple[KeepInterval, ...]
    expected_output_duration: float

@dataclass(frozen=True)
class RenderResult:
    staged_video: Path
    actual_duration: float
    video_codec: str
    audio_codec: str
```

### 11.3 Em `gui.py`

```python
@dataclass(frozen=True)
class ProcessingOptions:
    input_video: Path
    output_directory: Path
    terms: tuple[str, ...]
    model: str
    margin_before: float
    margin_after: float
```

O worker recebe um `ProcessingOptions` pronto. Ele nunca lê diretamente `StringVar`, `Entry` ou `Text`.

## 12. `src/main.py`

Responsabilidades exclusivas:

1. Calcular `PROJECT_ROOT`.
2. Configurar variáveis de cache local antes de importar `faster_whisper` indiretamente:
   - `HF_HOME=<projeto>\models\.hf`
   - `HF_HUB_CACHE=<projeto>\models\.hf\hub`
   - `HF_HUB_DISABLE_TELEMETRY=1`
3. Importar e iniciar `PutzCleanerApp`.
4. Criar exatamente uma instância de `tk.Tk`.
5. Capturar erro fatal de inicialização.
6. Quando executado por `pythonw.exe`, mostrar erro fatal com `tkinter.messagebox` ou, se Tk não inicializar, `ctypes.windll.user32.MessageBoxW`.
7. Retornar exit code diferente de zero em erro fatal.

Não colocar transcrição, FFmpeg, configuração ou construção detalhada de widgets em `main.py`.

Proteger a entrada com:

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

## 13. `src/transcriber.py`

### 13.1 Classe principal

```python
class Transcriber:
    def __init__(self, model_directory: Path) -> None: ...

    def transcribe(
        self,
        canonical_audio_path: Path,
        timeline_duration: float,
        requested_model: str,
        cancel_event: threading.Event,
        log_callback: Callable[[str], None],
        progress_callback: Callable[[float], None],
    ) -> TranscriptionResult: ...
```

Manter no máximo um modelo em cache na instância. Se o usuário trocar o modelo:

1. descartar a referência anterior;
2. executar `gc.collect()` apenas depois de remover a referência;
3. carregar o novo modelo;
4. se faltar RAM, mostrar erro e sugerir `small` ou `medium`; não fazer downgrade silencioso.

### 13.2 Carregamento

```python
WhisperModel(
    MODEL_MAP[requested_model],
    device="cpu",
    compute_type="int8",
    download_root=str(model_directory),
)
```

O primeiro uso pode baixar pesos do Hugging Face. Isso não envia áudio/vídeo e não usa API paga, mas exige internet inicialmente. O log deve dizer:

```text
Carregando o modelo small. No primeiro uso, o download pode demorar.
```

Não inventar porcentagem de download se a biblioteca não fornecer callback.

### 13.3 Transcrição

Chamada exata de referência:

```python
segments, info = model.transcribe(
    str(canonical_audio_path),
    language="pt",
    task="transcribe",
    beam_size=5,
    temperature=0.0,
    word_timestamps=True,
    vad_filter=True,
    condition_on_previous_text=False,
)
```

Regras:

- Consumir `segments` em um `for`; ele é gerador e a inferência só acontece durante a iteração.
- Exigir que `info.duration` seja finito e esteja a no máximo 0,05 s de `timeline_duration`; divergência indica WAV inválido e interrompe com segurança.
- Checar `cancel_event.is_set()` antes de carregar, após carregar e a cada segmento.
- Para cada segmento, copiar seus metadados para cada `WordToken`.
- Sanitizar `start`, `end`, `probability`, `avg_logprob` e `no_speech_prob` com uma função que rejeite `bool`, conversão inválida, NaN e infinito. Valores ruins viram `None`; não ordenar nem calcular com eles.
- Se `segment.words is None`, registrar e continuar.
- Não salvar a transcrição textual completa em arquivo.
- Guardar palavras em memória; dezenas de milhares de tokens são aceitáveis e necessários para proteger vizinhas.
- Atualizar progresso pela maior razão temporal válida observada `segment.end / timeline_duration`, limitada a `[0, 1]`.
- Progresso deve ser monotônico.
- Deduplicar apenas duplicatas evidentes na fronteira de segmentos: mesma forma normalizada e timestamps inicial/final dentro de 20 ms; manter a de maior probabilidade. Não deduplicar palavras diferentes sobrepostas.

O transcritor não deve receber diretamente o MP4. Ele recebe o WAV canônico criado pelo worker conforme a seção 14.4. Isso evita que a decodificação do faster-whisper descarte PTS, comprima gaps internos ou escolha uma linha do tempo diferente da usada nos cortes.

## 14. Inspeção e validação da mídia em `cutter.py`

### 14.1 Descoberta do FFmpeg

Ordem:

1. `<PROJECT_ROOT>\tools\ffmpeg\bin\ffmpeg.exe` e `ffprobe.exe`.
2. Diretórios retornados pelos executáveis encontrados no PATH, testados como pares.

Não misturar instalações: `ffmpeg.exe` e `ffprobe.exe` devem existir no mesmo diretório resolvido. Testar candidatos por diretório e rejeitar qualquer par misto.

### 14.2 Validação do toolchain

Executar com lista de argumentos, `shell=False`, timeout de 10 segundos, UTF-8 com substituição de caracteres inválidos e `CREATE_NO_WINDOW` no Windows:

```text
ffmpeg -version
ffprobe -version
ffmpeg -hide_banner -encoders
```

Confirmar:

- exit code zero;
- encoder exato `libx264`;
- encoder exato `aac`;
- pelo menos uma forma funcional de carregar filtergraph por arquivo.

Não escolher a opção apenas procurando texto em `-h full`. Criar um filtergraph mínimo temporário e executar um comando `lavfi` de 0,1 s primeiro com `-/filter_complex`. Se retornar zero, guardar essa opção. Caso contrário, repetir com `-filter_complex_script`. Se ambas falharem, rejeitar o toolchain. Limpar o teste temporário em `finally`.

Guardar em `Toolchain.filter_file_option`:

```text
"-/filter_complex"        se disponível
"-filter_complex_script"  caso contrário
```

Erro ao usuário:

```text
FFmpeg/ffprobe não foi encontrado ou não possui suporte a H.264/AAC.
Execute novamente o arquivo setup.bat. Nenhum arquivo original foi alterado.
```

### 14.3 Probe do vídeo

Comando:

```text
ffprobe -v error -print_format json -show_format -show_streams <entrada>
```

Validar o JSON e obter:

- duração positiva e finita;
- streams;
- codec e tipo;
- largura/altura;
- `avg_frame_rate` com divisão racional segura;
- `start_time`;
- `disposition.attached_pic`.

Escolha de streams:

- vídeo: primeiro stream de vídeo que não seja imagem de capa anexada;
- áudio: primeiro stream de áudio; o mesmo índice global será usado na extração canônica e na renderização;
- sem vídeo ou sem áudio: falhar claramente;
- streams extras: ignorar e documentar.

Definir uma única duração canônica:

1. usar a duração positiva/finita do stream de vídeo selecionado;
2. se ela estiver ausente, usar `format.duration` positiva/finita;
3. armazenar o resultado em `MediaInfo.timeline_duration`;
4. usar esse mesmo valor na extração, validação dos tokens, plano de cortes, filtergraph, progresso, relatório e verificação final.

Para comparar início de streams, usar `stream.start_time` finito; se ausente, usar `format.start_time`; se todos os inícios estiverem ausentes, assumir zero para ambos e incluir aviso no relatório. Nunca calcular com `None`, NaN ou infinito.

Se a diferença absoluta entre `start_time` do vídeo e do áudio for maior que 0,05 s, interromper em vez de arriscar cortes dessincronizados:

```text
O vídeo possui deslocamento incomum entre áudio e imagem e não pode ser processado com segurança nesta versão.
```

### 14.4 Extração de áudio com linha do tempo canônica

Depois do probe e antes de carregar o modelo, o worker deve criar `audio_canonico.wav` dentro do diretório temporário da execução. Usar explicitamente o stream global escolhido:

```text
ffmpeg -hide_banner -nostdin -n
  -i <entrada>
  -map 0:<AUDIO_INDEX>
  -vn
  -af aresample=16000:async=1:first_pts=0,apad,atrim=start=0:end=<TIMELINE_DURATION>,asetpts=PTS-STARTPTS
  -ac 1 -ar 16000 -c:a pcm_s16le
  -progress pipe:1 -nostats
  <audio_canonico.wav>
```

Essa etapa:

- preserva gaps de timestamp como silêncio por meio do resample assíncrono;
- normaliza o início em zero;
- corta ou preenche o áudio exatamente até `timeline_duration`;
- fixa mono/16 kHz/PCM para a transcrição;
- garante que ASR e cutter compartilhem a mesma duração.

Validar o WAV com ffprobe antes de transcrever e exigir duração dentro de 0,05 s da `timeline_duration`. O arquivo pode ocupar aproximadamente 115 MB por hora e deve ser apagado no `finally`. Se a extração falhar, não carregar o modelo nem publicar saída.

## 15. Critério de ocorrência confiável

O faster-whisper fornece `Word.probability`, mas não uma métrica calibrada específica para confiança do timestamp. O programa usa uma combinação conservadora de probabilidade, validade temporal e metadados do segmento.

Uma palavra-alvo é aceita somente se todos os itens forem verdadeiros:

1. `normalized` está no conjunto exato de alvos.
2. `start`, `end` e `probability` existem.
3. Não são booleanos e podem ser convertidos para `float`.
4. São finitos.
5. `probability >= 0.60`.
6. `end > start`.
7. Duração da palavra entre 0,02 e 3,00 s.
8. `segment_no_speech_prob` e `segment_avg_logprob` existem e são finitos.
9. `segment_no_speech_prob <= 0.60`.
10. `segment_avg_logprob >= -1.00`.
11. Os timestamps não excedem os limites em mais de 20 ms.
12. O núcleo da palavra não se sobrepõe a uma palavra protegida.

Clamping permitido:

- `start` entre `-0,02` e `0` pode virar `0`.
- `end` entre `duração` e `duração + 0,02` pode virar `duração`.
- Desvio maior é ignorado e relatado; não adivinhar.

Palavras protegidas:

- qualquer palavra não alvo com timestamps básicos válidos;
- qualquer alvo rejeitado por baixa confiança ou metadados ruins, se seus timestamps básicos forem válidos;
- qualquer outro token transcrito com sobreposição temporal.

O MVP não possui diarização e não sabe identificar falantes. A proteção é puramente temporal; ela reduz o risco, mas não garante detectar toda fala simultânea de outra pessoa.

Uma ocorrência ignorada deve entrar em `IgnoredOccurrence` com razão canônica, por exemplo:

```text
probabilidade_ausente
baixa_confianca
timestamp_ausente
timestamp_nao_finito
timestamp_fora_do_video
duracao_invalida
segmento_inseguro
sobreposicao_com_palavra_protegida
margem_eliminou_o_alvo
```

## 16. Algoritmo completo de planejamento dos cortes

Assinatura:

```python
def build_cut_plan(
    words: Sequence[WordToken],
    configured_terms: Sequence[str],
    timeline_duration: float,
    margin_before: float,
    margin_after: float,
) -> CutPlan:
    ...
```

### 16.1 Preparação

1. Validar `timeline_duration` e margens.
2. Normalizar/deduplicar os termos.
3. Antes de ordenar, converter para uma visão sanitizada: aplicar os clamps externos de até 20 ms e separar todo token com `start`/`end` ausente, não conversível, booleano, NaN, infinito ou ainda fora dos limites.
4. Se um token inválido for um alvo, criar `IgnoredOccurrence` com a razão correspondente; token inválido não participa de ordenação nem proteção.
5. Ordenar somente a visão de tokens temporalmente válidos por `(start, end, segment_id)` sem arredondar.
6. Separar palavras com formato temporal básico válido para proteção.

### 16.2 Avaliação dos alvos

Para cada palavra cuja forma normalizada seja alvo:

1. Aplicar o critério de confiança.
2. Procurar qualquer palavra protegida que intersecte o núcleo `[word.start, word.end)`.
3. Qualquer sobreposição real maior que `EPSILON` torna a ocorrência insegura e deve ser ignorada. Os 20 ms servem apenas para clamp dos limites externos do vídeo, não para autorizar sobreposição com outra palavra.
4. Calcular:

```python
candidate_start = max(0.0, word.start - margin_before)
candidate_end = min(timeline_duration, word.end + margin_after)
```

5. Proteger a palavra válida imediatamente anterior:

```python
candidate_start = max(candidate_start, previous_protected.end)
```

A palavra anterior precisa satisfazer `previous_protected.end <= word.start + EPSILON`; não usar um token sobreposto como “vizinho”.

6. Proteger a palavra válida imediatamente posterior:

```python
candidate_end = min(candidate_end, next_protected.start)
```

A palavra posterior precisa satisfazer `next_protected.start >= word.end - EPSILON`.

7. Se essa proteção passar para dentro do núcleo do alvo além de `EPSILON`, ignorar a ocorrência em vez de cortar parcialmente uma palavra incerta.
8. Revalidar que o candidato inteiro não intersecta nenhuma palavra protegida. Se intersectar, ignorar a ocorrência.
9. Adicionar `CutOccurrence`.

### 16.3 União dos candidatos

Ordenar por `(candidate_start, candidate_end)`.

Pseudocódigo:

```python
merged = []
current = first_candidate

for next_candidate in remaining:
    union_start = min(current.start, next_candidate.start)
    union_end = max(current.end, next_candidate.end)
    near_or_overlapping = next_candidate.start <= current.end + MERGE_GAP_SEC
    union_is_safe = not any_protected_word_intersects(
        union_start,
        union_end,
    )

    if near_or_overlapping and union_is_safe:
        current = union(current, next_candidate)
    else:
        if next_candidate.start < current.end - EPSILON:
            raise UnsafeCutPlanError(
                "Candidatos sobrepostos entraram em conflito com fala protegida"
            )
        merged.append(current)
        current = next_candidate

merged.append(current)
```

Nunca unir por cima de uma palavra protegida, mesmo quando o gap é menor que 0,12 s. A etapa de candidatos deve eliminar previamente toda interseção. Se, apesar dessa invariante, dois candidatos finais ainda se sobrepuserem e a união for insegura, falhar conservadoramente sem renderizar; nunca conservar cortes finais sobrepostos nem escolher um corte arbitrariamente.

Após a união:

- limitar novamente a `[0, duração]`;
- eliminar somente intervalo com duração `<= EPSILON`;
- atribuir IDs começando em 1;
- associar cada ocorrência ao corte final que a contém.

### 16.4 Complemento preservado

```python
keeps = []
cursor = 0.0

for cut in cuts:
    if cut.start > cursor + EPSILON:
        keeps.append(KeepInterval(cursor, cut.start))
    cursor = max(cursor, cut.end)

if cursor < timeline_duration - EPSILON:
    keeps.append(KeepInterval(cursor, timeline_duration))
```

Regras finais:

- Zero ocorrências/cortes: `keeps = [(0, duração)]`; ainda renderizar H.264/AAC e relatório com zero.
- Zero segmentos preservados: abortar com `Os cortes calculados removeriam todo o vídeo.`
- `expected_output_duration = sum(keep.end - keep.start)`.
- Não arredondar durante cálculos.
- Usar seis casas decimais no filtergraph e três casas no relatório.
- `total_cortes` é a quantidade de intervalos finais unidos.
- `total_ocorrencias` é a quantidade de palavras-alvo aceitas. Os totais podem ser diferentes.

## 17. Renderização FFmpeg

### 17.1 Por que reencodar

Não usar `-c copy` para os cortes principais. Timestamps de palavras raramente coincidem com keyframes; stream copy criaria cortes imprecisos. Vídeo e áudio devem ser filtrados e reencodados.

### 17.2 Filtergraph para até 100 segmentos preservados

Normalizar PTS antes de dividir, dividir em `K` branches, cortar cada keep e zerar seus timestamps.

Exemplo conceitual para dois keeps:

```text
[0:VIDEO_INDEX]setpts=PTS-STARTPTS,split=outputs=2[vsrc0][vsrc1];
[0:AUDIO_INDEX]aresample=async=1:first_pts=0,apad,atrim=start=0:end=<TIMELINE_DURATION>,asetpts=PTS-STARTPTS,asplit=outputs=2[asrc0][asrc1];
[vsrc0]trim=start=0.000000:end=10.000000,setpts=PTS-STARTPTS[v0];
[asrc0]atrim=start=0.000000:end=10.000000,asetpts=PTS-STARTPTS[a0];
[vsrc1]trim=start=10.500000:end=30.000000,setpts=PTS-STARTPTS[v1];
[asrc1]atrim=start=10.500000:end=30.000000,asetpts=PTS-STARTPTS[a1];
[v0][a0][v1][a1]concat=n=2:v=1:a=1[vcat][acat];
[vcat]pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p[vout];
[acat]anull[aout]
```

Para um único keep, não usar `split/asplit` nem `concat`; ligar o `trim/atrim` diretamente às saídas finais.

O `pad` adiciona no máximo um pixel para tornar dimensões ímpares compatíveis com `yuv420p`/libx264.

A cadeia inicial do áudio deve repetir a normalização temporal usada no WAV (sem forçar mono/16 kHz na saída): resample assíncrono, padding/truncamento até a duração canônica e PTS iniciado em zero. Assim, os timestamps encontrados no WAV são aplicados à mesma linha do tempo usada na renderização.

Gravar o filtergraph em arquivo ASCII/UTF-8 dentro do diretório temporário. Isso evita o limite de linha de comando do Windows.

Argumentos de referência, sempre como lista Python e `shell=False`:

```text
ffmpeg
-hide_banner
-nostdin
-n
-i <entrada>
<filter_file_option> <arquivo_filtergraph>
-map [vout]
-map [aout]
-c:v libx264
-preset medium
-crf 20
-pix_fmt yuv420p
-c:a aac
-b:a 192k
-movflags +faststart
-map_metadata -1
-map_chapters -1
-abort_on empty_output
-progress pipe:1
-nostats
<mp4_temporario>
```

Não inserir caminhos dentro do filtergraph; somente índices e números. Assim, nomes com espaços, acentos, `&` e parênteses permanecem seguros.

### 17.3 Rota em lotes para mais de 100 keeps

Um `split/asplit` com centenas de saídas degrada memória e CPU. A implementação para entrevistas longas deve possuir fallback em lotes, não apenas um comentário.

Algoritmo:

1. Particionar `keeps` cronologicamente em lotes de no máximo 100.
2. Criar um `TemporaryDirectory` único dentro da pasta de saída.
3. Para cada lote:
   - `batch_start = primeiro_keep.start`;
   - `batch_end = ultimo_keep.end`;
   - abrir somente essa janela com `-ss batch_start -t (batch_end - batch_start)` antes de `-i`;
   - converter keeps do lote para tempos relativos subtraindo `batch_start`;
   - usar `(batch_end - batch_start)` como duração da normalização de áudio desse lote, nunca a duração global;
   - gerar o mesmo filtergraph limitado;
   - renderizar `batch_0000.mkv`, `batch_0001.mkv`, etc.;
   - vídeo intermediário H.264 com os mesmos parâmetros finais;
   - áudio intermediário FLAC, para evitar múltiplas perdas/atrasos AAC.
4. Validar cada lote antes de continuar.
5. Criar no mesmo diretório um manifesto com somente nomes relativos seguros:

```text
file 'batch_0000.mkv'
file 'batch_0001.mkv'
```

6. Executar a concatenação final com `cwd` apontando para o diretório temporário:

```text
ffmpeg -hide_banner -nostdin -n
  -f concat -safe 1 -i batches.txt
  -map 0:v:0 -map 0:a:0
  -c:v copy
  -c:a aac -b:a 192k
  -movflags +faststart
  -map_metadata -1 -map_chapters -1
  -progress pipe:1 -nostats
  <mp4_temporario_absoluto>
```

7. Validar o MP4 final.
8. Fechar todos os handles e subprocessos.
9. Apagar manifesto, filtergraphs e lotes no `finally` por meio do dono do diretório temporário.

Antes de considerar a rota pronta, executar teste sintético com mais de 100 keeps e verificar sincronismo, duração, codecs e limpeza. Se essa rota não for implementada/testada, registrar explicitamente que entrevistas com muitos cortes ainda não são suportadas; não alegar suporte completo a vídeos longos.

### 17.4 Progresso do FFmpeg

Usar `-progress pipe:1`. O stdout contém blocos `chave=valor` e termina cada bloco com `progress=continue` ou `progress=end`.

- Preferir `out_time_us`.
- Interpretar `out_time_us` e o legado `out_time_ms` como microssegundos; apesar do nome histórico, não tratar `out_time_ms` como milissegundos. Usar `out_time` (`HH:MM:SS.microseconds`) como fallback.
- Dividir pela duração esperada da saída ou do lote.
- Na rota em lotes, calcular o progresso global ponderado como `(soma_duracoes_lotes_concluidos + out_time_lote_atual) / soma_duracoes_de_todos_os_lotes`; reservar a parte final da faixa para a concatenação. Nunca reiniciar a barra em cada lote nem deixá-la chegar ao teto no primeiro lote.
- Limitar a `[0, 1]` e manter monotônico.
- Drenar stderr em uma segunda thread leve para não bloquear o pipe.
- Guardar somente as últimas 100 linhas técnicas para erro; não despejar milhares de linhas na GUI.

### 17.5 Cancelamento do subprocesso

Ao receber cancelamento:

1. chamar `terminate()` somente no processo filho FFmpeg;
2. aguardar até 5 segundos;
3. se ainda estiver vivo, chamar `kill()` somente nesse PID;
4. drenar/fechar pipes;
5. limpar os temporários registrados;
6. nunca matar processos por nome.

## 18. Saída, colisões e transação de publicação

### 18.1 Nomes

Para `entrevista.mp4`:

```text
entrevista_limpo.mp4
entrevista_limpo_relatorio.json
```

Usar `Path.stem`; extensão de saída sempre `.mp4` minúscula.

### 18.2 Pré-validação

Antes da transcrição:

- resolver entrada e saída com `Path.resolve`;
- comparar com `os.path.normcase`;
- confirmar que são diferentes;
- confirmar que pasta de saída existe e é diretório;
- testar escrita com um arquivo temporário exclusivo e apagar somente esse arquivo;
- se vídeo ou relatório final já existir, parar antes do trabalho pesado.

Mensagem:

```text
Já existe um arquivo de saída com esse nome. Nada foi sobrescrito.
Renomeie/mova o arquivo existente ou escolha outra pasta de saída.
```

Não criar `_2`, não perguntar para substituir e não usar `-y`.

### 18.3 Staging

- MP4 temporário: `.putzcleaner-<uuid>.mp4`, na pasta de saída e ainda inexistente.
- JSON temporário: `.putzcleaner-<uuid>.json`, na mesma pasta.
- Diretório de trabalho: `.putzcleaner-<uuid>/`, criado pelo `TemporaryDirectory`.
- Usar `-n` inclusive nos temporários; UUID torna colisão improvável e `-n` torna a proteção real.

### 18.4 Verificação antes de publicar

Com ffprobe, exigir:

- arquivo existe e tem tamanho maior que zero;
- há vídeo `h264`;
- há áudio `aac`;
- duração positiva;
- diferença para duração esperada no máximo `max(0,5 s, 2 frames)`;
- FFmpeg retornou zero e terminou com `progress=end` quando disponível.

### 18.5 Publicação sem sobrescrita

Como o alvo é Windows e temporários ficam no mesmo volume:

1. revalidar que os dois nomes finais não existem;
2. publicar o vídeo com `os.rename`, que deve falhar se o destino existir;
3. publicar o relatório por último; ele funciona como marcador de commit da operação;
4. se a publicação do relatório falhar, tentar remover somente o vídeo que esta execução acabou de publicar; antes, conferir que identidade/estatísticas ainda correspondem ao staged renomeado, evitando apagar um arquivo trocado por outra operação;
5. se esse rollback também falhar, informar falha parcial e o caminho exato do vídeo órfão; não mostrar sucesso;
6. reconhecer no código/comentários que dois renames não formam uma transação atômica real;
7. sucesso apenas quando ambos estiverem nos nomes finais;
8. nunca usar `os.replace` para vídeo ou relatório final.

Rastrear a propriedade de cada caminho em variáveis booleanas para a limpeza nunca apagar um arquivo anterior do usuário.

## 19. Relatório JSON em `src/report.py`

Funções sugeridas:

```python
def build_report(...) -> dict[str, object]: ...

def write_report_staged(
    destination: Path,
    payload: Mapping[str, object],
) -> None: ...
```

Schema mínimo:

```json
{
  "schema_version": 1,
  "status": "concluido",
  "gerado_em": "2026-07-09T23:59:00-03:00",
  "arquivo_original": "C:\\videos\\entrevista.mp4",
  "arquivo_gerado": "C:\\videos\\entrevista_limpo.mp4",
  "configuracao": {
    "palavras_removidas": ["né", "hum"],
    "modelo_selecionado": "small",
    "modelo_resolvido": "small",
    "idioma": "pt",
    "margem_antes": 0.05,
    "margem_depois": 0.08,
    "limiar_confianca": 0.6,
    "distancia_uniao": 0.12
  },
  "midia": {
    "duracao_formato_original": 60.0,
    "duracao_timeline": 60.0,
    "duracao_saida_esperada": 59.5,
    "duracao_saida_real": 59.52,
    "codec_video": "h264",
    "codec_audio": "aac"
  },
  "resumo": {
    "total_ocorrencias": 2,
    "total_cortes": 1,
    "duracao_total_removida": 0.5
  },
  "ocorrencias": [
    {
      "palavra_removida": "né",
      "palavra_configurada": "né",
      "texto_reconhecido": " né,",
      "timestamp_inicial": 12.34,
      "timestamp_final": 12.61,
      "confianca": 0.93,
      "corte_id": 1,
      "candidato_inicio": 12.29,
      "candidato_fim": 12.69,
      "corte_final_inicio": 12.29,
      "corte_final_fim": 12.79
    },
    {
      "palavra_removida": "hum",
      "palavra_configurada": "hum",
      "texto_reconhecido": " hum",
      "timestamp_inicial": 12.65,
      "timestamp_final": 12.71,
      "confianca": 0.88,
      "corte_id": 1,
      "candidato_inicio": 12.60,
      "candidato_fim": 12.79,
      "corte_final_inicio": 12.29,
      "corte_final_fim": 12.79
    }
  ],
  "cortes": [
    {
      "id": 1,
      "inicio": 12.29,
      "fim": 12.79,
      "duracao": 0.5
    }
  ],
  "ignorados": {
    "total": 1,
    "por_motivo": {
      "baixa_confianca": 1
    },
    "itens": [
      {
        "texto_reconhecido": " hum",
        "timestamp_inicial": 20.1,
        "timestamp_final": 20.4,
        "confianca": 0.42,
        "motivo": "baixa_confianca"
      }
    ]
  },
  "ferramentas": {
    "faster_whisper": "1.2.1",
    "ffmpeg": "8.1.2"
  },
  "avisos": []
}
```

Regras:

- Timestamps do relatório sempre se referem ao vídeo original.
- `duracao_timeline` é a duração canônica usada por ASR, cortes, progresso e verificação; `duracao_formato_original` preserva o valor bruto do contêiner para diagnóstico.
- `total_cortes` conta intervalos unidos, não palavras.
- Arredondar valores exibidos para três casas; calcular com precisão completa.
- Incluir ocorrências com palavra, timestamp inicial, timestamp final e confiança.
- `palavra_removida` é a chave literal exigida para a palavra efetivamente aceita; `palavra_configurada` registra o alvo normalizado e `texto_reconhecido` preserva a saída bruta do ASR.
- `candidato_inicio/fim` representa a margem individual; `corte_final_inicio/fim` deve repetir exatamente os limites do `corte_id` após a união.
- Incluir razões de alvos ignorados para auditoria.
- `allow_nan=False` para nunca produzir JSON inválido.
- Não criar relatório final em falha/cancelamento; o erro fica apenas no log da sessão.

## 20. `src/gui.py`

### 20.1 Janela

- Uma única janela.
- Título da janela: `PutzCleaner`.
- Tamanho inicial aproximado: `900x720`.
- Tamanho mínimo: `760x600`.
- Grid responsivo; coluna do caminho e logs expande.
- Fonte padrão do sistema; título maior em negrito.
- Não criar múltiplas instâncias de `Tk`.

### 20.2 Ordem visual

1. Título `PutzCleaner`.
2. Subtítulo `Removedor automático de vícios de fala para entrevistas`.
3. Linha do vídeo:
   - botão `Selecionar vídeo`;
   - campo readonly com caminho completo.
4. Linha de saída, adição justificada pelo requisito de salvar `pasta_saida`:
   - campo readonly;
   - botão `Escolher pasta`;
   - vazio mostrado como `Mesma pasta do vídeo`.
5. `LabelFrame` da lista:
   - instrução `Uma palavra ou som por linha`;
   - `Text` editável com scrollbar.
6. Opções:
   - combobox readonly `small`, `medium`, `large`;
   - `Margem antes (s)`;
   - `Margem depois (s)`.
7. Botão principal `Processar vídeo`.
8. Label de status.
9. Barra de progresso.
10. Área de logs readonly com scrollbar.

Não adicionar preview, tema complexo, login, menus, abas ou botões sem necessidade.

### 20.3 Seleção e validação

File dialog:

```text
Arquivos MP4 (*.mp4)
Todos os arquivos (*.*)
```

Antes do worker, executar somente validações instantâneas de conteúdo dos widgets:

- vídeo selecionado;
- sufixo `.mp4` case-insensitive;
- modelo permitido;
- lista válida e não vazia;
- margens aceitam `0.05` e `0,05`;
- margens finitas, entre 0 e 2;

Depois de iniciar o worker, ainda na fase `VALIDATING`, executar fora da thread Tkinter:

- existência, tipo, leitura e tamanho do arquivo;
- existência e escrita da pasta de saída;
- teste temporário de escrita;
- colisões de saída, com nova verificação imediatamente antes da publicação;
- descoberta e validação de FFmpeg/ffprobe;
- ffprobe, streams, offsets e duração canônica;
- extração/validação do WAV.

Converter vírgula decimal substituindo uma única vírgula por ponto antes de `float`. Rejeitar texto misto, NaN e infinito.

### 20.4 Worker e fila

A thread principal:

1. lê widgets;
2. executa apenas as validações instantâneas descritas acima;
3. salva configuração;
4. cria `ProcessingOptions` imutável;
5. desativa controles;
6. cria `cancel_event`;
7. inicia `threading.Thread(daemon=False)`;
8. agenda `_drain_events` com `root.after(100, ...)`.

Criar a fila explicitamente com `queue.Queue`; não implementar uma fila caseira nem compartilhar listas mutáveis sem sincronização.

O worker:

- nunca toca em widget, `StringVar`, `messagebox` ou `after`;
- realiza todas as operações de filesystem potencialmente lentas, subprocessos de validação, ffprobe e extração de áudio;
- executa o fluxo de domínio;
- captura exceções conhecidas e inesperadas;
- envia eventos estruturados;
- sempre envia `done` no `finally`.

Eventos mínimos:

```text
log(message)
status(message)
progress_mode("determinate" | "indeterminate")
progress(value_0_to_100)
success(video_path, report_path, occurrences, cuts)
error(user_message, technical_detail)
done()
```

O poll da GUI:

- drena no máximo 100 eventos por tick;
- mantém a GUI responsiva se houver muitos logs;
- limita logs visuais às últimas 1.000 linhas;
- mostra messagebox somente ao receber `success` ou `error` na thread principal;
- reativa controles ao receber `done`.

### 20.5 Progresso por fases

```text
0–5%    validações no worker
5–10%   extração/validação do WAV canônico
10–18%  carga/download do modelo (indeterminado se necessário)
18–60%  transcrição
60–65%  cálculo dos cortes
65–97%  renderização
97–99%  verificação e relatório
100%    publicação concluída
```

Nunca simular progresso de download. Durante modo indeterminado, mostrar apenas a atividade e a etapa.

### 20.6 Fechamento durante processamento

Ao receber `WM_DELETE_WINDOW` com worker ativo:

1. perguntar `Há um processamento em andamento. Deseja cancelar e sair?`;
2. se não, continuar;
3. se sim, setar `cancel_event`;
4. desativar a janela e mostrar `Cancelando com segurança...`;
5. esperar o worker limpar e enviar `done`;
6. só então destruir a janela.

Download/carregamento do modelo e uma chamada de inferência dentro de um chunk podem não cancelar imediatamente. Documentar isso; não destruir a raiz e abandonar o subprocesso.

### 20.7 Mensagem final

```text
Processamento concluído.

Vídeo: C:\...\entrevista_limpo.mp4
Relatório: C:\...\entrevista_limpo_relatorio.json
```

Registrar os mesmos caminhos no log.

## 21. `requirements.txt`

Conteúdo inicial mínimo:

```text
faster-whisper==1.2.1
```

Tkinter não entra no pip; ele deve vir da instalação oficial do Python. FFmpeg não entra no pip porque será validado/instalado localmente pelo setup.

Não usar versão aberta `>=`. Se a implementação ocorrer muito depois de 09/07/2026 e a versão não estiver disponível, não escolher uma nova silenciosamente: verificar documentação oficial, testar e registrar a atualização no plano/README.

## 22. `setup.bat`

### 22.1 Princípios

- Executável por duplo clique.
- Sem administrador.
- Sem Winget, Chocolatey ou alteração global de PATH.
- Não instalar Python automaticamente fora do projeto.
- Criar somente `.venv`, `models` e `tools/ffmpeg` dentro do projeto.
- Idempotente: segunda execução reutiliza o que é válido.
- Toda etapa crítica testa `errorlevel`.
- Mensagens em português.
- Caminhos sempre entre aspas.
- Usar `setlocal EnableExtensions DisableDelayedExpansion`.
- Usar `cd /d "%~dp0"` ou `pushd "%~dp0"`.
- Ativar `chcp 65001 >nul`.

### 22.2 Python

Versão de referência: CPython 3.11 x64.

Ordem:

1. tentar `py -3.11`;
2. tentar `python` como segundo candidato;
3. para **todo** candidato, inclusive o retornado por `py`, executar uma única checagem de versão 3.11, CPython, arquitetura 64 bits, `import tkinter` e `import venv`;
4. obter `sys.executable`, normalizá-lo como caminho absoluto e usar esse executável em todas as etapas seguintes; não guardar um comando ambíguo;
5. se nenhum candidato passar, mostrar:

```text
Python 3.11 de 64 bits com Tkinter não foi encontrado.
Instale o Python pelo site oficial e execute setup.bat novamente.
```

Python é o único pré-requisito do sistema. Depois que ele estiver instalado e o setup terminar, o usuário não precisará executar comandos manuais.

### 22.3 Ambiente virtual

1. Se `.venv\Scripts\python.exe` não existir, criar com `python -m venv .venv`.
2. Se `.venv` parcial possuir `pyvenv.cfg` reconhecível e pertencer ao projeto, tentar repará-la com o Python selecionado por `python -m venv --upgrade .venv`; não apagar recursivamente.
3. Se `.venv` existir mas não for uma venv reconhecível, parar e explicar em vez de sobrescrever.
4. Usar sempre `.venv\Scripts\python.exe -m pip`, nunca `pip` solto.
5. Atualizar pip.
6. Instalar `-r requirements.txt` com `--disable-pip-version-check --no-input`.
7. Configurar `PYTHONNOUSERSITE=1` e `PIP_NO_CACHE_DIR=1`, evitando cache do pip fora do projeto.
8. Validar `import tkinter` e `from faster_whisper import WhisperModel`.
9. Não considerar apenas a existência de `python.exe` como instalação completa.

### 22.4 FFmpeg local

Usar uma build Windows x64 de release referenciada pela página oficial de downloads do FFmpeg. Pin inicial do plano:

```text
URL: https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.2-essentials_build.zip
SHA-256: db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec
```

Fluxo:

1. Se os executáveis locais já existirem, validar e reutilizar.
2. Caso contrário, criar uma pasta temporária única sob `%ROOT%tools`, com prefixo `.setup-temp-<uuid>`; não escrever em diretórios externos ao projeto.
3. Baixar o ZIP com PowerShell `Invoke-WebRequest`.
4. Calcular `Get-FileHash -Algorithm SHA256`.
5. Comparar exatamente com o hash fixado; em divergência, parar e apagar somente o download temporário.
6. Extrair na pasta temporária.
7. Localizar `ffmpeg.exe` e `ffprobe.exe` no `bin` extraído.
8. Montar uma árvore completa staged, por exemplo `tools\.ffmpeg-stage-<uuid>\bin`, e copiar os dois executáveis mais licença/README.
9. Validar **os executáveis staged** e seus encoders antes de publicar qualquer parte.
10. Somente se `tools\ffmpeg` ainda não existir, renomear atomicamente a árvore staged para esse nome.
11. Se `tools\ffmpeg` já existir, validá-la e reutilizá-la; se estiver corrompida, não misturar nem sobrescrever parcialmente — parar com diagnóstico de instalação danificada.
12. Limpar somente a pasta temporária/staged criada por esta execução, verificando antes que seus caminhos resolvidos permaneçam sob `%ROOT%tools` e tenham os prefixos exclusivos esperados.

Se o URL/hash precisar ser atualizado no futuro, consultar novamente uma fonte oficial, atualizar ambos juntos e testar. Nunca desativar a verificação de hash para “fazer funcionar”.

### 22.5 Modelo `small`

O setup deve pré-carregar o modelo padrão dentro de `models` para que o primeiro processamento com `small` não exija outro comando:

Antes do Python inline:

```bat
if not exist "%ROOT%models" mkdir "%ROOT%models"
set "PUTZCLEANER_MODEL_DIR=%ROOT%models"
set "HF_HOME=%ROOT%models\.hf"
set "HF_HUB_CACHE=%ROOT%models\.hf\hub"
set "HF_HUB_DISABLE_TELEMETRY=1"
```

```python
WhisperModel(
    "small",
    device="cpu",
    compute_type="int8",
    download_root=os.environ["PUTZCLEANER_MODEL_DIR"],
)
```

Se faltar internet, o setup falha com mensagem clara e pode ser executado novamente. `medium` e `large-v3` serão baixados automaticamente pelo worker na primeira seleção.

### 22.6 Verificação final do setup

Somente exibir `Instalação concluída com sucesso` depois de:

- venv válido;
- imports válidos;
- FFmpeg e ffprobe válidos;
- `libx264` e `aac` encontrados;
- modelo `small` carregável.

Depois de todas essas verificações, criar atomicamente o marcador `.venv\.putzcleaner_setup_complete`, contendo versão do Python, faster-whisper e FFmpeg validados. O marcador nunca é criado em instalação parcial.

Retornar `exit /b 0` no sucesso e código não zero em qualquer falha. Manter a janela aberta com `pause` quando iniciado diretamente para o usuário poder ler o resultado.

## 23. `abrir_putzcleaner.bat`

Fluxo:

1. `setlocal EnableExtensions DisableDelayedExpansion`.
2. `chcp 65001 >nul`.
3. `cd /d "%~dp0"`.
4. Configurar caches locais e `PYTHONNOUSERSITE=1`.
5. Exigir `.venv\Scripts\pythonw.exe` **e** `.venv\.putzcleaner_setup_complete`; se qualquer um faltar, chamar `setup.bat --from-launcher`.
6. Se setup falhar, mostrar erro e não abrir.
7. Executar:

```text
start "" /D "%ROOT%" "%ROOT%\.venv\Scripts\pythonw.exe" "%ROOT%\src\main.py"
```

8. Fechar o console.

O argumento interno `--from-launcher` deve evitar um `pause` de sucesso no setup, sem ocultar falhas.

## 24. README.md — conteúdo obrigatório

Escrever em português e na seguinte ordem:

1. **O que é o PutzCleaner** — finalidade e foco em entrevistas do Putzforce.
2. **Privacidade** — transcrição/renderização locais; nenhum vídeo enviado; internet apenas para instalação/pesos.
3. **Requisitos** — Windows 10/11 x64 e Python 3.11 x64 com Tkinter.
4. **Instalação** — duplo clique em `setup.bat`, downloads esperados e como reconhecer sucesso.
5. **Como abrir** — duplo clique em `abrir_putzcleaner.bat`.
6. **Como usar** — selecionar MP4, revisar lista, modelo, margens e pasta, processar.
7. **Como editar palavras** — uma por linha; matching exato; acentos importam.
8. **Modelos** — `small` mais leve, `medium` mais lento/preciso, `large` muito pesado em CPU.
9. **Margens** — valores maiores removem mais conteúdo em torno da palavra.
10. **Onde os arquivos são salvos** — padrão na pasta do vídeo ou pasta escolhida.
11. **Nomes de saída** — `_limpo.mp4` e `_limpo_relatorio.json`.
12. **Como testar com vídeo curto** — vídeo de 20–60 s, preferencialmente cópia, com vícios conhecidos.
13. **Como validar** — ouvir pontos do relatório, conferir sincronismo e verificar original.
14. **Solução de problemas** — FFmpeg, Python, rede, modelo, espaço, pasta sem permissão e saída já existente.
15. **Limitações conhecidas**.

Limitações que não podem ser omitidas:

- falsos positivos podem remover fala legítima;
- falsos negativos podem manter vícios;
- `tipo` e `assim` podem ser palavras semanticamente necessárias;
- ruído, música, sotaques e duas pessoas falando juntas reduzem precisão;
- Whisper pode transcrever `ã` como `a` ou `ééé` como `é`; o app não usa aliases agressivos;
- cortes podem soar/parecer bruscos ou causar microcliques;
- margens grandes removem conteúdo útil;
- vídeo é quantizado por frames, então o corte visual não é infinitamente preciso;
- `medium`/`large` podem ser muito lentos ou exceder RAM em CPU;
- primeira utilização de cada modelo exige download;
- a extração WAV usa cerca de 115 MB por hora, e a rota em lotes exige espaço temporário adicional;
- somente a primeira faixa de áudio é usada;
- legendas, capítulos, metadados e faixas adicionais não são preservados;
- HDR/10-bit é convertido para H.264 `yuv420p` de ampla compatibilidade, podendo perder alcance de cor;
- VFR, rotação incomum e offsets de stream exigem atenção;
- não há revisão prévia dos cortes no MVP.

## 25. Tratamento de erros

| Situação | Comportamento obrigatório |
|---|---|
| Python ausente | Setup explica versão/arquitetura e termina sem falso sucesso. |
| Tkinter ausente | Setup pede instalação oficial do Python com Tkinter. |
| Falha pip/rede | Mostrar etapa que falhou e permitir executar setup novamente. |
| FFmpeg/ffprobe ausente | Erro claro e nenhuma alteração no original. |
| Encoders ausentes | Informar falta de `libx264`/`aac`; não tentar outro codec silenciosamente. |
| Config JSON inválido | Não sobrescrever automaticamente; usar padrões em memória e pedir confirmação antes de salvar. |
| MP4 inexistente/vazio | Erro antes de iniciar worker pesado. |
| MP4 corrompido | Mostrar falha de inspeção sem traceback bruto como mensagem principal. |
| Sem vídeo ou áudio | Interromper; transcrição exige áudio. |
| Offset A/V > 0,05 s | Interromper por segurança. |
| Extração/WAV canônico inválido | Interromper antes de carregar o modelo; limpar o WAV. |
| Margem inválida | Destacar campo e explicar faixa 0–2 s. |
| Lista vazia/inválida | Explicar uma palavra por linha. |
| Pasta sem permissão | Interromper antes de transcrever. |
| Saída existente | Parar, não sobrescrever, não numerar. |
| Modelo sem internet/cache | Explicar que o download inicial falhou e pedir nova tentativa com conexão. |
| Falta de RAM | Sugerir modelo menor, sem downgrade silencioso. |
| Nenhum alvo confiável | Gerar vídeo H.264/AAC completo e relatório com zero cortes. |
| Todo vídeo seria removido | Interromper e não publicar saída. |
| FFmpeg exit code != 0 | Mostrar resumo do stderr, limpar staged, não mostrar sucesso. |
| Codec/duração final inválido | Não publicar e explicar falha de validação. |
| Relatório falha | Não concluir transação; limpar artefatos próprios. |
| Cancelamento | Encerrar filho, limpar e informar cancelamento sem sucesso. |
| Limpeza falha | Registrar o caminho exato e erro; não fingir que temporários foram removidos. |

Exceções técnicas devem ser registradas no log com tipo e detalhe. A messagebox mostra uma frase curta e acionável, não um traceback inteiro.

## 26. Ordem exata de implementação

### Fase 0 — guarda de segurança

1. Repetir auditoria.
2. Ler todos os arquivos existentes.
3. Comparar estrutura com este plano.
4. Se houver conflito, parar.
5. Não executar mutações Git.

Critério de saída: escopo confirmado sem sobrescrita.

### Fase 1 — arquivos declarativos

1. Criar `requirements.txt`.
2. Criar `config.json` exato.
3. Não instalar nada ainda.
4. Validar JSON com o Python disponível.

Critério de saída: configuração carrega e preserva acentos.

### Fase 2 — transcriber

1. Implementar dataclasses.
2. Implementar `normalize_token` e validação de termos.
3. Implementar `Transcriber` e mapa de modelos.
4. Implementar callbacks/cancelamento.
5. Não criar GUI ainda.

Critério de saída: módulo compila e testes de normalização passam.

### Fase 3 — cutter puro

1. Implementar dataclasses de mídia/cortes.
2. Implementar validação numérica.
3. Implementar ocorrência confiável/protegida.
4. Implementar margens, união e complemento.
5. Testar funções sem FFmpeg.

Critério de saída: invariantes e casos de limite passam.

### Fase 4 — toolchain/render

1. Resolver/validar FFmpeg e ffprobe.
2. Implementar probe.
3. Implementar extração e validação do WAV canônico.
4. Implementar graph builder para 1 e N keeps usando a mesma timeline.
5. Implementar runner com progresso/stderr/cancelamento.
6. Implementar verificação final.
7. Implementar rota em lotes.
8. Implementar staging e limpeza.

Critério de saída: integração sintética real obrigatória passa sem alterar o original; a rota de mais de 100 keeps também passa antes de alegar suporte completo a entrevistas longas.

### Fase 5 — report

1. Implementar schema.
2. Arredondar somente na serialização.
3. Implementar escrita staged.
4. Validar `allow_nan=False`.

Critério de saída: JSON contém ocorrências, timestamps e total de cortes.

### Fase 6 — GUI e orquestração

1. Implementar carga/salvamento de config.
2. Construir tela única.
3. Implementar validações síncronas rápidas.
4. Implementar worker/fila/progresso.
5. Conectar módulos.
6. Implementar fechamento seguro.

Critério de saída: GUI continua respondendo durante um worker controlado.

### Fase 7 — entry point e BATs

1. Implementar `main.py`.
2. Implementar `setup.bat` idempotente.
3. Implementar launcher.
4. Testar caminhos com espaços.

Critério de saída: duplo clique instala/abre sem comandos posteriores.

### Fase 8 — README

Escrever somente depois que comportamento e nomes estiverem confirmados. Não documentar recurso não implementado.

### Fase 9 — validação e entrega

Executar a seção seguinte, registrar comandos/códigos de saída e reportar honestamente o que não foi testado.

## 27. Plano de testes

### 27.1 Checagem obrigatória de sintaxe sem criar bytecode no `src`

Depois de criar o projeto, executar realmente:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
@'
from pathlib import Path

files = sorted(Path("src").glob("*.py"))
if not files:
    raise SystemExit("Nenhum arquivo Python encontrado em src")

for path in files:
    source = path.read_text(encoding="utf-8")
    compile(source, str(path), "exec")
    print(f"SINTAXE OK: {path}")
'@ | .\.venv\Scripts\python.exe -
```

Se a venv ainda não estiver disponível, usar o Python 3.11 detectado, deixando isso explícito no relatório. Não declarar sucesso se qualquer exit code for diferente de zero.

### 27.2 Smoke de imports

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'src'); import transcriber, cutter, report, gui; print('IMPORTS OK')"
```

Esse teste não prova que a GUI abriu nem que um vídeo foi processado.

### 27.3 Casos unitários obrigatórios para lógica

Mesmo sem criar uma pasta persistente de testes, executar por harness temporário ou chamada controlada:

- `" Né, " → "né"`;
- `ã != a`;
- `ééé != é`;
- duplicatas normalizadas;
- entrada com duas palavras rejeitada;
- margem `0.05` e `0,05`;
- `None`, string inválida, booleano, NaN, infinito, negativo e `end <= start` rejeitados antes da ordenação;
- confiança `0.599` rejeitada e `0.600` aceita;
- clamp de 20 ms no início/fim;
- corte no início nunca negativo;
- corte no fim nunca passa da duração;
- candidatos sobrepostos unidos;
- gap `0.119` unido e `0.121` separado;
- palavra protegida no gap impede união;
- alvo sobreposto a palavra protegida é ignorado;
- complemento com zero, um e vários cortes;
- zero cortes preserva todo vídeo;
- corte cobrindo tudo falha;
- `total_ocorrencias != total_cortes` quando duas palavras viram um corte;
- colisão de saída falha;
- comparação de caminhos case-insensitive impede saída igual à entrada;
- JSON rejeita NaN.

### 27.4 Integração sintética obrigatória para aceitar o cutter

Um teste sintético valida o cutter, não a qualidade da transcrição.

1. Criar em diretório temporário um MP4 real de 3 s, 320×240, cor sólida e tom, H.264/AAC.
2. Calcular SHA-256 do original.
3. Aplicar corte conhecido de 1,0–1,5 s diretamente pelo cutter.
4. Confirmar saída próxima de 2,5 s.
5. Confirmar codecs H.264/AAC com ffprobe.
6. Recalcular SHA-256 do original e exigir igualdade.
7. Confirmar relatório.
8. Confirmar ausência de staged/filtergraph/lotes após sucesso.
9. Forçar falha e confirmar limpeza.
10. Repetir com caminho que tenha espaços, acentos, `&` e parênteses.
11. Testar zero cortes.
12. Testar mais de 100 keeps para a rota em lotes.
13. Testar áudio menor que vídeo e confirmar padding/sincronismo da timeline.
14. Testar mídia com gap de timestamps e confirmar que WAV, plano e saída usam a mesma duração.
15. Testar offset A/V acima de 0,05 s e confirmar rejeição anterior à transcrição.
16. Testar funcionalmente as duas opções de filtergraph por arquivo e confirmar o fallback.

Só relatar esse teste se ele for realmente executado. Chamá-lo de “teste sintético”; nunca de entrevista real.

### 27.5 Validação manual com vídeo curto real

Quando o usuário fornecer ou autorizar um MP4 curto em português:

1. Preferir 20–60 s.
2. Anotar manualmente vícios e timestamps aproximados.
3. Calcular SHA-256 do original antes.
4. Abrir pelo BAT.
5. Selecionar `small`, margens padrão e lista padrão.
6. Processar.
7. Conferir MP4 e JSON.
8. Calcular SHA-256 do original depois e exigir igualdade.
9. Ouvir/ver 2 s antes e depois de cada corte do relatório.
10. Conferir sincronismo labial no começo e no fim.
11. Confirmar que usos legítimos de `tipo`/`assim` foram avaliados manualmente.
12. Registrar falsos positivos, falsos negativos e cortes bruscos.

Sem esse arquivo, escrever explicitamente: `Nenhum vídeo real foi processado porque nenhum vídeo real foi fornecido.`

### 27.6 Matriz manual Windows

- Primeira instalação.
- Segunda execução idempotente do setup.
- Projeto em pasta com espaços e acentos.
- Máquina sem FFmpeg global.
- Abertura por launcher.
- Cancelar file dialog.
- MP4 inválido/corrompido.
- MP4 sem áudio.
- Config inválido.
- Pasta sem permissão.
- Saída já existente.
- Primeiro download de cada modelo.
- Execução offline com modelo já armazenado.
- Fechamento durante transcrição.
- Fechamento durante FFmpeg.
- Escala de tela 100%, 150% e 200%.
- Entrevista longa para medir RAM, tempo, espaço e sincronismo.

## 28. Rastreabilidade dos requisitos

| Requisito | Implementação | Validação |
|---|---|---|
| Original intacto | Entrada somente leitura; caminhos distintos; staging | SHA-256 antes/depois. |
| Sufixo `_limpo` | Função determinística de nomes | Teste de nome. |
| H.264/AAC | `libx264` + `aac` | ffprobe da saída. |
| Relatório | `report.py`, schema v1 | Parse JSON e campos obrigatórios. |
| Salvar configurações | Escrita atômica de `config.json` | Round-trip com acentos. |
| Validar FFmpeg | `resolve_toolchain`/`validate_toolchain` | Ausência e encoder faltante. |
| GUI não trava | Thread + Queue + `after` | Interagir/mover janela durante worker. |
| Ignorar timestamp não confiável | Critério conservador e razões | NaN/limites/confiança. |
| Nunca tempo negativo | Clamp e invariantes | Palavra no início. |
| Unir cortes próximos | 0,12 s com proteção | Gaps e palavra protegida. |
| Sem comandos após instalação | BATs por duplo clique | Teste limpo Windows. |
| Temporários removidos | Diretório por execução + `finally` | Sucesso, erro e cancelamento. |
| Tela única em português | Layout definido | Inspeção manual. |
| Modelos small/medium/large | mapa para faster-whisper | Seleção e validação. |
| Pasta de saída | Campo e config | Reiniciar e recarregar. |
| Logs/progresso | eventos estruturados | Fases e monotonicidade. |
| Sem API paga/web | inferência local | Revisão de dependências/código. |

## 29. Critérios de aceite finais

A implementação só pode ser considerada pronta quando todos os itens aplicáveis forem verdadeiros:

- Todos os arquivos obrigatórios existem e foram revisados.
- Nenhum arquivo anterior foi sobrescrito sem inspeção.
- Não houve mutação Git.
- Setup funciona por duplo clique e sem administrador.
- Depois do Python e do setup, nenhum comando manual é exigido.
- Launcher abre a GUI sem console permanente.
- Interface contém todos os controles pedidos em português.
- Pasta de saída está disponível e persistida.
- GUI responde durante transcrição e renderização.
- Um segundo processamento simultâneo é impedido.
- FFmpeg/ffprobe e encoders são validados.
- Original nunca é usado como saída.
- Saída existente nunca é sobrescrita.
- Nenhum timestamp negativo ou não finito chega ao FFmpeg.
- Alvo de baixa confiança é ignorado e relatado.
- Margens não atravessam palavras protegidas.
- Cortes próximos são unidos apenas quando seguro.
- Zero cortes ainda produz MP4 H.264/AAC e relatório zero.
- Todo o vídeo nunca é removido silenciosamente.
- Saída final contém H.264 e AAC e duração plausível.
- Relatório contém palavra, início, fim e total de cortes.
- `total_ocorrencias` e `total_cortes` têm semânticas distintas.
- Temporários são removidos em sucesso, erro e cancelamento controlado.
- Configuração preserva acentos e recarrega.
- Sintaxe Python foi realmente checada.
- Testes declarados como aprovados foram realmente executados.
- Se não houve vídeo real, isso foi dito explicitamente.
- README descreve limitações e não promete recursos ausentes.

## 30. Formato do relatório de entrega da IA implementadora

Ao terminar, responder com fatos verificáveis:

```text
Arquivos criados:
- ...

Arquivos alterados:
- ...

Validações executadas:
- <comando> — exit code <n> — resultado

Validações não executadas:
- <teste> — <motivo>

Como executar:
1. Duplo clique em setup.bat.
2. Duplo clique em abrir_putzcleaner.bat.

Como validar com vídeo curto:
- ...

Riscos reais:
- cortes bruscos;
- falso positivo/falso negativo;
- tempo/RAM dos modelos;
- ...

Vídeo real:
- Nenhum vídeo real foi processado porque nenhum foi fornecido.
```

Não usar frases como “tudo testado” sem listar o que foi executado e seu resultado real.

## 31. Referências técnicas verificadas

Consultadas em 09/07/2026; priorizar estas fontes oficiais ao implementar:

- faster-whisper, instalação, CPU/int8, timestamps por palavra, VAD e download de modelos: <https://github.com/SYSTRAN/faster-whisper>
- API `WhisperModel`, `download_root`, `word_timestamps` e opções de transcrição: <https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py>
- Decodificação do áudio pelo faster-whisper, cujo descarte de PTS motiva o WAV canônico: <https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/faster_whisper/audio.py>
- Release fixada do faster-whisper: <https://pypi.org/project/faster-whisper/1.2.1/>
- FFmpeg `trim`, `atrim`, `setpts`, `asetpts` e `concat`: <https://ffmpeg.org/ffmpeg-filters.html>
- FFmpeg `-progress`: <https://ffmpeg.org/ffmpeg.html>
- ffprobe e saída JSON: <https://ffmpeg.org/ffprobe-all.html>
- Página oficial do FFmpeg que referencia builds Windows: <https://ffmpeg.org/download.html>
- Build Windows fixada e checksum: <https://www.gyan.dev/ffmpeg/builds/>
- Modelo de threads/event loop do Tkinter: <https://docs.python.org/3/library/tkinter.html#threading-model>
- Temporários e limpeza via `TemporaryDirectory`: <https://docs.python.org/3/library/tempfile.html#tempfile.TemporaryDirectory>

## 32. Riscos residuais aceitos

Mesmo com toda a proteção deste plano, não é possível garantir edição perfeita automaticamente:

1. ASR pode errar a palavra ou o timestamp.
2. `probability` não é uma confiança semântica calibrada.
3. `tipo` e `assim` podem ser fala útil.
4. Hesitações isoladas podem ser removidas pelo VAD ou transcritas como outra palavra.
5. Se o ASR produzir tokens temporalmente sobrepostos, o corte é ignorado; sem diarização, algumas falas simultâneas podem não ser representadas como tokens separados e ainda ser afetadas.
6. O vídeo corta em limites de frames; o áudio, em limites de amostras/frames de áudio.
7. Corte seco pode produzir mudança visual abrupta ou microclique.
8. CPU pode tornar `medium`/`large` muito lentos em vídeos longos.
9. A rota em lotes usa mais disco temporário.
10. HDR/10-bit e metadados avançados não são preservados integralmente.

Esses riscos devem ser comunicados ao usuário e avaliados com um vídeo curto antes de processar uma entrevista longa.
