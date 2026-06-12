"""CLI unificada do sistema VAR.

Uso:
  python -m var.cli ingest                 # inicia ingestao FFmpeg de todas as cameras
  python -m var.cli timeline cam-07        # lista segmentos no buffer
  python -m var.cli analyze video.mp4      # roda YOLO + tracking, imprime trajetoria
  python -m var.cli sync cam-01 cam-07 12  # alinha t=12s de cam-01 em cam-07
  python -m var.cli api                    # sobe a API de decisao (FastAPI)
  python -m var.cli sample                 # gera um video sintetico de teste
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from .config import load_config


def cmd_ingest(args) -> int:
    from .ingestion import ffmpeg_available, ingest_all
    if not ffmpeg_available():
        print("ERRO: ffmpeg nao encontrado no PATH. Instale o FFmpeg.", file=sys.stderr)
        return 1
    config = load_config()
    print(f"Iniciando ingestao de {len(config.cameras)} camera(s)...")
    ingestors = ingest_all(config)
    print("Ingestao ativa. Ctrl+C para parar.")
    try:
        while any(i.is_running() for i in ingestors):
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nParando ingestao...")
    finally:
        for i in ingestors:
            i.stop()
    return 0


def cmd_timeline(args) -> int:
    from .buffer import TimelineBuffer
    config = load_config()
    buf = TimelineBuffer(args.camera, config)
    segs = buf.segments()
    print(f"Camera {args.camera}: {len(segs)} segmento(s), {buf.duration():.1f}s no buffer")
    for s in segs:
        print(f"  #{s.index:05d}  [{s.start:7.2f} -> {s.end:7.2f}]  {s.path.name}")
    return 0


def cmd_analyze(args) -> int:
    from .vision import analyze_video
    config = load_config()
    print(f"Analisando {args.video} (step={args.step})...")
    result = analyze_video(args.video, config=config, step=args.step)
    print(json.dumps(result.to_dict()["trajectory"]["summary"], indent=2))
    traj = result.trajectory
    if traj.contacts:
        print(f"\nContatos detectados ({len(traj.contacts)}):")
        for c in traj.contacts:
            print(f"  frame {c.frame} t={c.timestamp:.3f}s "
                  f"angulo={c.direction_change_deg}graus "
                  f"({c.speed_before:.0f}->{c.speed_after:.0f} px/s)")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result.to_dict(), fh, indent=2)
        print(f"\nResultado completo salvo em {args.json}")
    return 0


def cmd_sync(args) -> int:
    from .sync import SyncManager
    config = load_config()
    mgr = SyncManager(config)
    t_target = mgr.align(args.source, args.t, args.target)
    print(f"{args.source} t={args.t}s  ==  {args.target} t={t_target:.4f}s")
    print(f"tempo absoluto: {mgr.absolute_at(args.source, args.t).isoformat()}")
    return 0


def cmd_api(args) -> int:
    import uvicorn
    from .api import create_app
    config = load_config()
    api_cfg = config.section("api")
    app = create_app(config)
    uvicorn.run(app, host=api_cfg.get("host", "127.0.0.1"),
                port=int(api_cfg.get("port", 8000)))
    return 0


def cmd_sample(args) -> int:
    from .samples import generate_sample
    out = generate_sample(args.output, seconds=args.seconds, fps=args.fps)
    print(f"Video de amostra gerado: {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="var", description="Sistema VAR modular")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("ingest", help="inicia ingestao FFmpeg").set_defaults(func=cmd_ingest)

    pt = sub.add_parser("timeline", help="lista segmentos do buffer")
    pt.add_argument("camera")
    pt.set_defaults(func=cmd_timeline)

    pa = sub.add_parser("analyze", help="roda YOLO + tracking sobre um video")
    pa.add_argument("video")
    pa.add_argument("--step", type=int, default=3, help="processa 1 a cada N frames")
    pa.add_argument("--json", help="salva resultado completo em arquivo JSON")
    pa.set_defaults(func=cmd_analyze)

    ps = sub.add_parser("sync", help="alinha um instante entre dois angulos")
    ps.add_argument("source")
    ps.add_argument("target")
    ps.add_argument("t", type=float)
    ps.set_defaults(func=cmd_sync)

    sub.add_parser("api", help="sobe a API de decisao").set_defaults(func=cmd_api)

    psa = sub.add_parser("sample", help="gera video sintetico de teste")
    psa.add_argument("--output", default="samples/cam07.mp4")
    psa.add_argument("--seconds", type=int, default=6)
    psa.add_argument("--fps", type=int, default=30)
    psa.set_defaults(func=cmd_sample)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
