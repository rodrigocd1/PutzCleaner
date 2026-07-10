# PutzCleaner

## O que é o PutzCleaner

O PutzCleaner é um aplicativo desktop local para Windows que remove
automaticamente vícios de fala (como "né", "hum", "tipo") de vídeos de
entrevista. Ele foi pensado para agilizar a edição de entrevistas do
Putzforce: você seleciona um `.mp4`, revisa a lista de palavras, e o programa
transcreve o áudio, identifica as ocorrências e gera um novo vídeo sem esses
trechos, além de um relatório auditável de tudo o que foi cortado.

O vídeo original nunca é modificado.

## Privacidade

Todo o processamento é local. A transcrição (faster-whisper) e a renderização
(FFmpeg) acontecem no seu computador. **Nenhum vídeo ou áudio é enviado para a
internet.** A conexão só é usada uma vez, durante a instalação, para baixar as
bibliotecas, o FFmpeg e os pesos do modelo de transcrição. Depois que o modelo
escolhido estiver armazenado, o processamento funciona offline.

## Requisitos

- Windows 10 ou 11, 64 bits.
- Python 3.11 de 64 bits, instalado pelo site oficial, com Tkinter (que já vem
  na instalação padrão do Windows).

Python é o único pré-requisito manual. Todo o resto é instalado pelo
`setup.bat` dentro da própria pasta do projeto.

## Instalação

1. Dê um duplo clique em `setup.bat`.
2. O script vai, nesta ordem: localizar o Python 3.11, criar um ambiente
   virtual `.venv`, instalar as dependências, baixar e verificar o FFmpeg
   (conferindo o hash SHA-256) e pré-carregar o modelo `small`.
3. A instalação exige internet e pode demorar no primeiro uso (o download do
   modelo é o passo mais lento).
4. A instalação só é considerada concluída quando aparece
   `Instalacao concluida com sucesso`. O script é idempotente: se você rodar de
   novo, ele reaproveita o que já estiver válido.

## Como abrir

Dê um duplo clique em `abrir_putzcleaner.bat`. Se o setup ainda não tiver sido
concluído, o launcher chama o `setup.bat` automaticamente. A janela abre sem um
console permanente.

## Como usar

1. Clique em **Selecionar vídeo** e escolha um `.mp4`.
2. Revise a **lista de palavras/sons** a remover (uma por linha).
3. Escolha o **modelo** (`small`, `medium` ou `large`).
4. Ajuste as **margens antes/depois** se quiser.
5. Opcionalmente, escolha uma **pasta de saída** (por padrão, a mesma do vídeo).
6. Clique em **Processar vídeo** e acompanhe o progresso e os logs.

## Como editar as palavras

- Uma palavra ou som por linha.
- O reconhecimento é **exato** sobre a forma normalizada (minúsculas, sem
  pontuação nas pontas). Não há busca por substring nem correspondência
  aproximada: `hum` não corresponde a `humano`, `tipo` não corresponde a
  `tipologia`.
- **Acentos importam.** `ã` é diferente de `a` e `ééé` é diferente de `é`. Isso
  é proposital: normalizar acentos faria o programa cortar palavras legítimas
  como o artigo "a" ou o verbo "é".

## Modelos

- `small`: mais leve e rápido; é o padrão e vem pré-carregado.
- `medium`: mais lento, geralmente mais preciso.
- `large`: mapeia para `large-v3`; pesado em CPU (use GPU se possível) e pode
  exigir bastante memória RAM.

O primeiro uso de `medium` ou `large` faz o download dos pesos automaticamente.

Se o modelo `small` não estiver detectando vícios que a pessoa claramente
falou, suba para `medium` ou `large`: modelos maiores reconhecem melhor
interjeições curtas como "né". Confira também a seção `ignorados` do relatório
JSON para ver se a palavra foi detectada mas descartada (e por qual motivo).

## Processamento: CPU ou GPU

No campo **Processar em** você escolhe onde a transcrição roda:

- **auto** (padrão): usa a GPU NVIDIA se ela estiver disponível e funcional;
  caso contrário, usa a CPU automaticamente.
- **cpu**: força a CPU, usando **todos os núcleos** do processador.
- **cuda**: força a GPU NVIDIA.

A transcrição em CPU já usa todos os núcleos disponíveis. Ainda assim, para
modelos `medium`/`large` a GPU é muito mais rápida.

### Habilitar a GPU (NVIDIA)

A GPU exige uma placa NVIDIA com drivers atualizados e as bibliotecas CUDA
(cuBLAS e cuDNN). Para instalá-las:

