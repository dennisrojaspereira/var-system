"""Carregamento e acesso tipado da configuracao (config.yaml)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Raiz do projeto = pasta que contem este pacote 'var'.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@dataclass
class CameraConfig:
    id: str
    angle: str
    source: str
    fps: int = 50


@dataclass
class Config:
    raw: dict[str, Any]
    path: Path

    @property
    def match(self) -> dict[str, Any]:
        return self.raw.get("match", {})

    @property
    def cameras(self) -> list[CameraConfig]:
        return [
            CameraConfig(
                id=c["id"],
                angle=c.get("angle", "unknown"),
                source=c["source"],
                fps=int(c.get("fps", 50)),
            )
            for c in self.raw.get("cameras", [])
        ]

    def camera(self, camera_id: str) -> CameraConfig:
        for cam in self.cameras:
            if cam.id == camera_id:
                return cam
        raise KeyError(f"Camera '{camera_id}' nao encontrada na configuracao")

    def section(self, name: str) -> dict[str, Any]:
        return self.raw.get(name, {})

    def resolve(self, relative: str) -> Path:
        """Resolve um caminho relativo a raiz do projeto."""
        p = Path(relative)
        return p if p.is_absolute() else (PROJECT_ROOT / p)


def load_config(path: str | Path | None = None) -> Config:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(f"config.yaml nao encontrado em {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return Config(raw=raw, path=cfg_path)
