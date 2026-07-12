# Plano de Melhoria do Sistema de Cortes — PutzCleaner v2

> **Status:** plano aprovado para implementação. Nenhum código foi alterado ainda.
> **Baseado em:** auditoria completa do código real em `src/` (commit `574e872`).
> **Complementa:** `PLANO_IMPLEMENTACAO_PUTZCLEANER.md` (plano v1, já implementado). Este documento NÃO repete o v1; ele evolui o sistema existente.
> **Público:** qualquer desenvolvedor ou IA que vá implementar sem acesso a quem escreveu este plano. Todas as decisões estão fechadas; onde havia alternativas, a escolha está justificada.

---

## 0. Sumário executivo

O PutzCleaner v1 funciona e é seguro contra corrupção de arquivos, mas tem quatro limitações estruturais que este plano resolve:

1. **Detecção por igualdade exata** — o usuário precisa enumerar manualmente cada variação ("né", "nééé", "neeee"...). O `config.json` atual tem **45 entradas** que colapsam para ~8 termos canônicos. Pior: palavras lexicais como "tipo" e "assim" são cortadas **sempre** que reconhecidas, mesmo em uso legítimo ("um *tipo* de pessoa"), porque não existe análise de contexto.
2. **Cortes cegos ao áudio** — margens fixas (0,05s/0,08s) aplicadas sobre timestamps do Whisper (precisão real: ±20–100ms), sem detecção de silêncio, sem ajuste por energia, sem duração mínima de corte e sem suavização de áudio nas junções (cliques audíveis).
3. **Retranscrição a cada execução** — ajustar o limiar de confiança ou a lista de termos exige transcrever o vídeo inteiro de novo (minutos), quando o replanejamento de cortes leva milissegundos.
4. **Sem testes e GUI monolítica** — `gui.py` (1015 linhas) mistura UI, configuração e orquestração; não existe pasta `tests/`.

A solução, em uma frase: **cachear a transcrição, decidir com contexto e classe de palavra, ajustar as bordas do corte pelo silêncio real do áudio, e suavizar as junções com micro-fades — mantendo a renderização FFmpeg atual, que já é correta.**

---

## 1. Diagnóstico da arquitetura atual

### 1.1 Fluxo real mapeado (com referências de código)

```
main.py                    → caches HF locais + DLLs CUDA + Tk root
gui.py:PutzCleanerApp      → coleta opções (ProcessingOptions), valida, salva config.json
gui.py:run_worker (l.291)  → orquestra em thread separada, eventos via queue:
  1. Validações de entrada/saída/colisão        (gui.py:317-346)
  2. resolve_toolchain                          (cutter.py:565)  — acha/valida ffmpeg+ffprobe
  3. probe_media                                (cutter.py:696)  — duração canônica, streams, offsets A/V
  4. extract_canonical_audio                    (cutter.py:821)  — WAV mono 16kHz alinhado à timeline
  5. Transcriber.transcribe                     (transcriber.py:337) — faster-whisper, word_timestamps
  6. build_cut_plan                             (cutter.py:253)  — lógica pura: alvos → proteção → merge → keeps
  7. render_video                               (cutter.py:949)  — filtergraph trim/atrim/concat, reencode H.264+AAC
  8. build_report + build_transcript            (report.py:29, transcript.py:61)
  9. Publicação transacional sem sobrescrita    (gui.py:472-504)
```

### 1.2 O que já está bom (NÃO retrabalhar)

Estes pontos foram auditados e devem ser **preservados como estão** na v2:

- **Separação domínio/UI**: `transcriber.py`, `cutter.py`, `report.py`, `transcript.py` não importam `gui`. O worker nunca toca widgets.
- **WAV canônico** (`extract_canonical_audio`): `aresample=async=1:first_pts=0,apad,atrim` garante que os timestamps do Whisper se referem à mesma timeline do vídeo. É a fundação de tudo — e será **reutilizada** para a análise de energia (seção 2).
- **Proteção de vizinhas** (`build_cut_plan`): margens nunca invadem palavra não-alvo; núcleo sobreposto a protegida é rejeitado. Este invariante de segurança continua na v2.
- **Renderização por filtergraph** com `setpts/asetpts` por segmento + `concat`: frame-exato, sem problema de keyframe, A/V síncrono. A rota em lotes (>100 keeps) também está correta.
- **Publicação transacional** (staging + `os.rename` sem sobrescrita + rollback).
- **Verificação da saída** (`verify_output`): codec e duração conferidos antes de publicar.
- **Relatório auditável** com motivos canônicos de ignorados.

### 1.3 Fragilidades identificadas, separadas por categoria

#### (A) Qualidade perceptual — cortes "secos"/artificiais

| # | Problema | Evidência no código | Efeito percebido |
|---|----------|--------------------|------------------|
| A1 | Margens fixas globais (0,05/0,08s) sem relação com o áudio real | `config.json:51-52`; `build_cut_plan` aplica `word_start - mb` / `word_end + ma` (cutter.py:354-355) | Corte no meio de fonema: sobra um "n..." do "né" ou some o ataque da palavra seguinte |
| A2 | Nenhuma detecção de silêncio/energia; o WAV canônico é usado só pelo Whisper e descartado | Não existe módulo de análise de áudio | A borda do corte cai em ponto de alta energia → clique/estalo e transição abrupta |
| A3 | Sem duração mínima de corte | `_merge_candidates` só descarta corte `<= EPSILON` (cutter.py:488) | Cortes de 40–80ms produzem "soluços" no vídeo sem ganho perceptível |
| A4 | Fusão de cortes com gap fixo de 0,12s | `MERGE_GAP_SEC = 0.12` (cutter.py:43) | Dois vícios a 0,2s de distância viram dois jump-cuts em sequência (metralhadora visual) |
| A5 | Micro-pausas naturais são engolidas por inteiro quando a margem alcança o silêncio | Margem se estende até a vizinha protegida sem reservar pausa (cutter.py:357-374) | Fala emendada "sem respiro": ritmo robótico |
| A6 | Nenhuma suavização de áudio nas junções | Filtergraph concatena `atrim` bruto (cutter.py:898-946) | Cliques audíveis quando a borda não cai em silêncio absoluto |
| A7 | Vício "grudado" na fala recebe o mesmo tratamento do isolado | Não há medição de gap para vizinhos na decisão | Ou corta agressivo demais (trunca sílaba vizinha) ou é ignorado |

#### (B) Precisão/assertividade — decisão "remover ou não"

| # | Problema | Evidência | Efeito |
|---|----------|-----------|--------|
| B1 | Match por igualdade exata do token normalizado | `vt.token.normalized in targets` (cutter.py:317) | Usuário mantém 45 variantes no config e ainda escapam "nééééééé" com 7 letras |
| B2 | Palavras lexicais ("tipo", "assim") cortadas sem análise de contexto | Nenhum uso de vizinhança na decisão | **Falso positivo garantido** em "um tipo de", "assim que ele chegou" |
| B3 | Limiar de confiança único global | `_confidence_reason` usa um só `min_probability` (cutter.py:211) | Usuário baixou para 0,2 (config.json:53) para pegar "hum"/"ééé" (que têm prob. baixa por natureza) — e com isso "tipo"/"assim" duvidosos também passam |
| B4 | Sem termos multi-palavra | `validate_terms` rejeita whitespace interno (transcriber.py:157) | Impossível remover "tipo assim", "é... né" |
| B5 | Normalização preserva acentos e repetições por design (v1 §10), sem camada opcional de equivalência | `normalize_token` (transcriber.py:97) | "né"≠"ne"≠"nê"≠"neh" exigem 4 entradas |
| B6 | Vínculo ocorrência→palavra na transcrição .txt é heurístico por tolerância de timestamp | `_removed_word_indexes` (transcript.py:35-58) | Marcação `[removida]` pode errar com palavras repetidas próximas |
| B7 | Duração da palavra validada, mas sem plausibilidade por classe (um "hum" de 2,9s passa) | `MIN/MAX_WORD_DURATION_SEC` globais (transcriber.py:38-39) | Alucinações longas do Whisper podem virar cortes de vários segundos |

