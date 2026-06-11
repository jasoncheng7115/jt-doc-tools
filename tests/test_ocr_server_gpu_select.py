"""Unit tests for jt-ocr-server's auto GPU selection (server_template.py).

server_template.py is the EasyOCR FastAPI service deployed to a remote GPU host
(embedded as a heredoc into the generated install.sh). On multi-GPU hosts it
should:
  - pick the eligible GPU with the MOST free VRAM (>= JT_OCR_MIN_FREE_MB),
  - fall back to CPU when no GPU has enough free VRAM (or there's no CUDA),
  - never pick a GPU whose mem_get_info failed (treated as 0 free),
  - cache the decision for the process lifetime.

We exercise `_pick_device` / `_gpu_free_table` by injecting a fake `torch` into
sys.modules (the module imports torch lazily inside those functions).
"""
from __future__ import annotations

import sys
import types

import pytest

import app.admin.ocr_remote_deploy.server_template as srv


def _fake_torch(devices):
    """Build a fake torch module.

    `devices` is a list of dicts: {"name": str, "free_mb": int|None, "total_mb": int}.
    free_mb=None makes mem_get_info(i) raise (simulating a saturated card)."""
    cuda = types.SimpleNamespace()
    cuda.is_available = lambda: len(devices) > 0
    cuda.device_count = lambda: len(devices)

    def get_device_name(i):
        return devices[i]["name"]

    def mem_get_info(i):
        d = devices[i]
        if d["free_mb"] is None:
            raise RuntimeError("CUDA error: out of memory")
        mb = 1024 * 1024
        return (d["free_mb"] * mb, d["total_mb"] * mb)

    cuda.get_device_name = get_device_name
    cuda.mem_get_info = mem_get_info
    cuda.set_device = lambda i: None
    mod = types.ModuleType("torch")
    mod.cuda = cuda
    mod.__version__ = "fake"
    return mod


@pytest.fixture
def reset_state(monkeypatch):
    """Reset the module's cached decision + threshold before each test."""
    monkeypatch.setattr(srv, "_chosen_device", None)
    monkeypatch.setattr(srv, "_MIN_FREE_MB", 2048)
    yield


def _install_torch(monkeypatch, devices):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(devices))


def test_picks_gpu_with_most_free_vram(reset_state, monkeypatch):
    # gpu0=1000 (below), gpu1=8000 (eligible, most), gpu2=3000 (eligible)
    _install_torch(monkeypatch, [
        {"name": "A", "free_mb": 1000, "total_mb": 8000},
        {"name": "B", "free_mb": 8000, "total_mb": 8000},
        {"name": "C", "free_mb": 3000, "total_mb": 8000},
    ])
    assert srv._pick_device() == 1
    assert srv._device_label() == "cuda:1"


def test_falls_back_to_cpu_when_none_eligible(reset_state, monkeypatch):
    # All cards below the 2048 MB threshold (busy with other workloads).
    _install_torch(monkeypatch, [
        {"name": "A", "free_mb": 500, "total_mb": 8000},
        {"name": "B", "free_mb": 1500, "total_mb": 8000},
    ])
    assert srv._pick_device() == "cpu"
    assert srv._device_label() == "cpu"


def test_no_cuda_returns_cpu(reset_state, monkeypatch):
    _install_torch(monkeypatch, [])  # is_available() -> False
    assert srv._pick_device() == "cpu"


def test_gpu_with_failed_mem_query_not_picked(reset_state, monkeypatch):
    # gpu0 mem_get_info raises -> free=0 (skip); gpu1 eligible -> picked.
    _install_torch(monkeypatch, [
        {"name": "A", "free_mb": None, "total_mb": 8000},
        {"name": "B", "free_mb": 4000, "total_mb": 8000},
    ])
    assert srv._pick_device() == 1
    table = srv._gpu_free_table()
    assert table[0]["free_mb"] == 0 and "note" in table[0]


def test_threshold_is_configurable(reset_state, monkeypatch):
    # Raise threshold so the 3000 MB card no longer qualifies.
    monkeypatch.setattr(srv, "_MIN_FREE_MB", 6000)
    _install_torch(monkeypatch, [
        {"name": "A", "free_mb": 3000, "total_mb": 8000},
        {"name": "B", "free_mb": 5000, "total_mb": 8000},
    ])
    assert srv._pick_device() == "cpu"


def test_decision_is_cached(reset_state, monkeypatch):
    _install_torch(monkeypatch, [
        {"name": "A", "free_mb": 9000, "total_mb": 12000},
    ])
    assert srv._pick_device() == 0
    # Now swap in a torch that would pick differently; cached value must persist.
    _install_torch(monkeypatch, [
        {"name": "X", "free_mb": 100, "total_mb": 12000},
    ])
    assert srv._pick_device() == 0  # unchanged — cached


def test_ties_break_deterministically(reset_state, monkeypatch):
    # Two eligible cards with equal free VRAM -> max() returns the first.
    _install_torch(monkeypatch, [
        {"name": "A", "free_mb": 4000, "total_mb": 8000},
        {"name": "B", "free_mb": 4000, "total_mb": 8000},
    ])
    assert srv._pick_device() == 0
