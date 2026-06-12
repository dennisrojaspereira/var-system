"""API de suporte a decisao do VAR (FastAPI).

Expoe a sala do VAR como servico:
  GET  /health                       -> status do sistema e backends
  GET  /cameras                      -> cameras configuradas
  GET  /timeline/{cam}               -> segmentos no buffer (duracao, replay)
  GET  /timeline/{cam}/at?t=...      -> segmento que cobre o instante t
  POST /review                       -> inicia revisao (publica evento)
  POST /analyze                      -> roda visao (YOLO) sobre um video e
                                        retorna trajetoria + contatos detectados
  GET  /sync/align                   -> mapeia um instante entre dois angulos
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from ..buffer import TimelineBuffer
from ..config import Config, load_config
from ..events import EventBus, make_event
from ..storage import Storage
from ..sync import SyncManager


def _require_camera(config: Config, camera_id: str) -> None:
    try:
        config.camera(camera_id)
    except KeyError:
        raise HTTPException(404, f"Camera '{camera_id}' nao configurada")


class ReviewRequest(BaseModel):
    camera_id: str
    t_seconds: float
    reason: str = "incident-review"


class AnalyzeRequest(BaseModel):
    video_path: str
    step: int = 3  # subamostragem padrao p/ velocidade
    camera_id: str | None = None  # usada para timestamp absoluto/persistencia


def create_app(config: Config | None = None, storage: Storage | None = None) -> FastAPI:
    config = config or load_config()
    app = FastAPI(title="VAR Decision Support API", version="0.1.0")
    bus = EventBus(config)
    sync = SyncManager(config)
    storage = storage or Storage(config)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "match": config.match,
            "event_backend": bus.backend_in_use,
            "database": storage.available(),
            "cameras": [c.id for c in config.cameras],
        }

    @app.get("/cameras")
    def cameras() -> list[dict[str, Any]]:
        return [
            {"id": c.id, "angle": c.angle, "source": c.source, "fps": c.fps}
            for c in config.cameras
        ]

    @app.get("/timeline/{camera_id}")
    def timeline(camera_id: str) -> dict[str, Any]:
        _require_camera(config, camera_id)
        buf = TimelineBuffer(camera_id, config)
        segs = buf.segments()
        return {
            "camera_id": camera_id,
            "duration_s": buf.duration(),
            "segments": [
                {"index": s.index, "start": s.start, "duration": s.duration,
                 "file": s.path.name}
                for s in segs
            ],
        }

    @app.get("/timeline/{camera_id}/at")
    def timeline_at(camera_id: str, t: float) -> dict[str, Any]:
        _require_camera(config, camera_id)
        seg = TimelineBuffer(camera_id, config).segment_at(t)
        if seg is None:
            raise HTTPException(404, f"Nenhum segmento cobre t={t}s")
        return {
            "camera_id": camera_id,
            "t": t,
            "segment": {"index": seg.index, "start": seg.start,
                        "duration": seg.duration, "file": seg.path.name},
            "absolute_time": sync.absolute_at(camera_id, t).isoformat(),
        }

    @app.get("/sync/align")
    def align(source: str, target: str, t: float) -> dict[str, Any]:
        _require_camera(config, source)
        _require_camera(config, target)
        t_target = sync.align(source, t, target)
        return {
            "source": source, "target": target,
            "t_source": t, "t_target": round(t_target, 4),
            "absolute_time": sync.absolute_at(source, t).isoformat(),
        }

    @app.post("/review")
    def start_review(req: ReviewRequest) -> dict[str, Any]:
        _require_camera(config, req.camera_id)
        absolute = sync.absolute_at(req.camera_id, req.t_seconds).isoformat()
        bus.publish(make_event(
            "VAR_REVIEW_STARTED", config, camera_id=req.camera_id,
            t_seconds=req.t_seconds, reason=req.reason, absolute_time=absolute,
        ))
        return {"status": "review_started", "camera_id": req.camera_id,
                "absolute_time": absolute}

    @app.post("/analyze")
    def analyze(req: AnalyzeRequest) -> dict[str, Any]:
        path = config.resolve(req.video_path)
        if not Path(path).exists():
            raise HTTPException(404, f"Video nao encontrado: {path}")
        camera_id = req.camera_id or config.cameras[0].id
        _require_camera(config, camera_id)
        # Import tardio: ultralytics/torch sao pesados.
        from ..vision import analyze_video
        result = analyze_video(path, config=config, step=req.step)
        traj = result.trajectory
        for contact in traj.contacts:
            bus.publish(make_event(
                "BALL_CONTACT_DETECTED", config, camera_id=camera_id,
                frame=contact.frame, t_seconds=contact.timestamp,
                x=contact.x, y=contact.y,
                direction_change_deg=contact.direction_change_deg,
            ))
        persisted = _persist_trajectory(config, storage, sync, camera_id, traj)
        out = result.to_dict()
        out["persisted_detections"] = persisted
        return out

    @app.get("/videos")
    def videos() -> list[dict[str, Any]]:
        samples = Path(config.resolve("samples"))
        if not samples.is_dir():
            return []
        return [
            {"name": f.name, "url": f"/videos/{f.name}",
             "size_kb": round(f.stat().st_size / 1024, 1)}
            for f in sorted(samples.glob("*.mp4"))
        ]

    @app.get("/videos/{filename}")
    def video_file(filename: str) -> FileResponse:
        if "/" in filename or "\\" in filename or ".." in filename:
            raise HTTPException(400, "Nome de arquivo invalido")
        path = Path(config.resolve("samples")) / filename
        if not path.is_file():
            raise HTTPException(404, f"Video nao encontrado: {filename}")
        return FileResponse(path, media_type="video/mp4")

    @app.get("/viewer", response_class=HTMLResponse)
    def viewer() -> str:
        return _VIEWER_HTML

    @app.get("/trajectory/{camera_id}")
    def stored_trajectory(camera_id: str, t0: str, t1: str) -> dict[str, Any]:
        """Consulta a trajetoria persistida (t0/t1 em ISO-8601 UTC).
        Sempre escopada por match_id + camera_id - o padrao de acesso que
        mantem as queries num unico shard quando distribuido (Citus)."""
        _require_camera(config, camera_id)
        if not storage.available():
            raise HTTPException(503, "Banco de dados indisponivel")
        from datetime import datetime
        match_id = config.match.get("id", "unknown-match")
        points = storage.trajectory(
            match_id, camera_id,
            datetime.fromisoformat(t0), datetime.fromisoformat(t1),
        )
        for p in points:
            p["time"] = p["time"].isoformat() if hasattr(p["time"], "isoformat") else p["time"]
        return {"match_id": match_id, "camera_id": camera_id, "points": points}

    return app


_VIEWER_HTML = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>VAR Review Room</title>
<style>
  body { background:#0d1117; color:#e6edf3; font-family:Segoe UI,system-ui,sans-serif;
         margin:0; padding:24px; }
  h1 { font-size:20px; margin:0 0 4px; }
  h1 span { color:#f0c000; }
  .sub { color:#8b949e; font-size:13px; margin-bottom:20px; }
  .wrap { display:flex; gap:24px; flex-wrap:wrap; }
  video { max-width:760px; width:100%; background:#000; border-radius:8px;
          border:1px solid #30363d; }
  .panel { min-width:260px; flex:1; }
  .item { padding:10px 12px; border:1px solid #30363d; border-radius:8px;
          margin-bottom:8px; cursor:pointer; font-size:14px; }
  .item:hover { background:#161b22; }
  .item.active { border-color:#f0c000; background:#161b22; }
  .size { color:#8b949e; font-size:12px; }
  .legend { margin-top:16px; font-size:13px; color:#8b949e; line-height:1.8; }
  .dot { display:inline-block; width:10px; height:10px; border-radius:2px;
         margin-right:6px; }
</style>
</head>
<body>
<h1><span>&#9679;</span> VAR Review Room</h1>
<div class="sub">Deteccao YOLOv8 &middot; tracking da bola &middot; contatos por mudanca de direcao</div>
<div class="wrap">
  <div><video id="player" controls autoplay loop muted></video></div>
  <div class="panel">
    <div id="list">carregando...</div>
    <div class="legend">
      <div><span class="dot" style="background:#ffd700"></span>caixa amarela: bola (YOLO "sports ball")</div>
      <div><span class="dot" style="background:#00dcff"></span>caixa ciano: jogador</div>
      <div><span class="dot" style="background:#78ff50"></span>trilha verde: trajetoria rastreada</div>
      <div><span class="dot" style="background:#ff3c3c"></span>circulo vermelho: contato detectado</div>
    </div>
  </div>
</div>
<script>
const player = document.getElementById('player');
const list = document.getElementById('list');
fetch('/videos').then(r => r.json()).then(videos => {
  if (!videos.length) { list.textContent = 'nenhum video em samples/'; return; }
  list.innerHTML = '';
  videos.forEach((v, i) => {
    const div = document.createElement('div');
    div.className = 'item';
    div.innerHTML = v.name + ' <span class="size">(' + v.size_kb + ' KB)</span>';
    div.onclick = () => {
      player.src = v.url;
      document.querySelectorAll('.item').forEach(e => e.classList.remove('active'));
      div.classList.add('active');
    };
    list.appendChild(div);
    const preferred = v.name.includes('annotated') || v.name.includes('anotado');
    if (i === 0 || preferred) div.onclick();
  });
});
</script>
</body>
</html>"""


def _persist_trajectory(config: Config, storage: Storage, sync: SyncManager,
                        camera_id: str, traj) -> int:
    """Grava os pontos da bola em detections. Retorna quantos persistiu (0 se
    o banco estiver desligado/indisponivel - a analise nunca falha por isso)."""
    if not config.section("storage").get("persist_detections", True):
        return 0
    if not storage.available():
        return 0
    try:
        match = config.match
        match_id = match.get("id", "unknown-match")
        storage.ensure_schema()
        storage.upsert_match(match_id, match.get("name", match_id))
        cam = config.camera(camera_id)
        storage.upsert_camera(match_id, cam.id, cam.angle, cam.fps)
        rows = [
            (sync.absolute_at(camera_id, p.timestamp), match_id, camera_id,
             p.frame, "sports ball", p.confidence, p.x, p.y)
            for p in traj.points
        ]
        return storage.insert_detections(rows)
    except Exception as exc:
        print(f"[api] persistencia falhou (analise segue valida): {exc}")
        return 0