#### (C) Performance

| # | Problema | Evidência | Custo |
|---|----------|-----------|-------|
| C1 | Retranscreve a cada execução, mesmo com arquivo/modelo idênticos | `run_worker` sempre chama `transcribe` | Minutos desperdiçados a cada ajuste de limiar/termos — o maior custo de iteração do usuário |
| C2 | Sem modo "analisar sem renderizar" | Fluxo é sempre completo até o MP4 | Usuário paga render inteiro só para descobrir o que seria cortado |
| C3 | Encoder fixo libx264 preset medium, mesmo com GPU CUDA presente | `_render_single_graph` (cutter.py:1027-1031) | Render 3–8× mais lento que h264_nvenc em máquinas com NVIDIA |
| C4 | Progresso com faixas fixas hardcoded (18–60% transcrição etc.) | gui.py:382-397, 424-425 | Estimativa irreal quando transcrição domina (CPU) ou render domina (GPU) — aceitável, prioridade baixa |

#### (D) Manutenibilidade

| # | Problema | Evidência |
|---|----------|-----------|
| D1 | `gui.py` com 1015 linhas: UI + config + worker + publicação no mesmo arquivo | src/gui.py |
| D2 | `cutter.py` com 1442 linhas: lógica pura + toolchain + probe + render + verificação | src/cutter.py |
| D3 | Zero testes no repositório (o plano v1 §27 os define, mas `tests/` não existe) | ausência de `tests/` |
| D4 | Constantes de decisão espalhadas entre `transcriber.py` e `cutter.py` | MIN_WORD_PROBABILITY em transcriber, MERGE_GAP em cutter |

---

## 2. Estratégia para cortes mais naturais

Princípio geral: **o timestamp do Whisper indica ONDE está o vício; o áudio real indica ONDE cortar.** A v2 introduz uma etapa de *refinamento de bordas* entre a decisão (o que remover) e o plano final (intervalos exatos).

### 2.1 Análise de energia do áudio (novo módulo `audio_analysis`)

O WAV canônico (mono, 16kHz, s16le) já existe antes do planejamento. Ler com `wave` (stdlib) + `numpy` (já é dependência transitiva do faster-whisper — **zero dependências novas**):

- **Envelope RMS**: janela de 25ms, hop de 10ms → array `rms_db` com ~100 amostras/segundo. Custo: <1s para 1h de vídeo.
- **Piso de ruído**: percentil 10 do `rms_db` (robusto a vídeos com ruído de fundo constante).
- **Silêncios**: sequências contíguas com `rms_db < piso + 8 dB` durando ≥ 60ms → lista de `SilenceSpan(start, end)`.

Por que RMS próprio em vez de `ffmpeg silencedetect`: (a) o WAV já está decodificado em disco — nenhuma passada extra de FFmpeg; (b) precisamos do envelope contínuo para *snapping* por energia mínima, não só de spans binários; (c) testável com WAVs sintéticos sem FFmpeg. **Decisão: RMS via numpy. `silencedetect` rejeitado.**

### 2.2 Margens adaptativas + snapping por silêncio

Substituir as margens fixas por uma busca em janela:

- Para a **borda inicial** do corte: procurar, na janela `[word_start − busca_max, word_start]`, o melhor ponto de corte:
  1. Se existe silêncio nessa janela → cortar **dentro do silêncio**, preservando `pausa_retida/2` de silêncio antes da junção (ver 2.4).
  2. Senão → usar margem mínima (30ms) e ajustar ao **mínimo local de energia** em ±20ms.
- Simetricamente para a **borda final** na janela `[word_end, word_end + busca_max]`.
- A busca **nunca** ultrapassa a palavra protegida vizinha (invariante v1 mantido) nem `busca_max` (parâmetro do preset, 0,25–0,50s).

**Zero-crossing:** ajustar a amostra exata de corte ao cruzamento por zero eliminaria cliques, mas o envelope de 10ms + micro-fades de 12ms (2.6) já mascaram qualquer descontinuidade com resultado audivelmente idêntico e implementação muito mais simples (o render trabalha na taxa original, não em 16kHz — o snapping por amostra exigiria decodificar o áudio original). **Decisão: mínimo de energia (granularidade 10ms) + micro-fades; zero-crossing rejeitado como over-engineering.**

### 2.3 Não cortar no meio de sílabas/fonemas

Três defesas em camadas:

1. **Snapping por silêncio/energia (2.2)** — resolve a maioria dos casos.
2. **Classificação isolado vs. grudado** (por gap temporal E confirmação por energia):
   - `isolado`: silêncio ≥ 120ms de ambos os lados → corte generoso (absorve pausa, ver 2.4).
   - `semi-isolado`: silêncio de um lado só → corte estendido apenas para o lado do silêncio; do outro lado, margem mínima de 30ms.
   - `grudado`: sem silêncio nos dois lados → **modo conservador**: cortar apenas o núcleo com margem de 20ms + fades; em preset Conservador, **não cortar** (motivo `vicio_grudado_na_fala`).
3. **Micro-fades no áudio (2.6)** — mascaram resíduos de fonema de <15ms.

### 2.4 Preservação de micro-pausas naturais

Regra da **pausa retida**: ao remover um vício cercado de silêncio, a junção resultante deve manter uma pausa total de `pausa_retida` segundos (preset: 0,10–0,20s), preferencialmente distribuída como ⅓ antes + ⅔ depois da junção (a respiração antes da próxima frase soa mais natural que depois da anterior). Ou seja: o corte absorve o vício **e parte** do silêncio ao redor, nunca **todo** o silêncio.

### 2.5 Fusão, duração mínima e máxima

- **Fusão adaptativa**: gap de fusão vira parâmetro de preset (0,12–0,40s). Regra extra: se o trecho *preservado* entre dois cortes durar < 250ms **e** for silêncio (energia no piso), fundir os cortes — evita "ilha" de 200ms de nada entre dois jump-cuts. Se o trecho preservado contém fala (energia acima do piso), **nunca** fundir.
- **Duração mínima do corte**: cortes finais < `corte_min` (60–100ms por preset) são descartados com motivo `corte_muito_curto`. Custo/benefício: um pulo de 60ms é visível e o vício de 60ms é quase inaudível.
- **Duração máxima do corte**: cortes finais > 2,5s são rejeitados com motivo `corte_suspeito_longo` (provável alucinação do ASR — B7). Ficam no relatório para revisão humana.

### 2.6 Suavização de áudio (declick) — micro-fades, não crossfade

Duas opções foram avaliadas:

| Abordagem | Como | Prós | Contras |
|-----------|------|------|---------|
| **`acrossfade` real** entre segmentos | Sobrepõe N ms do fim de um segmento com o início do próximo | Transição de áudio mais suave | **Consome duração** (a sobreposição encurta o áudio) → dessincroniza com o vídeo concatenado por corte seco; compensar exigiria manipular também o vídeo (xfade), mudando a natureza visual do corte |
| **Micro-fades por segmento** (recomendada) | `afade=t=in:st=0:d=0.012` + `afade=t=out:st=DUR−0.012:d=0.012` em cada `atrim` antes do `concat` | Preserva duração exata (A/V sync intacto); elimina cliques; trivial de gerar (a duração de cada keep é conhecida) | Não é crossfade "de verdade" — mas com bordas já em silêncio (2.2), é imperceptível |

