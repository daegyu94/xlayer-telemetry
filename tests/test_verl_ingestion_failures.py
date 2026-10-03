"""Logger restart and heterogeneous records must not lose subsequent steps."""

import json

import pytest

from xlayer_telemetry.adapters import verl


@pytest.mark.parametrize("buffered_record", [False, True])
def test_copytruncate_regrow_recovers_first_record_and_resets_provenance(tmp_path, monkeypatch, buffered_record):
    path = tmp_path / "logger.jsonl"
    original = {"step": 1, "data": {"timing_s/step": 1}}
    path.write_text(json.dumps(original) + "\n" +
                    (json.dumps({"step": 99, "data": {}}) + "\n" if buffered_record else ""))
    inode = path.stat().st_ino
    follower = verl.iter_file_records(path, follow=True, poll_interval=.01)
    # Fail deterministically instead of hanging if the follower loses a record.
    monkeypatch.setattr(verl.time, "sleep", lambda _: pytest.fail("lost a complete replacement record"))
    try:
        assert next(follower)["step"] == 1
        replacement = {"step": 2, "data": {"padding": "x" * 512}}
        path.write_text(json.dumps(replacement) + "\n" + json.dumps({"step": 3, "data": {}}) + "\n")
        assert path.stat().st_ino == inode
        records = [next(follower), next(follower)]
        assert [record["step"] for record in records] == [2, 3]
        assert all(record.live is False for record in records)
        with path.open("a") as stream:
            stream.write(json.dumps({"step": 4, "data": {}}) + "\n")
        live = next(follower)
        assert live["step"] == 4 and live.live is True
    finally:
        follower.close()
