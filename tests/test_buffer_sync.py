"""Testa parsing de playlist HLS (buffer) e alinhamento de cameras (sync)."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from var.config import load_config
from var.buffer import TimelineBuffer
from var.sync import SyncManager


def _write_fake_playlist(tmp: Path, cam_id: str) -> None:
    d = tmp / cam_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.m3u8").write_text(
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n"
        "#EXTINF:2.000,\nseg_00000.ts\n"
        "#EXTINF:2.000,\nseg_00001.ts\n"
        "#EXTINF:1.500,\nseg_00002.ts\n",
        encoding="utf-8",
    )
    for n in range(3):
        (d / f"seg_{n:05d}.ts").write_bytes(b"\x00")


def test_timeline_parsing(tmp_path):
    cfg = load_config()
    cfg.raw["ingestion"]["output_dir"] = str(tmp_path)
    _write_fake_playlist(tmp_path, "cam-07")

    buf = TimelineBuffer("cam-07", cfg)
    segs = buf.segments()
    assert len(segs) == 3
    assert abs(buf.duration() - 5.5) < 1e-6
    seg = buf.segment_at(3.0)
    assert seg is not None and seg.index == 1  # 3.0s cai no 2o segmento [2,4)
    assert len(buf.window(2.5, before=1, after=1)) >= 1


def test_sync_alignment():
    cfg = load_config()
    epoch = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)
    mgr = SyncManager(cfg, master_epoch=epoch)
    src = cfg.cameras[0].id
    tgt = cfg.cameras[-1].id
    # Sem offset relativo, o mesmo instante mapeia para o mesmo t.
    assert abs(mgr.align(src, 12.34, tgt) - 12.34) < 1e-6
    assert mgr.absolute_at(src, 0).isoformat().startswith("2026-07-19T12:00:00")


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        test_timeline_parsing(Path(d))
    test_sync_alignment()
    print("OK: testes de buffer e sync passaram")
