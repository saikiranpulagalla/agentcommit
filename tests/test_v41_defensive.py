from __future__ import annotations

import json
from pathlib import Path
import pytest

from agentcommit.ai import (
    CandidatePlanner, CatalogCandidate, CompilerConfig, ConstraintOp, HardConstraint,
    IntentSpec, IntentStatus, ModelFailure, PlanResult, PlanningGoldCase, ProductFacts,
    ScriptedJsonModel, StructuredIntentCompiler, evaluate_candidate_planner,
    evaluate_intent_compiler, extract_critical_expectation, load_intent_gold_jsonl,
)
from agentcommit.ai.eval import PlanningEvalMetrics
from agentcommit.ai.model import bounded_json
from agentcommit.domain.models import DomainError


def pf(sku='x', **attrs):
    return ProductFacts('m',sku,'monitor','INR',3_000_000,1,1,attrs)


def ready_model_response():
    return {
        'status':'READY','hard_constraints':[],'soft_preferences':[],
        'substitution_allowed':False,'unresolved_fields':[],
    }


@pytest.mark.parametrize('value',[-1,4,True])
def test_compiler_config_rejects_invalid_repair_budget(value):
    with pytest.raises(DomainError, match='max_repairs'):
        CompilerConfig(max_repairs=value)


def test_compiler_config_rejects_empty_constraint_whitelist():
    with pytest.raises(DomainError, match='whitelist'):
        CompilerConfig(allowed_constraint_fields=frozenset())


@pytest.mark.parametrize('bad',[
    [],
    {'status':'READY','hard_constraints':[],'soft_preferences':[],'substitution_allowed':1,'unresolved_fields':[]},
    {'status':'READY','hard_constraints':{},'soft_preferences':[],'substitution_allowed':False,'unresolved_fields':[]},
    {'status':'READY','hard_constraints':[],'soft_preferences':{},'substitution_allowed':False,'unresolved_fields':[]},
    {'status':'READY','hard_constraints':[],'soft_preferences':[],'substitution_allowed':False,'unresolved_fields':'x'},
    {'status':'READY','hard_constraints':[{'field':'usb_c','op':'EQ'}],'soft_preferences':[],'substitution_allowed':False,'unresolved_fields':[]},
    {'status':'READY','hard_constraints':[{'field':'brand','op':'IN','value':'Dell'}],'soft_preferences':[],'substitution_allowed':False,'unresolved_fields':[]},
    {'status':'READY','hard_constraints':[],'soft_preferences':[{'field':'price_paise'}],'substitution_allowed':False,'unresolved_fields':[]},
    {'status':'READY','hard_constraints':[],'soft_preferences':[{'field':'unknown','direction':'MINIMIZE'}],'substitution_allowed':False,'unresolved_fields':[]},
    {'status':'NEEDS_CLARIFICATION','hard_constraints':[],'soft_preferences':[],'substitution_allowed':False,'unresolved_fields':['budget','budget']},
])
def test_structured_compiler_rejects_malformed_shapes(bad):
    model=ScriptedJsonModel([bad])
    with pytest.raises(DomainError):
        StructuredIntentCompiler(model,config=CompilerConfig(max_repairs=0)).compile(
            intent_id='i',buyer_id='b',raw_request='monitor'
        )


def test_structured_compiler_rejects_unsupported_hard_field():
    bad=ready_model_response(); bad['hard_constraints']=[{'field':'evil','op':'EQ','value':True}]
    with pytest.raises(DomainError, match='unsupported hard'):
        StructuredIntentCompiler(ScriptedJsonModel([bad]),config=CompilerConfig(max_repairs=0)).compile(
            intent_id='i',buyer_id='b',raw_request='x'
        )


def test_scripted_model_exhaustion_and_bounded_json_fail_closed():
    with pytest.raises(ModelFailure, match='exhausted'):
        ScriptedJsonModel([]).complete_json(system='s',user='u')
    with pytest.raises(DomainError, match='serializable'):
        bounded_json({'x':object()})
    with pytest.raises(DomainError, match='size'):
        bounded_json({'x':'a'*100},max_chars=10)


@pytest.mark.parametrize('text', [None, 42])
def test_critical_extractor_requires_string(text):
    with pytest.raises(DomainError, match='string'):
        extract_critical_expectation(text)  # type: ignore[arg-type]


def test_critical_budget_fraction_and_zero_fail_closed():
    with pytest.raises(DomainError, match='exactly'):
        extract_critical_expectation('monitor under 0.333 rupees')
    with pytest.raises(DomainError, match='out of range'):
        extract_critical_expectation('monitor under 0')


