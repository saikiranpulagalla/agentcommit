from dataclasses import replace
import pytest
from agentcommit.domain.models import *
from agentcommit.domain.policy import evaluate_commit
from agentcommit.domain.spec import spec_allows_commit
from test_policy import valid_snapshot, NOW

@pytest.mark.parametrize('currency',['inr','IN','INRR','IÑR',123])
def test_bad_currency(currency):
    with pytest.raises(DomainError):
        DelegationGrant('d','b','m','c',1,currency,1,NOW+1)

@pytest.mark.parametrize('cls,obj,field,bad',[
    (DelegationGrant, DelegationGrant('d','b','m','c',1,'INR',1,NOW+1), 'status', 'ACTIVE'),
    (MerchantReservation, MerchantReservation('r','q','m','c','s',1,'INR',1,1,1,NOW+1), 'status', 'ACTIVE'),
    (ExecutionGrant, ExecutionGrant('g','d',1,'b','r','q','m','c','s',1,'INR',1,1,1), 'status', 'ACTIVE'),
    (ExecutionRecord, ExecutionRecord('e','b'), 'state', 'PLANNED'),
    (PaymentProjection, PaymentProjection(), 'state', 'UNKNOWN'),
])
def test_corrupted_enum_rejected(cls,obj,field,bad):
    object.__setattr__(obj,field,bad)
    with pytest.raises(DomainError): obj.__post_init__()


def test_payment_unknown_with_id_rejected():
    with pytest.raises(DomainError): PaymentProjection('p',PaymentState.UNKNOWN)


def test_payment_nonunknown_without_id_rejected():
    with pytest.raises(DomainError): PaymentProjection(None,PaymentState.AUTHORIZED)


def test_bump_no_version_and_exhaustion():
    class X: pass
    with pytest.raises(DomainError): bump(X())
    d=DelegationGrant('d','b','m','c',1,'INR',1,NOW+1,version=INT64_MAX)
    with pytest.raises(DomainError): bump(d)


def test_bump_happy_path():
    d=DelegationGrant('d','b','m','c',1,'INR',1,NOW+1)
    d2=bump(d,status=DelegationState.REVOKED)
    assert d2.version==2 and d2.status is DelegationState.REVOKED and d.version==1

@pytest.mark.parametrize('bad_now',[0,-1,True,1.2,INT64_MAX+1])
def test_policy_and_spec_bad_clock(bad_now):
    s=valid_snapshot()
    assert evaluate_commit(s,now_ms=bad_now).code is DecisionCode.DOMAIN_STATE_INVALID
    assert spec_allows_commit(s,now_ms=bad_now) is False


def test_policy_category_mismatch_branch():
    s=valid_snapshot(); s=replace(s,quote=replace(s.quote,category='other'))
    assert evaluate_commit(s,now_ms=NOW).code is DecisionCode.RESOURCE_MISMATCH


def test_policy_counter_exhausted_branch():
    s=valid_snapshot(); s=replace(s,grant=replace(s.grant,version=INT64_MAX))
    assert evaluate_commit(s,now_ms=NOW).code is DecisionCode.COUNTER_EXHAUSTED


def test_spec_corrupted_domain_returns_false():
    s=valid_snapshot(); object.__setattr__(s.grant,'expected_amount_paise',-1)
    assert spec_allows_commit(s,now_ms=NOW) is False