**Decisão: micro-fades de 12ms por segmento de áudio (opção "Suavizar áudio" na UI, ligada por padrão). `acrossfade` rejeitado por quebrar sincronismo.**

### 2.7 Regras visuais

- Corte de vídeo continua seco (jump-cut) — é a linguagem padrão de entrevista editada e qualquer suavização visual (xfade/morph) está **fora do escopo v2**.
- A defesa contra "pulo brusco" é indireta e suficiente: duração mínima de corte (2.5), fusão de rajadas (2.5) e bordas em pausas (2.2) — o jump-cut em cima de pausa é o menos perceptível que existe.

---

## 3. Estratégia para maior assertividade

### 3.1 Termos com metadados (TermSpec) em vez de lista plana

Cada termo passa a ter classe e modo de matching (config v2, seção 6.7):

```json
{ "texto": "né",    "alongamento": true,  "contexto": "sempre",  "classe": "vicio_puro" }
{ "texto": "tipo",  "alongamento": false, "contexto": "isolado", "classe": "lexical"    }
```

- **`classe`** define o limiar de confiança padrão (3.3).
- **`contexto`** define se exige isolamento prosódico (3.4).
- **`alongamento`** habilita o matching de alongamentos (3.2).
- Retrocompatibilidade: lista plana v1 é migrada automaticamente — termos conhecidos recebem metadados de um dicionário embutido (né→vicio_puro/sempre/alongamento; tipo/assim→lexical/isolado); desconhecidos viram `exato/sempre/vicio_puro` (comportamento v1).

### 3.2 Três níveis de matching (substituem a explosão de variantes)

Ordem de avaliação por token; o primeiro que casar vence:

1. **Exato** (comportamento v1): `normalize_token(token) ∈ termos_exatos`. Continua sendo o único nível que casa formas curtas sem alongamento ("né", "neh", "hum").
2. **Alongamento** (novo): só se aplica quando o token bruto contém uma **sequência de ≥3 caracteres idênticos** (garantia de que houve alongamento real). Pipeline: normalizar → *fold* de acentos (NFD, remove marcas combinantes) → colapsar sequências para 1 caractere → comparar com a base do termo também *folded* ("né"→"ne").
   - `"nééé"` → fold `"neee"` → colapsa `"ne"` → casa com base `"ne"` ✓
   - `"é"` (verbo ser) → sem sequência ≥3 → **nunca** entra neste nível ✓ (propriedade de segurança central)
   - `"neeem"` → colapsa `"nem"` → `"nem"` não é base → não casa ✓
3. **Frase** (novo): termos com espaço ("tipo assim") casam janelas de 2–3 tokens **consecutivos, do mesmo segmento**, com gap entre palavras ≤ 300ms. Probabilidade da frase = mínimo das probabilidades das palavras.

**Regex por termo**: avaliado e **rejeitado para a v2** — os níveis 1+2 cobrem todos os casos do config atual com muito menos risco de falso positivo por regex mal escrita de usuário leigo. Fica anotado como extensão futura (prefixo `re:` no texto do termo), e o design do `TermSpec` já comporta.

Matching continua **sempre por token inteiro** — nunca substring. "né" jamais casa dentro de "nervoso" (propriedade v1 preservada).

### 3.3 Confiança mínima por classe (resolve B3)

O motivo de o usuário ter baixado o limiar global para 0,2: interjeições não-lexicais ("hum", "ééé", "ã") recebem probabilidade estruturalmente baixa do Whisper, enquanto "tipo"/"assim" recebem alta. Um limiar único não serve aos dois. A v2 usa limiares por classe, definidos pelo preset:

| Classe | Conservador | Equilibrado | Agressivo |
|--------|-------------|-------------|-----------|
| `vicio_puro` (né, hum, ééé, ã, hã) | 0,50 | 0,35 | 0,20 |
| `lexical` (tipo, assim, então) | 0,80 | 0,70 | 0,55 |

O campo "Confiança mínima" da UI vira um **ajuste fino opcional** (offset sobre o preset, seção 8) — o usuário leigo nunca mais precisa raciocinar sobre probabilidade.

### 3.4 Contexto anterior/posterior (resolve B2)

Para termos `contexto: "isolado"` (lexicais), a remoção exige isolamento prosódico:

- `gap_antes` = `token.start − fim_do_token_anterior` (início de segmento conta como gap grande);
- `gap_depois` = simétrico;
- **Confirmação por energia**: o gap só conta como pausa se a análise de áudio (2.1) confirmar silêncio nele — gaps do ASR mentem em fala rápida.

| Preset | Exigência para remover termo lexical |
|--------|--------------------------------------|
| Conservador | pausa ≥ 250ms **dos dois lados** |
| Equilibrado | pausa ≥ 200ms de um lado **e** ≥ 120ms do outro |
| Agressivo | pausa ≥ 120ms de pelo menos um lado |

"um **tipo** de pessoa" não tem pausa alguma ao redor de "tipo" → nunca é removido. "a gente foi lá... **tipo**... e aí deu certo" tem pausas dos dois lados → removido. Termos `contexto: "sempre"` (vícios puros) pulam esta checagem.

### 3.5 Plausibilidade de duração por classe (resolve B7)

Além dos limites globais v1: `vicio_puro` aceita 60ms–1,5s; `lexical` aceita 80ms–1,0s. Fora disso → `duracao_implausivel` no relatório.

### 3.6 Relatório de ignorados ampliado

Novos motivos canônicos (somam-se aos 9 do v1): `vicio_grudado_na_fala`, `contexto_nao_isolado`, `corte_muito_curto`, `corte_suspeito_longo`, `duracao_implausivel`, `frase_incompleta` (janela de frase quebrada por gap/segmento). Cada item ignorado ganha os campos `gap_antes`, `gap_depois` e `classe` para o usuário entender e calibrar.

### 3.7 Modos Conservador / Equilibrado / Agressivo

Um preset é um dicionário fechado de parâmetros (única fonte de verdade, em `config.py`):

| Parâmetro | Conservador | Equilibrado | Agressivo |
|---|---|---|---|
| limiar `vicio_puro` | 0,50 | 0,35 | 0,20 |
| limiar `lexical` | 0,80 | 0,70 | 0,55 |
| contexto p/ lexical | 2 lados ≥250ms | 200ms + 120ms | 1 lado ≥120ms |
| vício grudado | não corta | corta núcleo+20ms | corta núcleo+30ms |
| busca de silêncio (`busca_max`) | 0,25s | 0,35s | 0,50s |
| pausa retida na junção | 0,20s | 0,15s | 0,10s |
| gap de fusão de cortes | 0,12s | 0,25s | 0,40s |
| corte mínimo | 0,10s | 0,08s | 0,06s |
| corte máximo | 2,0s | 2,5s | 2,5s |

Padrão de fábrica: **Equilibrado**.

---

## 4. Estratégia de performance

### 4.1 Cache de transcrição (maior ganho — resolve C1)

