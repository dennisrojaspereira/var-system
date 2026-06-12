"""Testa o tracker isoladamente (sem YOLO): trajetoria em V deve gerar 1 contato."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from var.vision.tracker import BallTracker


def test_v_trajectory_detects_one_contact():
    tracker = BallTracker(direction_change_threshold_deg=35.0, min_speed_px_s=50.0)
    fps = 30.0
    # Sobe-direita por 15 frames, depois desce-esquerda por 15 (forma um "V").
    for i in range(15):
        tracker.add(i, i / fps, 320 + 20 * i, 380 - 8 * i, 0.9)
    for j in range(1, 16):
        i = 15 + j
        tracker.add(i, i / fps, 620 - 18 * j, 260 + 20 * j, 0.9)

    traj = tracker.build()
    assert len(traj.points) == 30
    assert len(traj.contacts) >= 1, "esperava ao menos 1 mudanca de direcao"
    assert traj.summary()["max_speed_px_s"] > 0


def test_straight_line_has_no_contact():
    tracker = BallTracker()
    for i in range(20):
        tracker.add(i, i / 30.0, 100 + 10 * i, 200, 0.9)
    traj = tracker.build()
    assert len(traj.contacts) == 0


if __name__ == "__main__":
    test_v_trajectory_detects_one_contact()
    test_straight_line_has_no_contact()
    print("OK: testes do tracker passaram")
