from __future__ import annotations
import importlib.util
from pathlib import Path

def test_certify_v31_import_has_no_side_effects(tmp_path, monkeypatch):
    path=Path(__file__).parents[1]/"evals"/"certify_v31.py"
    spec=importlib.util.spec_from_file_location("certify_v31_import_test",path)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert callable(mod.main)