- **Local**: `cache/transcricoes/<chave>.json` na raiz do projeto (adicionar `cache/` ao `.gitignore`).
- **Chave**: SHA-256 de `(caminho_absoluto, tamanho_bytes, mtime_ns, modelo_resolvido, idioma, versao_faster_whisper, parametros_transcricao)`. Tamanho+mtime é suficiente e instantâneo; hash do conteúdo do vídeo rejeitado (custaria ler GBs).
- **Conteúdo**: `TranscriptionResult` serializado (todas as `WordToken` + metadados) com `schema_cache: 1`.
- **Invalidação**: chave diferente = miss. Schema diferente = descarta. Botão/checkbox "Ignorar cache" na área avançada da UI.
- **Limpeza**: ao gravar, manter no máximo 20 arquivos (LRU por mtime).
- **Efeito**: mudar termos/preset/margens e reprocessar cai de minutos para segundos.
- **Importante**: o dispositivo (cpu/cuda) **não** entra na chave — o resultado é o que importa, não onde foi computado.

### 4.2 Modo "Analisar (sem renderizar)" (resolve C2)

Novo botão que executa o pipeline até o planejamento (passos 1–6 do fluxo) e grava apenas `<nome>_analise_relatorio.json` + `<nome>_analise_transcricao.txt`, mostrando resumo na tela (nº de ocorrências, cortes, tempo removido, ignorados por motivo). Com o cache (4.1), a segunda análise do mesmo vídeo é quase instantânea. É o loop de calibração do usuário.

### 4.3 FFmpeg

- **Manter** a rota atual de passada única (filtergraph + reencode) — já é o ótimo de robustez (ver seção 11).
- **Encoder GPU opcional** (resolve C3): se `h264_nvenc` estiver na lista de encoders do toolchain, oferecer checkbox "Usar encoder da placa de vídeo (mais rápido)". Parâmetros: `-c:v h264_nvenc -preset p5 -rc vbr -cq 23 -b:v 0`. `verify_output` continua exigindo `h264` (NVENC produz h264 — passa sem mudança). Fallback silencioso para libx264 se o encoder falhar na validação funcional (testar com render lavfi de 0,1s, mesma técnica de `_detect_filter_file_option`).
- **Análise de energia sem passada extra**: lê o WAV canônico que já existe (2.1).
- **Nenhuma operação temporária nova em disco** além do cache (4.1).

### 4.4 CPU/GPU e progresso

- Modelo em memória entre execuções: já existe (`Transcriber._loaded_key`) — manter.
- Progresso: com cache hit, pular a faixa de transcrição (18–60%) e redistribuir para o render. Faixas passam a ser calculadas por um pequeno mapa `fases = [(nome, peso)]` em vez de números mágicos espalhados.
- Logs: manter o padrão atual (1 linha por marco). Adicionar: cache hit/miss, nº de silêncios detectados, resumo de refinamento ("N bordas ajustadas para silêncio, M para energia mínima").

---

## 5. Nova arquitetura de módulos

Reorganização **pragmática**: pacotes de verdade, mas sem nesting profundo (app pequeno). `src/` vira:

```
src/
  main.py                    # inalterado (entry point)
  putz/
    __init__.py
    config.py                # load/save config v2, migração v1→v2, PRESETS
    models.py                # TODOS os dataclasses compartilhados (seção 6)
    audio_analysis.py        # NOVO — envelope RMS, silêncios, AudioProfile
    transcription.py         # ex-transcriber.py (Transcriber, WordToken sai p/ models.py)
    transcription_cache.py   # NOVO — chave, load/save, LRU
    detection.py             # NOVO — normalize/canonicalize/validate_terms, TermSpec, matcher (níveis 1-3), contexto
    planner.py               # NOVO — pipeline de planejamento v2 (seção 7); absorve a "parte 1" do cutter.py
    refine.py                # NOVO — snapping de bordas por silêncio/energia, pausa retida
    toolchain.py             # ex-cutter.py parte 2a — resolve/valida ffmpeg, probe, extração WAV
    renderer.py              # ex-cutter.py parte 2b — filtergraph (+fades), lotes, progresso, verify
    report.py                # relatório JSON schema v2
    transcript.py            # transcrição .txt (agora por índice de token, não heurística)
    ui/
      __init__.py
      app.py                 # janela Tkinter (ex-gui.py classe PutzCleanerApp)
      worker.py              # run_worker + publicação transacional (ex-gui.py run_worker)
      widgets.py             # Tooltip, helpers
tests/
  conftest.py                # fixtures: tokens fake, WAV sintético, TranscriberFake
  test_detection.py
  test_audio_analysis.py
  test_planner.py
  test_refine.py
  test_config.py
  test_report.py
  test_transcript.py
  test_render_integration.py # marcado @pytest.mark.ffmpeg
```

Responsabilidades, contratos e testes por módulo:

| Módulo | Responsabilidade | Entradas | Saídas | Erros que trata | Testes essenciais |
|---|---|---|---|---|---|
| `config.py` | Config v2 + presets + migração v1 | `config.json` (v1 ou v2) | `AppConfig`, avisos | JSON inválido → padrões + aviso (comportamento v1) | migração v1→v2 preserva termos; preset desconhecido → equilibrado |
| `audio_analysis.py` | Envelope RMS e silêncios do WAV canônico | caminho WAV, params | `AudioProfile` | WAV ilegível/vazio → `AudioAnalysisError`; **falha aqui NÃO aborta o processamento** — degrada para margens fixas v1 + aviso no relatório | silêncio sintético detectado; piso com ruído; bordas do arquivo |
| `detection.py` | TermSpec, matching 3 níveis, contexto | `WordToken[]`, `TermSpec[]`, `AudioProfile`, preset | `RemovalCandidate[]` + `IgnoredOccurrence[]` | termo inválido → `TermValidationError` | todos os casos da seção 10.2 |
| `planner.py` | Orquestra candidatos → validação → refine → fusão → keeps | candidatos, perfil, preset, duração | `CutPlan` | plano vazio, plano remove tudo, invariantes violados → `UnsafeCutPlanError` | invariantes (seção 10.1), property-based |
| `refine.py` | Bordas por silêncio/energia, pausa retida | candidato, `AudioProfile`, vizinhos protegidos | intervalo refinado + `BoundaryInfo` | janela sem dados → fallback margem fixa | snapping, pausa retida, nunca invade protegida |
| `toolchain.py` | ffmpeg/ffprobe/probe/WAV | caminhos | `Toolchain`, `MediaInfo` | idem v1 (`CutterError`) | já coberto por integração |
| `renderer.py` | Filtergraph + fades + NVENC + lotes + verify | `MediaInfo`, `CutPlan`, `RenderSettings` | `RenderResult` | idem v1 + fallback NVENC→libx264 | filtergraph gerado (string), integração lavfi |
| `report.py` | JSON schema 2 | tudo acima | dict serializável | `allow_nan` (v1) | snapshot de schema; consistência duração |
| `transcript.py` | .txt com `[removida]` por índice | tokens, `CutPlan` | texto | — | marcação bate com occurrences |
| `ui/*` | Tk + worker | — | — | idem v1 | smoke de import; `_collect_options` |

Regra de dependência (igual v1, agora explícita): `ui` → todos; `planner` → `detection`, `refine`, `models`; ninguém importa `ui`. `models.py` não importa ninguém.

**Migração dos arquivos**: `git mv` + divisão mecânica, sem mudança de comportamento (Fase 0). Os módulos antigos `src/*.py` deixam de existir; `main.py` passa a importar `putz.ui.app`.

---

## 6. Modelo de dados recomendado (em `putz/models.py`)

Todos `@dataclass(frozen=True)`, como no v1. Campos novos marcados com ★.

### 6.1 `WordToken` (movido de transcriber.py, +1 campo)
```python
class WordToken:
    index: int                      # ★ posição global na transcrição (resolve B6)
    text: str; normalized: str
    start: float | None; end: float | None
    probability: float | None
    segment_id: int
    segment_avg_logprob: float | None
    segment_no_speech_prob: float | None
```

