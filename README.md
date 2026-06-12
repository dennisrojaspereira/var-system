# VAR System

Sistema modular tipo VAR (Video Assistant Referee) construido com **FFmpeg,
OpenCV, YOLOv8 e Python**. Implementa o fluxo completo: ingestao de cameras ->
timeline buffer navegavel -> visao computacional (deteccao de bola/jogadores e
tracking) -> sincronizacao de cameras -> event bus -> API de suporte a decisao.

## Arquitetura

```
Cameras --(FFmpeg)--> Timeline Buffer (HLS) --> VAR Review Room
                              |                        |
                              v                        v
                       Frame Extractor           Decision API (FastAPI)
                              |                        |
                              v                        v
                       YOLO Detection           Event Bus (Kafka/file)
                              |
                              v
                    Ball & Player Tracking
```

## Modulos (`var/`)

| Modulo                | Responsabilidade                                              |
| --------------------- | ------------------------------------------------------------ |
| `config.py`           | Carrega `config.yaml` de forma tipada                        |
| `ingestion/`          | FFmpeg: RTSP/SRT/arquivo/webcam -> segmentos HLS             |
| `buffer/`             | Indexa segmentos HLS, replay por instante de tempo          |
| `vision/`             | `frame_extractor` (OpenCV), `detector` (YOLO), `tracker`, `pipeline` |
| `sync/`               | Master clock (PTP/SMPTE simulado), alinhamento entre angulos |
| `events/`             | Event bus: Kafka com fallback para JSONL/console            |
| `api/`                | API FastAPI de suporte a decisao                            |

## Instalacao

```bash
cd var-system
python -m pip install -r requirements.txt
```

> **FFmpeg**: necessario apenas para a ingestao ao vivo (modulo `ingestion`).
> Baixe em https://ffmpeg.org/download.html e adicione ao PATH. Os demais
> modulos (visao, buffer, sync, eventos, API) funcionam sem ele.
>
> **YOLOv8**: os pesos `yolov8n.pt` sao baixados automaticamente pela
> ultralytics no primeiro `analyze`.

## Uso rapido (CLI)

```bash
# 1. Gerar um video sintetico de teste (nao precisa de footage real)
python -m var.cli sample --output samples/cam07.mp4

# 2. Rodar visao computacional: deteccao + tracking + contatos
python -m var.cli analyze samples/cam07.mp4 --step 2 --json out.json

# 3. Alinhar um instante entre dois angulos (sincronizacao)
python -m var.cli sync cam-01 cam-07 12.5

# 4. Subir a API de decisao
python -m var.cli api
# -> http://127.0.0.1:8000/docs

# 5. Ingestao ao vivo (requer FFmpeg + fontes em config.yaml)
python -m var.cli ingest

# 6. Inspecionar a timeline de uma camera (apos ingestao)
python -m var.cli timeline cam-07
```

## API (principais rotas)

| Metodo | Rota                       | Descricao                                  |
| ------ | -------------------------- | ------------------------------------------ |
| GET    | `/health`                  | Status do sistema e backend de eventos     |
| GET    | `/cameras`                 | Cameras configuradas                       |
| GET    | `/timeline/{cam}`          | Segmentos no buffer                        |
| GET    | `/timeline/{cam}/at?t=`    | Segmento que cobre o instante t            |
| GET    | `/sync/align?source=&target=&t=` | Mapeia instante entre angulos        |
| POST   | `/review`                  | Inicia revisao (publica evento)            |
| POST   | `/analyze`                 | Roda YOLO+tracking, retorna trajetoria     |

## Testes

```bash
python tests/test_tracker.py
python tests/test_buffer_sync.py
# ou, com pytest instalado:
python -m pytest tests/
```

## Configuracao

Tudo e controlado por `config.yaml`: cameras e fontes, parametros HLS, modelo
YOLO e limiares de confianca, modo de sincronizacao e backend de eventos.

## Notas de producao

- **Buffer**: aqui em disco local. Em producao, considere NVMe de alta
  performance, S3/MinIO ou storage de video dedicado conforme a latencia.
- **Sincronizacao**: o modulo modela o contrato de master clock. Em campo,
  use PTP (IEEE 1588), GPS Time Source e SMPTE ST 2059 no hardware.
- **Eventos**: o fallback grava JSONL auditavel. Para Kafka real, instale
  `kafka-python` e ajuste `events.backend: kafka` no `config.yaml`.
- **Modelo**: `yolov8n.pt` (COCO) detecta `sports ball`/`person` de forma
  generica. Para precisao de VAR, treine um modelo dedicado (bola, jogadores,
  arbitros, linhas do campo).
```
