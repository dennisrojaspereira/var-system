"""Sincronizacao de cameras.

Num ambiente real, todas as cameras compartilham um master clock (PTP / IEEE
1588, GPS Time Source, SMPTE ST 2059) para que o instante 12:35:21.123 seja o
mesmo em todos os angulos. Aqui modelamos esse contrato: cada camera tem um
offset de calibracao (ms) em relacao ao relogio mestre, e o SyncManager converte
entre tempo-de-camera (segundos no buffer) e tempo absoluto UTC.

Isso garante que, ao trocar de angulo durante uma revisao, todas as cameras
mostrem o MESMO instante do lance.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config import Config, load_config


@dataclass
class CameraClock:
    camera_id: str
    epoch: datetime          # tempo absoluto correspondente a t=0 no buffer
    offset_ms: float = 0.0   # calibracao PTP por camera

    def to_absolute(self, t_seconds: float) -> datetime:
        """Tempo-de-camera (s no buffer) -> tempo absoluto UTC sincronizado."""
        return self.epoch + timedelta(seconds=t_seconds, milliseconds=self.offset_ms)

    def to_camera_time(self, absolute: datetime) -> float:
        """Tempo absoluto UTC -> tempo-de-camera (s no buffer)."""
        delta = absolute - (self.epoch + timedelta(milliseconds=self.offset_ms))
        return delta.total_seconds()


class SyncManager:
    """Mantem um CameraClock por camera e mapeia instantes entre angulos."""

    def __init__(self, config: Config | None = None, master_epoch: datetime | None = None):
        self.config = config or load_config()
        self.sync_cfg = self.config.section("sync")
        # Epoch mestre comum: simula o master clock distribuido por PTP.
        self.master_epoch = master_epoch or datetime.now(timezone.utc)
        base_offset = float(self.sync_cfg.get("ptp_offset_ms", 0))
        self.clocks: dict[str, CameraClock] = {
            cam.id: CameraClock(cam.id, self.master_epoch, base_offset)
            for cam in self.config.cameras
        }

    def clock(self, camera_id: str) -> CameraClock:
        return self.clocks[camera_id]

    def align(self, source_cam: str, t_seconds: float, target_cam: str) -> float:
        """Dado o instante t na camera origem, retorna o instante equivalente
        (s no buffer) na camera destino - o nucleo da troca de angulo no VAR."""
        absolute = self.clocks[source_cam].to_absolute(t_seconds)
        return self.clocks[target_cam].to_camera_time(absolute)

    def absolute_at(self, camera_id: str, t_seconds: float) -> datetime:
        return self.clocks[camera_id].to_absolute(t_seconds)