### 6.2 `TermSpec` ★
```python
class TermSpec:
    raw: str                # como o usuário digitou
    canonical: str          # normalize_token(raw)
    folded: str             # sem acentos + colapsado (base p/ nível alongamento)
    is_phrase: bool         # contém espaço
    allow_elongation: bool
    context_mode: str       # "sempre" | "isolado"
    word_class: str         # "vicio_puro" | "lexical"
```

### 6.3 `AudioProfile` ★
```python
class SilenceSpan:  start: float; end: float
class AudioProfile:
    hop_sec: float                  # 0.010
    rms_db: tuple[float, ...]
    noise_floor_db: float
    silence_threshold_db: float     # floor + 8
    silences: tuple[SilenceSpan, ...]
    duration: float
    # métodos: is_silent(t0,t1), silence_around(t)->(SilenceSpan|None,SilenceSpan|None),
    #          local_energy_minimum(t, radius) -> float
```

### 6.4 `RemovalCandidate` ★ (substitui o conceito implícito de "alvo aprovado")
```python
class RemovalCandidate:
    token_indexes: tuple[int, ...]  # 1 palavra ou 2-3 (frase)
    term: TermSpec
    tier: str                       # "exato" | "alongamento" | "frase"
    word_start: float; word_end: float
    probability: float              # min das palavras
    gap_before: float; gap_after: float
    silence_before: SilenceSpan | None
    silence_after: SilenceSpan | None
    isolation: str                  # "isolado" | "semi" | "grudado"
```

### 6.5 `CutInterval` (v1 + rastreabilidade do refinamento ★)
```python
class BoundaryInfo:                 # ★ por borda: como foi decidida
    raw: float                      # antes do refinamento
    refined: float
    method: str                     # "silencio" | "energia_minima" | "margem_fixa" | "protegida"
class CutInterval:
    id: int; start: float; end: float
    occurrence_indexes: tuple[int, ...]
    start_info: BoundaryInfo; end_info: BoundaryInfo   # ★
class KeepInterval:  start: float; end: float          # inalterado
```

### 6.6 `CutPlan` (v1 + campos ★), `RenderSettings` ★, `RenderResult` (inalterado), `ProcessingReport`
```python
class CutPlan:
    occurrences: tuple[CutOccurrence, ...]   # CutOccurrence v1 + token_indexes ★ + tier ★
    ignored: tuple[IgnoredOccurrence, ...]   # v1 + gap_before/after ★ + word_class ★
    cuts: tuple[CutInterval, ...]
    keeps: tuple[KeepInterval, ...]
    expected_output_duration: float
    preset_name: str                         # ★
    audio_profile_used: bool                 # ★ False = degradou p/ margens fixas
class RenderSettings:                        # ★
    use_gpu_encoder: bool
    declick_fades: bool
    fade_duration: float                     # 0.012
```
`ProcessingReport` continua sendo o dict do `report.py` (schema 2) — não vale a pena tipar o JSON inteiro.

**Invariantes obrigatórios do `CutPlan`** (verificados por assert de função `validate_plan(plan, duration)` chamada antes do render e nos testes):
1. cuts ordenados, sem sobreposição, `0 ≤ start < end ≤ duração`;
2. keeps = complemento exato dos cuts (união = timeline, interseção vazia);
3. todo cut ≥ `corte_min` e ≤ `corte_max`;
4. nenhum cut intersecta palavra protegida;
5. `expected_output_duration == Σ keeps` (± EPSILON).

---

## 7. Algoritmo detalhado de planejamento de cortes (v2)

Assinatura central (em `planner.py`):

```python
def build_cut_plan_v2(
    words: Sequence[WordToken],
    terms: Sequence[TermSpec],
    timeline_duration: float,
    preset: Preset,
    audio: AudioProfile | None,     # None => degradar p/ comportamento v1 (margens fixas)
) -> CutPlan
```

Pseudocódigo completo:

```
FASE 1 — SANEAMENTO (herdada do v1, inalterada)
  validar duração, preset, termos
  tokens_validos = clamp + ordenação por (start, end, segment_id)   # cutter.py:_clamp_interval
  separar: possiveis_alvos (casam algum nível de matching) vs protegidas
  # NOVO: a classificação "casa matching" usa detection.match_token/match_phrase
  #       (níveis exato → alongamento → frase, seção 3.2)

FASE 2 — GERAÇÃO DE CANDIDATOS (detection.py)
  para cada token/janela que casou:
    calcular gap_before/gap_after pelos tokens vizinhos (fronteira de segmento = +inf)
    se audio: confirmar gaps com audio.silence_around(); classificar isolation
    emitir RemovalCandidate

FASE 3 — VALIDAÇÃO (detection.py)
  para cada candidato:
    limiar = preset.limiar[term.word_class] (+ offset do usuário, clampado a [0,1])
    se probability < limiar                    → ignorar("baixa_confianca")
    se duração fora da faixa da classe         → ignorar("duracao_implausivel")
    se term.context_mode == "isolado":
        se não satisfaz regra de pausas do preset → ignorar("contexto_nao_isolado")
    se isolation == "grudado" e preset == conservador → ignorar("vicio_grudado_na_fala")
    critérios de segmento do v1 (no_speech, avg_logprob) continuam valendo
    núcleo não pode sobrepor protegida (v1)     → ignorar("sobreposicao_com_palavra_protegida")

FASE 4 — REFINAMENTO DE BORDAS (refine.py)          # substitui margens fixas
  para cada candidato aprovado:
    limite_esq = max(0, fim_da_protegida_anterior)   # invariante v1
    limite_dir = min(dur, inicio_da_protegida_seguinte)
    se audio é None:                                  # degradação
        start = clamp(word_start - margem_v1_antes, limite_esq)
        end   = clamp(word_end + margem_v1_depois, limite_dir); method="margem_fixa"
    senão:
        # borda inicial
        sil = candidato.silence_before
        se sil existe e sil.end >= word_start - 0.05:
            start = max(limite_esq, sil.start + preset.pausa_retida * 1/3)
            se start > word_start: start = word_start          # nunca começar depois do núcleo
            method = "silencio"
        senão:
            start = max(limite_esq, word_start - 0.030)
            start = audio.local_energy_minimum(start, radius=0.020)
            method = "energia_minima"
        # borda final — simétrico com preset.pausa_retida * 2/3 e word_end + 0.030
    se isolation == "grudado": forçar margens mínimas (20-30ms) dos dois lados
    verificar: intervalo cobre o núcleo inteiro; senão → ignorar("margem_eliminou_o_alvo")  # v1
    emitir CutOccurrence(candidate_start=start, candidate_end=end, BoundaryInfo...)

FASE 5 — FUSÃO (planner.py; evolução do _merge_candidates v1)
  ordenar por candidate_start
  fundir consecutivos quando:
      (a) gap <= preset.gap_fusao, OU
      (b) trecho preservado entre eles < 0.25s E audio.is_silent(trecho)
    E a união não intersecta protegida (v1)
  # a checagem de conflito/sobreposição inesperada do v1 permanece (UnsafeCutPlanError)

FASE 6 — LIMITES DE SEGURANÇA
  descartar cortes com duração < preset.corte_min     → ignorar("corte_muito_curto")
      (as ocorrências do corte descartado voltam como ignoradas)
  rejeitar cortes com duração > preset.corte_max      → ignorar("corte_suspeito_longo")
  clamp final a [0, duração]

FASE 7 — KEEPS + VALIDAÇÃO FINAL
  keeps = complemento (v1: _build_keeps)
  se keeps vazio → CutterError("removeria todo o vídeo")   # v1
  validate_plan(plan, duração)   # invariantes da seção 6.6 — falha = bug, aborta
  retornar CutPlan(..., preset_name, audio_profile_used=audio is not None)

FASE 8 — RELATÓRIO EXPLICÁVEL (report.py, schema 2)
  cada ocorrência: tier, classe, gaps, isolation, BoundaryInfo (raw→refined+method)
  cada ignorada: motivo + gaps + classe
  resumo: bordas por método, preset, cache hit/miss, audio_profile_used
```

