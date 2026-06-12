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

## Docker (stack completa com Kafka)

Sobe Kafka (KRaft, sem Zookeeper), Kafka UI, a API de decisao e um consumer de
eventos. O `EventBus` passa a usar Kafka via variaveis de ambiente
(`VAR_EVENTS_BACKEND=kafka`, `VAR_KAFKA_BOOTSTRAP=kafka:9094`).

```bash
# 1. (opcional) gerar um video de amostra para a ingestao
python -m var.cli sample --output samples/cam07.mp4

# 2. subir kafka + kafka-ui + api + consumer
docker compose up -d --build

# 3. ver os servicos
#    API de decisao .... http://localhost:8000/docs
#    Kafka UI .......... http://localhost:8080

# 4. disparar uma revisao -> evento publicado no Kafka, lido pelo consumer
curl -X POST http://localhost:8000/review \
  -H "content-type: application/json" \
  -d '{"camera_id":"cam-07","t_seconds":12.5,"reason":"penalty-check"}'

docker compose logs -f consumer      # ve o evento chegando via Kafka

# 5. (opcional) ingestao FFmpeg ao vivo dentro do container
docker compose --profile ingest up -d

# 6. (opcional) imagem com YOLO/torch para o endpoint /analyze
INSTALL_ML=true docker compose build api
docker compose up -d api

# encerrar
docker compose down            # (use -v para limpar tambem o volume do Kafka)
```

| Servico       | Porta | Funcao                                       |
| ------------- | ----- | -------------------------------------------- |
| `kafka`       | 9092  | Broker (KRaft). Interno: `kafka:9094`        |
| `kafka-ui`    | 8080  | Observabilidade dos topicos/eventos          |
| `db`          | 5432  | PostgreSQL + TimescaleDB (`var`/`var`)       |
| `api`         | 8000  | API de decisao (FastAPI)                     |
| `consumer`    | -     | Consome `var.events` (audit/broadcast)       |
| `events-sink` | -     | Kafka -> Postgres (tabela `var_events`)      |
| `ingest`      | -     | Ingestao FFmpeg (profile `ingest`)           |

> A imagem base nao inclui torch/ultralytics (pesados). O endpoint `/analyze` so
> funciona na imagem ML (`INSTALL_ML=true`); os demais modulos rodam na base.
> Variaveis de override: veja `var/config.py:_apply_env_overrides`.

## Persistencia (PostgreSQL + TimescaleDB)

O modulo `var/storage/` persiste tres cargas distintas (videos ficam fora do
banco, em disco/S3):

| Tabela       | Conteudo                            | Tipo                       |
| ------------ | ----------------------------------- | -------------------------- |
| `matches`    | Partidas                            | Relacional                 |
| `cameras`    | Cameras por partida                 | Relacional                 |
| `detections` | Posicoes da bola/jogadores (YOLO)   | **Hypertable** Timescale   |
| `var_events` | Auditoria de eventos (sink Kafka)   | Append-only                |

- `dsn` vazio em `config.yaml` desliga a persistencia (a analise nunca falha
  por banco indisponivel). Override: `VAR_DB_DSN`.
- O `/analyze` grava a trajetoria da bola em `detections`; consulte via
  `GET /trajectory/{cam}?t0=...&t1=...` (ISO-8601).
- **Sharding**: o schema e Citus-ready - todas as tabelas de volume carregam
  `match_id`, a chave de distribuicao natural. Cada partida vive inteira num
  shard, e as queries de revisao (sempre escopadas por partida) nunca cruzam
  shards. Num unico Postgres, o particionamento por tempo do TimescaleDB ja
  cobre o crescimento; migre para Citus apenas em escala multi-torneio.

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
python -m pytest tests/        # suite completa (tracker, buffer, sync, storage)
```

Os testes de regressao rodam automaticamente em dois pontos:

1. **A cada commit (local)** — hook `pre-commit` em `.githooks/`. Ative uma vez
   por clone com:
   ```bash
   git config core.hooksPath .githooks
   ```
   Se a suite falhar, o commit e abortado (`--no-verify` pula em emergencia).
2. **A cada push/PR (GitHub Actions)** — `.github/workflows/ci.yml` roda a
   suite com TimescaleDB real (o teste de integracao executa de verdade),
   smoke test da CLI e build da imagem Docker.

## Seguranca (SAST + DAST)

`.github/workflows/security.yml` roda a cada push/PR e semanalmente (pega CVEs
novos mesmo sem commits). Tudo gratuito em repositorio publico:

| Job         | Ferramenta            | Tipo                                  |
| ----------- | --------------------- | ------------------------------------- |
| `codeql`    | GitHub CodeQL         | SAST profundo (queries security-extended) |
| `sast`      | Bandit + pip-audit    | SAST rapido + CVEs nas dependencias   |
| `container` | Trivy                 | CVEs na imagem Docker                 |
| `dast`      | OWASP ZAP baseline    | DAST contra a API rodando em CI       |

Trivy e ZAP estao em modo report-only (nao bloqueiam o build); os resultados do
CodeQL aparecem na aba **Security > Code scanning** do GitHub.

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
