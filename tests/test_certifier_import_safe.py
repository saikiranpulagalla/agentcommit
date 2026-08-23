import runpy
from pathlib import Path


def test_v2_certifier_is_import_safe(tmp_path, monkeypatch):
    script=Path(__file__).parents[1]/'evals'/'certify_v2.py'
    ns=runpy.run_path(str(script),run_name='agentcommit_certifier_import_test')
    assert callable(ns['main'])