Complexidade: com `protected` ordenado e busca binária (`bisect`) para vizinhos — O(n log n). O v1 faz varredura linear de `protected` por alvo (O(n·m)); a v2 corrige de graça durante a reescrita.

---

## 8. Alterações na interface (mínimas, para usuário leigo)

Layout atual mantido; mudanças cirúrgicas:

1. **Combobox "Modo:"** no topo das opções: `Conservador | Equilibrado | Agressivo | Personalizado`. Trocar preset atualiza os campos avançados; editar qualquer campo avançado muda para "Personalizado" automaticamente.
2. **Seção "Opções avançadas" recolhível** (frame com toggle ▸/▾, fechada por padrão) contendo: margens (agora rotuladas "margem máxima de busca"), confiança (vira "Ajuste de sensibilidade: −0,2 … +0,2" com o mesmo ⓘ, texto de ajuda reescrito para presets), distância mínima entre cortes (gap de fusão), checkbox **"Detectar silêncio para cortes naturais"** (ligado), checkbox **"Suavizar áudio nas emendas"** (ligado), checkbox **"Usar encoder da placa de vídeo"** (visível só se NVENC detectado), checkbox **"Ignorar cache de transcrição"** (desligado).
3. **Botão "Analisar (sem renderizar)"** ao lado de "Processar vídeo" (seção 4.2). Ao concluir, diálogo-resumo: "Seriam feitos N cortes removendo M s. X ocorrências ignoradas (Y por contexto, Z por confiança). Detalhes no relatório: <caminho>".
4. **Lista de termos**: continua um por linha, mas passa a aceitar espaços (frases) e sufixos opcionais legíveis: `tipo | contexto` marca termo lexical/isolado (parser simples: `texto [| contexto]`). Termos da lista embutida já vêm com metadados certos sem sufixo nenhum — o leigo não precisa saber que isso existe.
5. **O que NÃO fazer**: prévia de vídeo embutida (player Tk é um projeto em si — rejeitado); editor de regex; gráficos de energia. O relatório + transcrição + modo Analisar cobrem a calibração.

---

## 9. Plano de implementação em fases

Regra transversal: **cada fase termina com `python -m pytest` verde, `py_compile` de todos os módulos e um processamento manual de vídeo real curto.** Nenhuma fase quebra a anterior; o app permanece utilizável ao fim de cada uma.

### Fase 0 — Rede de segurança: testes + reorganização sem mudança de comportamento
- **Objetivo**: congelar o comportamento atual em testes de caracterização; dividir os monólitos.
- **Arquivos**: cria `tests/` completo; cria `src/putz/*` movendo código de `transcriber.py`/`cutter.py`/`gui.py` **sem alterar lógica**; `main.py` ajusta imports; adiciona `pytest` a um novo `requirements-dev.txt`.
- **Passos**: (1) escrever testes de caracterização de `normalize_token`, `validate_terms`, `_clamp_interval`, `_confidence_reason`, `build_cut_plan`, `_merge_candidates`, `_build_keeps`, `_build_filtergraph` (string exata), `report.build_report` (snapshot) — **contra o código atual, antes de mover**; (2) mover módulos; (3) rodar os mesmos testes.
- **Aceite**: todos os testes passam antes e depois da movimentação; app abre e processa um vídeo real idêntico ao v1 (mesmo relatório, mesma duração de saída).
- **Riscos**: import quebrado no `.bat`/pythonw → mitigar testando `abrir_putzcleaner.bat`.
- **Estimativa**: 1–2 dias.

### Fase 1 — Cache de transcrição + modo Analisar
- **Objetivo**: matar o custo de iteração (C1, C2) antes de mexer em qualidade — habilita calibrar as fases seguintes rapidamente.
- **Arquivos**: `transcription_cache.py` (novo), `ui/worker.py`, `ui/app.py`, `.gitignore`.
- **Passos**: chave/serialização/LRU; worker consulta cache antes de transcrever e grava depois; parâmetro `analyze_only` no worker (para antes do render, publica `_analise_*.json/.txt` com a mesma transação sem-sobrescrita); botão na UI; log de hit/miss.
- **Aceite**: 2ª execução do mesmo vídeo não transcreve (visível no log e no tempo); Analisar não gera MP4; cache corrompido → miss silencioso + aviso em log (nunca erro fatal).
- **Testes**: chave muda com mtime/modelo/versão; roundtrip de serialização preserva todos os campos de `WordToken`; LRU remove o mais antigo.
- **Riscos**: WordToken serializado divergir do real → teste de roundtrip com valores None/extremos.
- **Estimativa**: 1–2 dias.

### Fase 2 — Detecção v2
- **Objetivo**: matching por alongamento e frase, classes, contexto, limiares por classe, presets (B1–B5, B7).
- **Arquivos**: `detection.py` (novo), `config.py` (schema 2 + migração + PRESETS), `models.py` (TermSpec, RemovalCandidate), `ui/app.py` (combobox de modo — pode entrar já aqui de forma mínima), `report.py` (novos motivos).
- **Passos**: (1) TermSpec + parser da lista da UI + migração do config; (2) níveis de matching com a trava de sequência ≥3; (3) gaps por tokens (sem áudio ainda — contexto usa só gaps do ASR nesta fase); (4) limiares por classe via preset; (5) integrar ao planner mantendo o refinamento antigo (margens fixas).
- **Aceite**: config.json de 45 entradas migra para ~8 TermSpecs equivalentes (teste explícito com o arquivo real); todos os casos de teste da seção 10.2 passam; "um tipo de pessoa" nunca é cortado no preset Equilibrado.
- **Riscos**: regressão de recall (variantes que o exato pegava e o alongamento não) → teste de migração compara cobertura das 45 entradas.
- **Estimativa**: 2–3 dias.

### Fase 3 — Análise de áudio + planejamento inteligente
- **Objetivo**: bordas por silêncio/energia, pausa retida, fusão adaptativa, min/max de corte, isolado vs. grudado (A1–A5, A7).
- **Arquivos**: `audio_analysis.py`, `refine.py` (novos), `planner.py` (pipeline v2 completo, seção 7), `detection.py` (confirmação de gaps por energia), `models.py` (AudioProfile, BoundaryInfo).
- **Passos**: (1) envelope RMS + silêncios + testes com WAV sintético; (2) `refine.py` com snapping e pausa retida; (3) fusão adaptativa e limites; (4) `validate_plan`; (5) worker passa o WAV para análise entre extração e planejamento; (6) degradação graciosa: falha na análise → plano v1 com aviso.
- **Aceite**: nos WAVs sintéticos, 100% das bordas com silêncio disponível usam método "silencio"; nenhum corte < corte_min no plano final; invariantes de `validate_plan` cobertos por property-based test (hypothesis opcional; senão, 500 cenários aleatórios com seed fixo).
- **Riscos**: piso de ruído mal estimado em áudio muito limpo/muito sujo → teste com piso −90dB e −35dB; fallback para margens fixas se <2% do áudio for silêncio (perfil inutilizável).
- **Estimativa**: 3–4 dias. **É a fase de maior valor perceptual.**