def test_critical_explicit_quantity_zero_fails_closed():
    with pytest.raises(DomainError, match='quantity'):
        extract_critical_expectation('Buy 0 monitors')


def test_planner_validation_and_provider_failures():
    with pytest.raises(DomainError, match='max_model_calls'):
        CandidatePlanner(ScriptedJsonModel([]),max_model_calls=0)
    with pytest.raises(DomainError, match='catalog text'):
        CatalogCandidate(pf(), 'x'*2001)
    intent=IntentSpec('i','b','x',(HardConstraint('usb_c',ConstraintOp.EQ,True),))
    with pytest.raises(ModelFailure):
        CandidatePlanner(ScriptedJsonModel([ModelFailure('down')])).plan(
            intent=intent,catalog=[CatalogCandidate(pf(usb_c=True))]
        )


@pytest.mark.parametrize('raw',[
    {'ranked_skus':'x','reason':'r'},
    {'ranked_skus':['x'],'reason':3},
    {'ranked_skus':[],'reason':'r'},
])
def test_planner_malformed_output_exhausts_bounded_repair(raw):
    intent=IntentSpec('i','b','x',(HardConstraint('usb_c',ConstraintOp.EQ,True),))
    model=ScriptedJsonModel([raw])
    with pytest.raises(DomainError, match='bounded repair'):
        CandidatePlanner(model,max_model_calls=1).plan(intent=intent,catalog=[CatalogCandidate(pf(usb_c=True))])


def test_planner_no_valid_candidate_after_all_calls():
    intent=IntentSpec('i','b','x',(HardConstraint('usb_c',ConstraintOp.EQ,True),))
    model=ScriptedJsonModel([
        {'ranked_skus':['bad'],'reason':'r1'},
        {'ranked_skus':['bad'],'reason':'r2'},
    ])
    result=CandidatePlanner(model,max_model_calls=2).plan(
        intent=intent,catalog=[CatalogCandidate(pf('bad',usb_c=False))]
    )
    assert result.outcome=='NO_VALID_CANDIDATE' and result.selected is None


def test_planner_prompt_size_limit_fails_closed():
    intent=IntentSpec('i','b','x',(HardConstraint('usb_c',ConstraintOp.EQ,True),))
    # 40 * ~2k untrusted descriptions exceeds the planner's 64k payload ceiling.
    catalog=[CatalogCandidate(pf(f's{i}',usb_c=True),'z'*1900) for i in range(40)]
    with pytest.raises(DomainError, match='size'):
        CandidatePlanner(ScriptedJsonModel([])).plan(intent=intent,catalog=catalog)


def test_eval_rejects_empty_inputs_and_corrupt_gold(tmp_path: Path):
    with pytest.raises(ValueError,match='empty'):
        evaluate_intent_compiler(compiler=StructuredIntentCompiler(ScriptedJsonModel([])),cases=())
    class DummyPlanner:
        def plan(self, **kwargs):
            raise AssertionError
    with pytest.raises(ValueError,match='empty'):
        evaluate_candidate_planner(planner=DummyPlanner(),cases=())  # type: ignore[arg-type]

    p=tmp_path/'bad.jsonl'; p.write_text('{bad\n',encoding='utf-8')
    with pytest.raises(DomainError,match='invalid gold'):
        load_intent_gold_jsonl(p)
    p.write_text(json.dumps({'x':1})+'\n',encoding='utf-8')
    with pytest.raises(DomainError,match='schema'):
        load_intent_gold_jsonl(p)
    p.write_text('',encoding='utf-8')
    with pytest.raises(DomainError,match='empty'):
        load_intent_gold_jsonl(p)


def test_planning_eval_detects_unsafe_or_wrong_outcome_with_fake_planner():
    intent=IntentSpec('i','b','USB-C',(HardConstraint('usb_c',ConstraintOp.EQ,True),))
    bad=pf('bad',usb_c=False)
    case=PlanningGoldCase('c',intent,(CatalogCandidate(bad),),frozenset({'good'}),expected_outcome='SELECTED')
    class FakePlanner:
        def plan(self, **kwargs):
            return PlanResult(bad,('bad',),(),1,'WRONG')
    m=evaluate_candidate_planner(planner=FakePlanner(),cases=(case,))  # type: ignore[arg-type]
    assert m.outcome_accuracy==0.0 and m.expected_selection_accuracy==0.0 and m.unsafe_selection_rate==1.0