1. Dê um duplo clique em `instalar_gpu.bat` (baixa cerca de 1,3 GB).
2. Ao final, ele confirma se a GPU foi detectada.
3. Na janela do PutzCleaner, escolha **Processar em: cuda** (ou deixe em
   **auto**).

Se a GPU não estiver disponível ou as bibliotecas faltarem, o modo **auto**
simplesmente usa a CPU; o modo **cuda** mostra um erro explicando o que falta.

## Margens

As margens ampliam o corte em torno de cada palavra detectada. Valores maiores
removem mais conteúdo ao redor da palavra (útil para pegar a respiração antes
ou depois), mas também aumentam o risco de cortar fala vizinha. As margens são
limitadas a valores entre 0 e 2 segundos. A proteção das palavras vizinhas
impede que a margem invada uma palavra legítima adjacente.

## Onde os arquivos são salvos

Por padrão, na mesma pasta do vídeo de entrada. Se você escolher uma pasta de
saída, ela é salva na configuração e reutilizada nas próximas execuções.

## Nomes de saída

Para `entrevista.mp4`, são gerados:

- `entrevista_limpo.mp4` — o vídeo sem os vícios (H.264 / AAC).
- `entrevista_limpo_relatorio.json` — o relatório auditável.
- `entrevista_limpo_transcricao.txt` — a transcrição reconhecida, agrupada por
  trechos com os tempos do vídeo original, marcando com `[removida]` cada
  palavra que foi cortada do vídeo limpo.

Se já existir um arquivo com esse nome, o programa **para e não sobrescreve
nada**. Renomeie/mova o arquivo existente ou escolha outra pasta.

## Como testar com um vídeo curto

1. Prefira um vídeo de 20 a 60 segundos, de preferência uma cópia.
2. Escolha um trecho com vícios de fala conhecidos.
3. Processe com o modelo `small` e as margens/lista padrão.

## Como validar o resultado

- Abra o relatório JSON e confira as ocorrências, os timestamps e o total de
  cortes.
- Ouça/veja cerca de 2 segundos antes e depois de cada corte listado.
- Confira o sincronismo entre áudio e imagem no começo e no fim.
- Confirme que o vídeo original continua intacto.

## Solução de problemas

- **FFmpeg/ffprobe não encontrado ou sem H.264/AAC**: rode o `setup.bat`
  novamente.
- **Python 3.11 não encontrado**: instale pelo site oficial (64 bits, com
  Tkinter) e rode o setup de novo.
- **Falha de rede na instalação**: o setup mostra a etapa que falhou; basta
  executá-lo novamente com conexão.
- **Modelo não baixa**: o download inicial de cada modelo exige internet.
- **Falta de memória RAM**: use um modelo menor (`small`/`medium`); o programa
  não faz downgrade silencioso.
- **Pasta sem permissão de escrita**: escolha outra pasta de saída.
- **Saída já existente**: renomeie/mova o arquivo ou troque a pasta.
- **config.json inválido**: o programa carrega padrões em memória e pede
  confirmação antes de sobrescrever o arquivo.

## Limitações conhecidas

- **Falsos positivos** podem remover fala legítima.
- **Falsos negativos** podem manter alguns vícios.
- `tipo` e `assim` podem ser palavras semanticamente necessárias em algumas
  frases; avalie manualmente.
- Ruído, música, sotaques e duas pessoas falando juntas reduzem a precisão. Não
  há diarização (identificação de falantes); a proteção é apenas temporal.
- O Whisper pode transcrever `ã` como `a` ou `ééé` como `é`; o app não usa
  aliases agressivos para não cortar palavras legítimas.
- Os cortes são "secos" (sem crossfade) e podem soar/parecer abruptos ou causar
  microcliques.
- Margens grandes removem conteúdo útil.
- O vídeo é quantizado por frames, então o corte visual não é infinitamente
  preciso.
- `medium`/`large` podem ser muito lentos ou exceder a RAM em CPU.
- A primeira utilização de cada modelo exige download.
- A extração do WAV usa cerca de 115 MB por hora de vídeo; a rota em lotes
  (entrevistas com muitos cortes) usa espaço temporário adicional.
- Apenas a primeira faixa de áudio é usada.
- Legendas, capítulos, metadados e faixas adicionais não são preservados.
- Vídeos HDR/10-bit são convertidos para H.264 `yuv420p` de ampla
  compatibilidade, podendo perder alcance de cor.
- VFR, rotação incomum e offsets de stream exigem atenção; um offset entre
  áudio e imagem acima de 0,05 s é rejeitado por segurança.
- Não há revisão prévia (timeline) dos cortes nesta versão.