### Fase 4 — Renderização natural
- **Objetivo**: micro-fades de áudio + encoder GPU opcional (A6, C3).
- **Arquivos**: `renderer.py`, `models.py` (RenderSettings), `ui/app.py` (checkboxes).
- **Passos**: (1) fades no filtergraph (por segmento, duração conhecida); (2) mesma mudança na rota de lotes; (3) detecção funcional de NVENC + fallback; (4) `verify_output` inalterado.
- **Aceite**: espectrograma/inspeção de junção sem clique em vídeo de teste com corte em não-silêncio; duração de saída idêntica com fades ligados/desligados (±1ms); NVENC ausente → checkbox oculto e render funciona.
- **Riscos**: fade-out com `st` errado silencia o fim do segmento → teste da string do filtergraph com durações conhecidas.
- **Estimativa**: 1–2 dias.

### Fase 5 — Relatórios e UI final
- **Objetivo**: schema 2 completo do relatório, transcrição por índice, UI avançada recolhível, textos de ajuda novos.
- **Arquivos**: `report.py`, `transcript.py`, `ui/*`.
- **Passos**: relatório com tier/classe/gaps/BoundaryInfo/preset; transcrição marca `[removida]` por `token_indexes` (deleta a heurística de tolerância — B6); seção avançada recolhível; diálogo-resumo do Analisar; ⓘ reescrito.
- **Aceite**: relatório valida contra um JSON-schema versionado no repositório (`docs/report_schema_v2.json`); nº de `[removida]` na transcrição == Σ tokens das occurrences; UI abre com avançadas fechadas.
- **Estimativa**: 1–2 dias.

### Fase 6 — Validação final e calibração
- **Objetivo**: provar os critérios de produção (seção 12.5) com vídeos reais.
- **Passos**: (1) montar corpus de validação: 3 vídeos reais de entrevista (curto ~2min, médio ~10min, longo >30min p/ rota de lotes) + 1 vídeo sem vícios + 1 vídeo só de vícios; (2) rodar os 3 presets em cada um; (3) revisar manualmente cada corte do preset Equilibrado (ouvir junções); (4) ajustar constantes de preset conforme achados (só constantes — não lógica); (5) atualizar README; (6) comparar tempo total v1 vs v2 com cache.
- **Aceite**: critérios da seção 12.5 todos verdes; zero falso positivo lexical no corpus com Equilibrado.
- **Estimativa**: 1–2 dias.

**Total estimado: 10–17 dias de trabalho focado.**

---

## 10. Testes e validação

Framework: `pytest` (novo `requirements-dev.txt`: `pytest`, opcional `hypothesis`). Testes de FFmpeg marcados `@pytest.mark.ffmpeg` (rodam se `tools/ffmpeg/bin` existir; CI futura pode pulá-los).

### 10.1 Invariantes (property-based / cenários aleatórios com seed)

Para tokens e termos gerados aleatoriamente, **sempre**:
- keeps ∪ cuts particionam `[0, duração]` sem sobreposição nem buraco;
- nenhum corte intersecta token protegido;
- nenhum corte < corte_min nem > corte_max;
- `expected_output_duration = duração − Σ cortes` (±1e-6);
- plano é determinístico (mesma entrada → mesmo plano, byte a byte no relatório exceto timestamp).

### 10.2 Casos de teste unitários nomeados (exemplos concretos)

| Caso | Entrada | Esperado |
|---|---|---|
| `ne_isolado` | "então **né** deixa eu ver", pausas 0,4s ao redor, prob 0,8 | removido; junção preserva ≥ pausa_retida; borda método "silencio" |
| `tipo_legitimo` | "um **tipo** de pessoa", sem pausas | mantido, motivo `contexto_nao_isolado` |
| `tipo_vicio` | "foi lá... **tipo**... e deu certo", pausas 0,3s | removido |
| `e_alongado` | token "ééé" (run=3), prob 0,3, preset Equilibrado | removido via tier alongamento |
| `e_copula` | token "é" em "isso é bom" | mantido (sem run ≥3, "é" não está na lista exata) |
| `neem_nao_casa` | token "neeem" | colapsa p/ "nem" → não casa base "ne" → mantido |
| `nervoso_nunca` | token "nervoso", termo "né" | mantido (matching por token inteiro) |
| `frase_tipo_assim` | "tipo assim" consecutivos, gap 0,1s, mesmo segmento | removido como frase; prob = min |
| `frase_quebrada` | "tipo" fim de segmento + "assim" início do próximo | não casa frase; avaliados individualmente |
| `rajada_de_ne` | dois "né" com 0,2s de silêncio entre eles | um único corte fundido |
| `ilha_de_fala` | dois vícios com palavra válida de 0,2s entre eles | dois cortes (nunca fundir sobre fala) |
| `corte_50ms` | vício que gera corte de 50ms | descartado, `corte_muito_curto` |
| `alucinacao_3s` | "hum" com end−start = 3s | ignorado, `duracao_implausivel` |
| `grudado_conservador` | "né" sem silêncio ao redor, preset Conservador | mantido, `vicio_grudado_na_fala` |
| `grudado_equilibrado` | idem, preset Equilibrado | removido com margens de 20ms, método "energia_minima" ou "margem_fixa" |
| `sem_audio_profile` | audio=None | plano igual ao v1 (margens fixas), `audio_profile_used=false` |
| `migracao_config_real` | o config.json atual de 45 entradas | ~8 TermSpecs; todo token que casava no v1 continua casando no v2 |

### 10.3 Testes de `audio_analysis` com WAV sintético

Fixture gera WAV 16kHz mono via `numpy`+`wave`: `[0,5s tom 300Hz @ −20dB][0,3s silêncio @ −80dB][0,5s tom][...]`. Verificar: spans de silêncio detectados com erro ≤ 20ms; `local_energy_minimum` cai dentro do silêncio; piso correto com ruído de fundo −50dB.

### 10.4 Integração end-to-end com FFmpeg real (sem ASR)

Injeção de dependência: o worker recebe `transcribe_fn`; o teste injeta um **TranscriberFake** que devolve tokens prontos sobre um vídeo gerado por lavfi (`color` + `sine`, 10s). Verifica:
- duração da saída = esperada (± tolerância do `verify_output`);
- relatório bate com o plano (nº cortes, duração removida);
- com fades ligados: duração idêntica a fades desligados;
- rota de lotes: forçar `MAX_KEEPS_PER_GRAPH=3` via monkeypatch e planejar 7 keeps.

### 10.5 Critérios objetivos de validação (Fase 6, vídeos reais)

1. **Zero falso positivo lexical** no corpus com preset Equilibrado (revisão humana).
2. **Recall ≥ 90%** dos vícios anotados manualmente no vídeo curto (preset Equilibrado).
3. Nenhum corte < corte_min no relatório; nenhuma sobreposição (validado por script sobre o JSON).
4. `duracao_saida_real − duracao_saida_esperada` ≤ tolerância do `verify_output` em 100% das execuções.
5. Junções sem clique audível (escuta com fone) e sem truncamento de fonema perceptível.
6. Transcrição `.txt`: contagem `[removida]` == `resumo.total_ocorrencias` do JSON.
7. Reprocessamento com cache ≥ 10× mais rápido que a primeira execução (vídeo médio, mesma máquina).

---

## 11. Cuidados com FFmpeg (decisões fechadas)

### 11.1 Estratégia de corte — comparação e decisão

