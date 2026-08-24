from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def load_runner():
    p=Path(__file__).parents[1]/'evals'/'run_v42_live.py'
    spec=importlib.util.spec_from_file_location('run_v42_live_test',p)
    mod=importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_live_eval_not_run_without_key_and_dataset_is_unchanged(monkeypatch,tmp_path):
    mod=load_runner()
    before=mod.DATA.read_bytes()
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.setattr(mod,'OUT',tmp_path/'result.json')
    assert mod.main()==0
    report=json.loads((tmp_path/'result.json').read_text())
    assert report['status']=='NOT_RUN' and report['real_llm_accuracy'] is None
    assert 'key' not in json.dumps(report).lower()
    assert mod.DATA.read_bytes()==before


def test_live_eval_refuses_changed_holdout_hash(monkeypatch,tmp_path):
    mod=load_runner()
    fake=tmp_path/'heldout.jsonl'
    fake.write_text('{}\n')
    monkeypatch.setattr(mod,'DATA',fake)
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    try:
        mod.main()
    except RuntimeError as exc:
        assert 'dataset hash changed' in str(exc)
    else:
        raise AssertionError('expected frozen dataset hash refusal')


def test_planner_live_cases_are_adversarial_and_have_one_valid_selection():
    mod=load_runner()
    cases=mod._planner_cases()
    assert len(cases)==20
    for case in cases:
        assert len(case.catalog)==2
        assert len(case.allowed_selected_skus)==1
        assert 'ignore' in case.catalog[0].untrusted_text.lower() or 'disregard' in case.catalog[0].untrusted_text.lower()
