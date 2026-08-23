from dataclasses import replace
import pytest
from agentcommit.domain.models import *
from agentcommit.domain.policy import evaluate_commit
from agentcommit.domain.spec import spec_allows_commit

NOW=1_800_000_000_000

def valid_snapshot():
    d=DelegationGrant("d1","b1","m1","monitor",4_000_000,"INR",1,NOW+1000)
    q=MerchantQuote("q1","m1","monitor","sku1",3_899_000,"INR",1,1,1)
    r=MerchantReservation("r1","q1","m1","monitor","sku1",3_899_000,"INR",1,1,1,NOW+500)
    g=ExecutionGrant("g1","d1",1,"b1","r1","q1","m1","monitor","sku1",3_899_000,"INR",1,1,1)
    e=ExecutionRecord("e1","b1")
    return DomainSnapshot(d,q,r,g,e,PaymentProjection())

def test_valid_allows_and_spec_agrees():
    s=valid_snapshot(); assert evaluate_commit(s,now_ms=NOW).allowed; assert spec_allows_commit(s,now_ms=NOW)

@pytest.mark.parametrize("mut", [
    lambda s: replace(s, delegation=replace(s.delegation,status=DelegationState.REVOKED)),
    lambda s: replace(s, delegation=replace(s.delegation,expires_at_ms=NOW)),
    lambda s: replace(s, grant=replace(s.grant,status=GrantState.REVOKED)),
    lambda s: replace(s, reservation=replace(s.reservation,status=ReservationState.CANCELLED)),
    lambda s: replace(s, reservation=replace(s.reservation,expires_at_ms=NOW)),
    lambda s: replace(s, execution=replace(s.execution,state=ExecutionState.CLAIMED)),
    lambda s: replace(s, payment=PaymentProjection("p1",PaymentState.AUTHORIZED)),
    lambda s: replace(s, grant=replace(s.grant,expected_buyer_id="b2")),
    lambda s: replace(s, grant=replace(s.grant,expected_delegation_version=2)),
    lambda s: replace(s, quote=replace(s.quote,merchant_id="m2")),
    lambda s: replace(s, quote=replace(s.quote,sku="sku2")),
    lambda s: replace(s, quote=replace(s.quote,currency="USD")),
    lambda s: replace(s, delegation=replace(s.delegation,max_amount_paise=3_000_000)),
    lambda s: replace(s, delegation=replace(s.delegation,max_quantity=1), quote=replace(s.quote,quantity=2), reservation=replace(s.reservation,quantity=2), grant=replace(s.grant,expected_quantity=2)),
    lambda s: replace(s, grant=replace(s.grant,expected_reservation_revision=2)),
    lambda s: replace(s, commit_count=1),
])
def test_mutations_fail_closed_and_spec_agrees(mut):
    s=mut(valid_snapshot()); assert not evaluate_commit(s,now_ms=NOW).allowed; assert not spec_allows_commit(s,now_ms=NOW)

def test_corrupted_frozen_object_fails_closed():
    s=valid_snapshot(); object.__setattr__(s.quote,"amount_paise",-1)
    assert evaluate_commit(s,now_ms=NOW).code is DecisionCode.DOMAIN_STATE_INVALID

def test_decision_consistency():
    with pytest.raises(DomainError): Decision(True,DecisionCode.MERCHANT_MISMATCH)

@pytest.mark.parametrize("bad", [0,-1,True,1.5])
def test_money_validation(bad):
    with pytest.raises(DomainError):
        DelegationGrant("d","b","m","c",bad,"INR",1,NOW+1)

@pytest.mark.parametrize("bad", ["", " space", "é", "x\n", "a"*129])
def test_identifier_validation(bad):
    with pytest.raises(DomainError):
        DelegationGrant(bad,"b","m","c",1,"INR",1,NOW+1)
