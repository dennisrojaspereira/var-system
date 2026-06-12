"""Timeline buffer: indexa os segmentos HLS gravados pela ingestao e oferece
navegacao/replay por instante de tempo.

A playlist .m3u8 do FFmpeg contem a duracao de cada segmento (#EXTINF). A partir
disso reconstruimos uma timeline com offset acumulado, permitindo que a sala do
VAR peca "o trecho do lance em t = 305.2s" e receba os segmentos corretos.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..config import Config, load_config

_EXTINF = re.compile(r"#EXTINF:([0-9.]+)")


@dataclass
class Segment:
    camera_id: str
    index: int
    path: Path
    start: float       # offset (s) desde o inicio da playlist atual
    duration: float

    @property
    def end(self) -> float:
        return self.start + self.duration

    def contains(self, t: float) -> bool:
        return self.start <= t < self.end


class TimelineBuffer:
    """Le a playlist HLS de uma camera e indexa seus segmentos."""

    def __init__(self, camera_id: str, config: Config | None = None):
        self.config = config or load_config()
        self.camera_id = camera_id
        out = self.config.resolve(self.config.section("ingestion").get("output_dir", "buffer"))
        self.dir = out / camera_id
        self.playlist = self.dir / "index.m3u8"

    def segments(self) -> list[Segment]:
        """Parseia a playlist e retorna os segmentos com offsets acumulados."""
        if not self.playlist.exists():
            return []
        segs: list[Segment] = []
        offset = 0.0
        pending_dur: float | None = None
        for line in self.playlist.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            m = _EXTINF.match(line)
            if m:
                pending_dur = float(m.group(1))
                continue
            if line and not line.startswith("#") and pending_dur is not None:
                seg_path = self.dir / line
                idx = _segment_index(line)
                segs.append(
                    Segment(
                        camera_id=self.camera_id,
                        index=idx,
                        path=seg_path,
                        start=offset,
                        duration=pending_dur,
                    )
                )
                offset += pending_dur
                pending_dur = None
        return segs

    def duration(self) -> float:
        segs = self.segments()
        return segs[-1].end if segs else 0.0

    def segment_at(self, t: float) -> Segment | None:
        """Segmento que contem o instante t (segundos desde o inicio da janela)."""
        for seg in self.segments():
            if seg.contains(t):
                return seg
        return None

    def window(self, center: float, before: float = 4.0, after: float = 4.0) -> list[Segment]:
        """Segmentos cobrindo [center-before, center+after] para replay do lance."""
        lo, hi = max(0.0, center - before), center + after
        return [s for s in self.segments() if s.end > lo and s.start < hi]


def open_buffers(config: Config | None = None) -> dict[str, TimelineBuffer]:
    """Um TimelineBuffer por camera configurada, indexado por camera_id."""
    config = config or load_config()
    return {cam.id: TimelineBuffer(cam.id, config) for cam in config.cameras}


def _segment_index(filename: str) -> int:
    m = re.search(r"(\d+)", Path(filename).stem)
    return int(m.group(1)) if m else -1
