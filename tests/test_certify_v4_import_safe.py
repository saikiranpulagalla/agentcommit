from __future__ import annotations
import importlib.util
from pathlib import Path

def test_certify_v4_is_import_safe(tmp_path, monkeypatch):
    root=Path(__file__).parents[1]
    result=root/'evals'/'results'/'v4.0'
    before=set(result.rglob('*')) if result.exists() else set()
    spec=importlib.util.spec_from_file_location('certify_v4_import_probe',root/'evals'/'certify_v4.py')
    mod=importlib.util.module_from_spec(spec); assert spec.loader is not None; spec.loader.exec_module(mod)
    after=set(result.rglob('*')) if result.exists() else set()
    assert before==after

def test_certify_v4_aggregator_uses_v4_metrics(tmp_path, monkeypatch):
    root=Path(__file__).parents[1]
    spec=importlib.util.spec_from_file_location('certify_v4_aggregate_probe',root/'evals'/'certify_v4.py')
    mod=importlib.util.module_from_spec(spec); assert spec.loader is not None; spec.loader.exec_module(mod)
    prov={'git_sha':'abc','tracked_hash':'hash','clean':True,'python':'x','platform':'x'}
    monkeypatch.setattr(mod,'RESULTS',tmp_path)
    monkeypatch.setattr(mod,'provenance',lambda:prov)
    monkeypatch.setattr(mod,'git_clean',lambda:True)
    data={
      'coverage':{'tests_passed':1,'line_pct':99.0,'branch_pct':99.0},
      'differential':{'cases':100,'mismatches':0},
      'races':{'returncode':0,'tests_passed':1},
      'performance':{'v4_commit_p95_ms':1.0,'dispatch_p95_ms':2.0},
      'security':{'forbidden_constructs':[],'secret_findings':[]},
      'testmode':{'status':'NOT_RUN'},
    }
    for i in range(1,6): data[f'stability-{i}']={'passed':True,'test_count':1}
    monkeypatch.setattr(mod,'load_stage',lambda name:{'provenance':prov,'data':data[name]})
    assert mod.aggregate()==0
    metrics=__import__('json').loads((tmp_path/'metrics.json').read_text())
    assert metrics['version']=='v4.0' and metrics['v4_safety_promotion'] is True
    assert 'full_v4_certified' in metrics
