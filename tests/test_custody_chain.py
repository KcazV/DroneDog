"""C6 custody log: hash chain, tamper detection pinpointing the first bad line."""
from __future__ import annotations

import json

import pytest

from dronedog.case.custody import CustodyLog, canonical, entry_hash, verify_chain


def _log(tmp_path, n=6):
    p = tmp_path / "custody.log"
    log = CustodyLog(p)
    for i in range(n):
        log.append("artifact.extract", component="ingest.test",
                   inputs=[{"path": f"in{i}", "sha256": "a" * 64}],
                   outputs=[{"path": f"out{i}", "sha256": "b" * 64}], params={"i": i})
    return p, log


def _lines(p):
    return p.read_bytes().split(b"\n")[:-1]


def _write(p, lines):
    p.write_bytes(b"\n".join(lines) + b"\n")


def test_chain_ok_and_fields(tmp_path):
    p, log = _log(tmp_path)
    rep = verify_chain(p)
    assert rep.ok and rep.entries == 6 and rep.head_seq == 6
    e = json.loads(_lines(p)[0])
    for k in ("seq", "ts_utc", "actor", "action", "inputs", "outputs", "tool", "tool_version", "component",
              "params", "prev_hash", "hash"):
        assert k in e
    assert e["tool"] == "dronedog"
    assert e["prev_hash"] == "0" * 64 and e["hash"] == entry_hash(e)


def test_reopen_continues_chain(tmp_path):
    p, _ = _log(tmp_path, 3)
    log2 = CustodyLog(p)
    log2.append("note", component="test", params={"x": 1})
    rep = verify_chain(p)
    assert rep.ok and rep.entries == 4


def test_edit_value_detected_on_that_line(tmp_path):
    p, _ = _log(tmp_path)
    lines = _lines(p)
    obj = json.loads(lines[2])
    obj["params"]["i"] = 999
    lines[2] = canonical(obj).encode()
    _write(p, lines)
    rep = verify_chain(p)
    assert not rep.ok and rep.first_bad_line == 3 and rep.first_bad_seq == 3
    assert "hash mismatch" in rep.reason


def test_edit_with_recomputed_hash_caught_by_next_line(tmp_path):
    p, _ = _log(tmp_path)
    lines = _lines(p)
    obj = json.loads(lines[2])
    obj["outputs"][0]["sha256"] = "c" * 64
    obj["hash"] = entry_hash(obj)       # attacker recomputes this line's own hash
    lines[2] = canonical(obj).encode()
    _write(p, lines)
    rep = verify_chain(p)
    assert not rep.ok and rep.first_bad_line == 4
    assert "prev_hash" in rep.reason


def test_byte_flip_and_whitespace_detected(tmp_path):
    p, _ = _log(tmp_path)
    lines = _lines(p)
    lines[1] = lines[1].replace(b'"seq":2,', b'"seq": 2,')   # same JSON value, non-canonical bytes
    _write(p, lines)
    rep = verify_chain(p)
    assert not rep.ok and rep.first_bad_line == 2


def test_deleted_and_reordered_lines(tmp_path):
    p, _ = _log(tmp_path)
    lines = _lines(p)
    _write(p, lines[:3] + lines[4:])
    rep = verify_chain(p)
    assert not rep.ok and rep.first_bad_line == 4 and "seq" in rep.reason
    (tmp_path / "b").mkdir()
    p2, _ = _log(tmp_path / "b")
    lines = _lines(p2)
    lines[1], lines[2] = lines[2], lines[1]
    _write(p2, lines)
    assert verify_chain(p2).first_bad_line == 2


def test_tail_truncation_needs_head(tmp_path):
    p, log = _log(tmp_path)
    head = (log._seq, json.loads(_lines(p)[-1])["hash"])
    _write(p, _lines(p)[:-2])
    assert verify_chain(p).ok              # chain alone cannot see a clean truncation...
    rep = verify_chain(p, expected_head=head)
    assert not rep.ok and "truncated" in rep.reason and rep.first_bad_line == 5


def test_unknown_action_rejected(tmp_path):
    with pytest.raises(ValueError):
        CustodyLog(tmp_path / "c.log").append("evil.action", component="x")


def test_case_custody_verify_cli(tmp_path, fake_core):
    from dronedog.case import Case
    from dronedog.cli import main
    from test_ingest_fatbuilder import fake_dat
    case_dir = tmp_path / "case"
    Case.create(case_dir, name="T", examiner="E").close()
    f = tmp_path / "FLY001.DAT"
    f.write_bytes(fake_dat(1))
    assert main(["case", "add", str(case_dir), str(f), "--run", "--json"]) == 0
    assert main(["custody", "verify", str(case_dir)]) == 0
    c = Case.open(case_dir)
    assert c.verify_custody()["ok"]
    actions = [e["action"] for e in c.custody.entries()]
    for a in ("case.create", "job.start", "evidence.add", "evidence.verify", "artifact.extract",
              "artifact.detect", "artifact.parse", "job.end"):
        assert a in actions, a
    c.close()
    # tamper with a line in the middle
    log = case_dir / "custody.log"
    lines = _lines(log)
    lines[3] = lines[3].replace(b'"actor":"', b'"actor":"x', 1)
    _write(log, lines)
    assert main(["custody", "verify", str(case_dir)]) == 2
    c = Case.open(case_dir)
    r = c.verify_custody()
    assert not r["ok"] and r["first_bad_line"] == 4
    c.close()


from test_ingest_fatbuilder import fake_core  # noqa: E402,F401  (fixture)
