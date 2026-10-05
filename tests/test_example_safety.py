"""Examples must preserve results and record the resource actually selected."""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def load_example(relative):
    spec = importlib.util.spec_from_file_location("example_under_test", ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("generate_only", [False, True])
def test_evaluator_preserves_existing_results_without_explicit_overwrite(tmp_path, monkeypatch, generate_only):
    evaluate = load_example("examples/local-llm/evaluate.py")
    directory = tmp_path / "normal-1"
    directory.mkdir()
    (directory / "diagnosis.json").write_text('{"prior":"result"}')
    (directory / "observations.json").write_text('{"prior":"input"}')
    (tmp_path / "summary.json").write_text('[{"prior":"summary"}]')
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.setattr(evaluate, "diagnose", lambda *a, **k: pytest.fail("must not call model before overwrite consent"))
    monkeypatch.setattr(sys, "argv", ["evaluate", "--output", str(tmp_path), "--case", "normal",
                                    *(["--generate-only"] if generate_only else [])])
    with pytest.raises(SystemExit) as failure:
        evaluate.main()
    assert failure.value.code == 2
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("device,visible,expected", [("cuda:1", "3,5", "5"), ("cuda:1", "GPU-A,GPU-B", "GPU-B"), ("cuda", "3,5", "3")])
def test_gpu_smoke_correlates_the_explicit_selected_device(tmp_path, monkeypatch, device, visible, expected):
    smoke = load_example("examples/verl/gpu_smoke.py")
    fake = types.ModuleType("torch")
    fake.device = lambda name: types.SimpleNamespace(type=name.split(":")[0], index=int(name.split(":")[1]) if ":" in name else None)
    fake.cuda = types.SimpleNamespace(is_available=lambda: True, current_device=lambda: 0)
    monkeypatch.setitem(sys.modules, "torch", fake)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", visible)
    monkeypatch.setattr(sys, "argv", ["gpu_smoke", "--output", str(tmp_path), "--device", device])
    contexts = []
    class CapturedContext(Exception):
        pass
    def capture(_path, context):
        contexts.append(context)
        raise CapturedContext
    monkeypatch.setattr(smoke, "EventRecorder", capture)
    with pytest.raises(CapturedContext):
        smoke.main()
    assert contexts[0].gpu == expected
    assert contexts[0].local_rank == 0  # The one-process example's rank is not the device index.