| Abordagem | Precisão | Qualidade | Complexidade | Veredito |
|---|---|---|---|---|
| **filter_complex trim/atrim+concat, reencode total** (atual) | Frame-exata | Perda de 1 geração (CRF 20, imperceptível) | Já implementada e testada | ✅ **Manter** |
| concat demuxer com stream copy | Corta só em keyframe (GOP de 2–10s!) | Sem perda | Baixa | ❌ Inviável: cortes de 0,1–1s exigem precisão sub-segundo |
| "Smart cut" (copy no miolo + reencode só nas bordas) | Frame-exata | Quase sem perda | **Alta**: casar parâmetros de encoder, DTS/PTS nas emendas, áudio com priming samples AAC — fonte clássica de A/V drift | ❌ Rejeitada p/ v2 (anotar como v3 se render virar gargalo) |

### 11.2 Regras específicas

- **Sincronização A/V**: a normalização `aresample=async=1:first_pts=0,apad,atrim=0:dur` antes dos `atrim` por segmento (cutter.py:909-912) é o que garante que áudio e vídeo compartilham a mesma base de tempo — **não remover**. Cada segmento renasce com `setpts/asetpts=PTS-STARTPTS` e o `concat` (v=1,a=1) reconstrói timestamps contínuos: sem drift por construção.
- **Fades**: inseridos **depois** do `asetpts` de cada segmento: `afade=t=in:st=0:d=F` e `afade=t=out:st=(DUR_SEG−F):d=F`, com `DUR_SEG = keep.end − keep.start` formatado com 6 casas (padrão `_fmt`). Não alteram duração. Na rota de lotes, aplicar dentro de cada lote (as junções entre lotes coincidem com junções de keeps, então já têm fade).
- **`-ss` antes de `-i` na rota de lotes**: com reencode a busca é exata (decodifica do keyframe anterior e descarta) — correto como está.
- **NVENC**: validar funcionalmente (render lavfi 0,1s com `h264_nvenc`) na resolução do toolchain, não só pela lista de encoders (driver pode estar quebrado). Falhou → esconder opção. `-c:v h264_nvenc -preset p5 -rc vbr -cq 23 -b:v 0 -pix_fmt yuv420p`; áudio/verify inalterados.
- **Cortes imprecisos por frame**: a borda de vídeo cai no frame ≥ start do trim; com fps 30 o erro máximo é 33ms — abaixo da percepção para jump-cut em pausa. Não compensar (compensação por snap-to-frame adicionaria complexidade sem ganho perceptível).
- **Duração esperada vs real**: manter a tolerância atual `max(0.5, 2/fps)` do `verify_output`; os fades não a afetam.

---

## 12. Entregável final

### 12.1 Visão geral da arquitetura final

```
UI (Tk) ──opções/preset──▶ worker
worker: toolchain → probe → WAV canônico ─┬─▶ cache? ──▶ Transcriber (faster-whisper)
                                          └─▶ audio_analysis (RMS/silêncios)
words + TermSpecs + preset + AudioProfile ──▶ detection (match 3 níveis + contexto)
        ──▶ planner (validação → refine bordas → fusão → limites → keeps → validate_plan)
        ──▶ [Analisar? para aqui] renderer (filtergraph + fades + libx264/NVENC) → verify
        ──▶ report v2 + transcript por índice → publicação transacional
```

### 12.2 Checklist de implementação

- [ ] F0: testes de caracterização verdes contra o código atual
- [ ] F0: `src/putz/` criado por movimentação mecânica; app funciona idêntico
- [ ] F1: cache de transcrição com chave/LRU/roundtrip testados
- [ ] F1: botão e fluxo "Analisar (sem renderizar)"
- [ ] F2: TermSpec + migração config v1→v2 (testada com o config real de 45 entradas)
- [ ] F2: matching exato/alongamento/frase com trava de run ≥3
- [ ] F2: limiares por classe + presets + contexto por gaps
- [ ] F3: `audio_analysis` com WAV sintético testado
- [ ] F3: `refine` (silêncio, energia mínima, pausa retida, grudado)
- [ ] F3: fusão adaptativa, corte_min/max, `validate_plan`, degradação sem áudio
- [ ] F4: micro-fades (rota única e lotes) sem mudar duração
- [ ] F4: NVENC opcional com validação funcional e fallback
- [ ] F5: relatório schema 2 + JSON-schema em docs/ + transcrição por índice
- [ ] F5: UI com preset, avançadas recolhíveis, diálogo do Analisar
- [ ] F6: corpus validado, critérios 10.5 verdes, README atualizado

### 12.3 Ordem exata recomendada para outra IA implementar

1. `tests/` de caracterização (F0.1) — **antes de tocar em qualquer módulo**.
2. Movimentação para `src/putz/` (F0.2-3), rodar testes.
3. `transcription_cache.py` + integração no worker (F1.1).
4. Modo Analisar (F1.2).
5. `models.py`: TermSpec/RemovalCandidate; `config.py`: schema 2 + migração + PRESETS (F2.1).
6. `detection.py`: matching e contexto por gaps (F2.2-4); ligar no planner com refine antigo (F2.5).
7. `audio_analysis.py` (F3.1) → `refine.py` (F3.2) → `planner.py` v2 completo (F3.3-6).
8. `renderer.py`: fades (F4.1-2) → NVENC (F4.3).
9. `report.py` v2 + `transcript.py` por índice (F5.1-2) → UI final (F5.3-5).
10. Fase 6 inteira (validação com vídeos reais e calibração de constantes).

Nunca implementar fora dessa ordem: cada passo depende dos tipos/testes do anterior.

### 12.4 Resumo das decisões técnicas

| Decisão | Escolha | Alternativa rejeitada e por quê |
|---|---|---|
| Análise de silêncio | RMS numpy sobre o WAV canônico | `silencedetect` (passada FFmpeg extra, sem envelope contínuo) |
| Borda de corte | snap a silêncio → energia mínima → margem fixa | zero-crossing por amostra (over-engineering; fades mascaram) |
| Suavização de áudio | micro-fades 12ms por segmento | `acrossfade` (consome duração → dessincroniza A/V) |
| Matching de variantes | tier alongamento com trava run≥3 + fold de acentos | regex de usuário (risco de falso positivo p/ leigo); fuzzy (idem) |
| Falso positivo lexical | contexto por pausas confirmadas por energia + limiar por classe | NLP/POS-tagging (dependência pesada, ganho marginal) |
| Performance de iteração | cache de transcrição + modo Analisar | reduzir modelo/beam (degrada qualidade da detecção) |
| Render | manter reencode total; NVENC opcional | smart-cut copy+bordas (complexidade/risco de drift) |
| Vídeo nas junções | jump-cut seco | xfade/morph (fora do escopo, muda linguagem visual) |
| Config | schema 2 com migração automática e silenciosa | quebrar compatibilidade (usuário perderia a lista) |

### 12.5 Critérios de "pronto para produção"

1. Suíte `pytest` completa verde (unit + invariantes + integração FFmpeg) em máquina limpa via `setup.bat`.
2. Os 7 critérios objetivos da seção 10.5 atendidos no corpus da Fase 6.
3. Config v1 real migra sem perda (as 45 entradas atuais continuam cobertas).
4. Processamento v1-equivalente disponível por degradação (áudio-análise desligada/indisponível) — nunca pior que hoje.
5. Cancelamento e fechamento seguro funcionam em todas as fases novas (cache, análise, fades).
6. Nenhuma sobrescrita de arquivo do usuário em nenhum caminho de código novo (auditar `os.rename`/staging).
7. README descreve presets, modo Analisar e opções avançadas em linguagem leiga.
