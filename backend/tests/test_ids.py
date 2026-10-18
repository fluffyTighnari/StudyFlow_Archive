"""顺序 ID 分配测试（M1 §3.3 / M0 §6.1 的人类可读 ID 约定）。"""

from __future__ import annotations

import pytest

from app.core.ids import (
    format_seq,
    next_evidence_id,
    next_intent_id,
    next_m1_baseline_id,
    next_m1_obs_id,
    next_preference_id,
    next_proposal_id,
    next_router_log_id,
    parse_seq,
)
from app.db import DbSequenceStore


class FakeStore:
    """纯内存计数器，用于隔离测试 ID 格式逻辑。"""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def next(self, scope: str) -> int:
        self._counters[scope] = self._counters.get(scope, 0) + 1
        return self._counters[scope]


def test_format_seq_zero_pads() -> None:
    assert format_seq("M1-SE", 1) == "M1-SE-001"
    assert format_seq("M1-SE", 42) == "M1-SE-042"
    assert format_seq("RL", 7, 4) == "RL-0007"


def test_format_seq_rejects_non_positive() -> None:
    with pytest.raises(ValueError):
        format_seq("X", 0)


def test_parse_seq_round_trips() -> None:
    assert parse_seq("M1-SE-001") == ("M1-SE", 1)
    assert parse_seq("RL-0007") == ("RL", 7)


def test_parse_seq_rejects_malformed() -> None:
    with pytest.raises(ValueError):
        parse_seq("no-number-here")


def test_id_families_have_documented_prefixes() -> None:
    store = FakeStore()
    assert next_router_log_id(store) == "RL-0001"
    assert next_proposal_id(store) == "UP-0001"
    assert next_intent_id(store) == "UI-0001"
    assert next_evidence_id(store) == "E-0001"
    assert next_preference_id(store) == "P-0001"
    assert next_m1_obs_id(store) == "OB-0001"


def test_m1_baseline_ids_are_counted_per_variable() -> None:
    """M1 §3.3：`M1-<变量缩写>-<3位>`，每个变量各自计数，互不干扰。"""
    store = FakeStore()
    assert next_m1_baseline_id(store, "V-SE") == "M1-SE-001"
    assert next_m1_baseline_id(store, "V-SE") == "M1-SE-002"
    assert next_m1_baseline_id(store, "V-CP") == "M1-CP-001"
    assert next_m1_baseline_id(store, "V-SE") == "M1-SE-003"


def test_db_sequence_store_persists_across_calls(session) -> None:
    store = DbSequenceStore(session)
    assert store.next("demo") == 1
    assert store.next("demo") == 2
    assert store.next("other") == 1
    assert store.peek("demo") == 2


def test_db_sequence_store_drives_id_helpers(session) -> None:
    store = DbSequenceStore(session)
    assert next_router_log_id(store) == "RL-0001"
    assert next_router_log_id(store) == "RL-0002"
    assert next_evidence_id(store) == "E-0001"
