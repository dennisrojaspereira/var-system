"""Ingestao de cameras via FFmpeg -> timeline HLS (janela deslizante).

Cada camera roda um processo FFmpeg dedicado que transcodifica o stream de
entrada (RTSP/SRT/UDP/arquivo/webcam) para HLS, gerando uma playlist .m3u8 e
segmentos .ts curtos. Esses segmentos formam o buffer de timeline navegavel
que a sala do VAR consome para replay.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

from ..config import CameraConfig, Config, load_config


def ffmpeg_available() -> bool:
    """True se o binario ffmpeg estiver no PATH."""
    return shutil.which("ffmpeg") is not None


def _source_input_args(source: str) -> list[str]:
    """Argumentos de entrada do FFmpeg conforme o tipo de fonte."""
    if source.startswith(("rtsp://",)):
        # rtsp via TCP costuma ser mais estavel que UDP.
        return ["-rtsp_transport", "tcp", "-i", source]
    if source.isdigit():
        # Webcam. dshow no Windows, v4l2 no Linux (ajuste conforme SO).
        return ["-f", "dshow", "-i", f"video={source}"]
    # Arquivo / srt / udp / http -> entrada direta.
    return ["-i", source]


class FFmpegIngestor:
    """Gerencia um processo FFmpeg de ingestao para uma camera."""

    def __init__(self, camera: CameraConfig, config: Config):
        self.camera = camera
        self.config = config
        self.ing = config.section("ingestion")
        self.output_dir = config.resolve(self.ing.get("output_dir", "buffer")) / camera.id
        self.playlist = self.output_dir / "index.m3u8"
        self._process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None

    def build_command(self) -> list[str]:
        ing = self.ing
        hls_flags = []
        if ing.get("delete_segments", True):
            hls_flags.append("delete_segments")
        flags = "+".join(hls_flags) if hls_flags else "independent_segments"

        cmd: list[str] = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
        cmd += _source_input_args(self.camera.source)
        cmd += [
            "-c:v", ing.get("video_codec", "libx264"),
            "-preset", ing.get("preset", "veryfast"),
            "-tune", "zerolatency",
            "-g", str(self.camera.fps * ing.get("hls_time", 2)),  # keyframe por segmento
            "-f", "hls",
            "-hls_time", str(ing.get("hls_time", 2)),
            "-hls_list_size", str(ing.get("hls_list_size", 60)),
            "-hls_flags", flags,
            "-hls_segment_filename", str(self.output_dir / "seg_%05d.ts"),
            str(self.playlist),
        ]
        return cmd

    def start(self, on_log: Callable[[str], None] | None = None) -> None:
        if not ffmpeg_available():
            raise RuntimeError(
                "ffmpeg nao encontrado no PATH. Instale o FFmpeg "
                "(https://ffmpeg.org/download.html) e tente novamente."
            )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        cmd = self.build_command()
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        def _pump() -> None:
            assert self._process and self._process.stdout
            for line in self._process.stdout:
                line = line.rstrip()
                if not line:
                    continue
                if on_log:
                    on_log(f"[{self.camera.id}] {line}")
                else:
                    print(f"[{self.camera.id}] {line}")

        self._reader = threading.Thread(target=_pump, daemon=True)
        self._reader.start()

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self, timeout: float = 5.0) -> None:
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None


def ingest_all(config: Config | None = None) -> list[FFmpegIngestor]:
    """Inicia a ingestao de todas as cameras configuradas. Retorna os ingestors."""
    config = config or load_config()
    ingestors: list[FFmpegIngestor] = []
    for cam in config.cameras:
        ing = FFmpegIngestor(cam, config)
        ing.start()
        ingestors.append(ing)
    return ingestors
