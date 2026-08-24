from __future__ import annotations

import json
import pytest

from agentcommit.ai import (
    OpenAIResponsesJsonModel, intent_output_schema, planner_output_schema,
)
from agentcommit.ai.model import ModelFailure
from agentcommit.domain.models import DomainError


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post_json(self, *, url, headers, body, timeout_s):
        self.calls.append((url, dict(headers), body, timeout_s))
        if isinstance(self.response, BaseException):
            raise self.response
        if isinstance(self.response, bytes):
            return self.response
        return json.dumps(self.response).encode()


def envelope(text='{"ok":true}', *, status='completed', usage=None):
    return {
        'status': status,
        'error': None,
        'output': [{
            'type': 'message',
            'content': [{'type': 'output_text', 'text': text}],
        }],
        'usage': usage or {'input_tokens': 10, 'output_tokens': 4, 'total_tokens': 14},
    }


def adapter(transport, **kw):
    return OpenAIResponsesJsonModel(
        api_key='sk-test-secret', model='gpt-5.6-luna',
        response_schema={'type':'object','additionalProperties':False,'required':['ok'],'properties':{'ok':{'type':'boolean'}}},
        response_name='agentcommit_test', transport=transport, **kw,
    )


def test_request_uses_responses_strict_json_schema_and_store_false():
    t=FakeTransport(envelope())
    m=adapter(t)
    assert m.complete_json(system='system',user='user') == {'ok':True}
    url,headers,body,timeout=t.calls[0]
    payload=json.loads(body)
    assert url=='https://api.openai.com/v1/responses'
    assert headers['Authorization']=='Bearer sk-test-secret'
    assert 'sk-test-secret' not in body.decode()
    assert payload['store'] is False
    assert payload['text']['format']['type']=='json_schema'
    assert payload['text']['format']['strict'] is True
    assert payload['text']['format']['name']=='agentcommit_test'
    assert payload['model']=='gpt-5.6-luna'
    assert payload['instructions']=='system' and payload['input']=='user'
    assert m.usage[0].total_tokens==14


def test_refusal_fails_closed():
    t=FakeTransport({'status':'completed','output':[{'type':'message','content':[{'type':'refusal','refusal':'no'}]}]})
    with pytest.raises(ModelFailure,match='refused'):
        adapter(t).complete_json(system='s',user='u')


def test_noncompleted_response_fails_closed():
    with pytest.raises(ModelFailure,match='not completed'):
        adapter(FakeTransport({'status':'incomplete','output':[]})).complete_json(system='s',user='u')


@pytest.mark.parametrize('raw,match',[
    (b'not-json','invalid JSON envelope'),
    (json.dumps([]).encode(),'envelope must be object'),
    (json.dumps({'status':'completed','output':'bad'}).encode(),'output missing'),
])
def test_malformed_envelope_fails_closed(raw,match):
    with pytest.raises(ModelFailure,match=match):
        adapter(FakeTransport(raw)).complete_json(system='s',user='u')


def test_multiple_output_text_parts_rejected():
    e=envelope()
    e['output'][0]['content'].append({'type':'output_text','text':'{"ok":false}'})
    with pytest.raises(ModelFailure,match='multiple output_text'):
        adapter(FakeTransport(e)).complete_json(system='s',user='u')


def test_invalid_structured_json_rejected():
    with pytest.raises(ModelFailure,match='not valid JSON'):
        adapter(FakeTransport(envelope('not-json'))).complete_json(system='s',user='u')


def test_provider_transport_failure_propagates_as_model_failure():
    with pytest.raises(ModelFailure,match='down'):
        adapter(FakeTransport(ModelFailure('down'))).complete_json(system='s',user='u')


def test_bad_usage_is_not_trusted():
    m=adapter(FakeTransport(envelope(usage={'input_tokens':True,'output_tokens':-1,'total_tokens':'14'})))
    m.complete_json(system='s',user='u')
    assert m.usage[0].input_tokens==0 and m.usage[0].output_tokens==0 and m.usage[0].total_tokens==0


@pytest.mark.parametrize('kwargs,match',[
    ({'api_key':''},'API key'),
    ({'model':''},'model'),
    ({'response_name':'bad name'},'response_name'),
    ({'base_url':'http://example.com'},'https'),
    ({'timeout_s':0},'timeout_s'),
    ({'max_output_tokens':10},'max_output_tokens'),
])
def test_adapter_config_validation(kwargs,match):
    base=dict(api_key='x',model='m',response_schema={'type':'object'},response_name='x',transport=FakeTransport(envelope()))
    base.update(kwargs)
    with pytest.raises(DomainError,match=match):
        OpenAIResponsesJsonModel(**base)


def test_schema_validation_and_builders():
    with pytest.raises(DomainError,match='root'):
        OpenAIResponsesJsonModel(api_key='x',model='m',response_schema={'type':'array'},response_name='x',transport=FakeTransport(envelope()))
    intent=intent_output_schema(constraint_fields=['price_paise','usb_c'],clarification_fields=['budget'])
    assert intent['additionalProperties'] is False
    assert set(intent['required'])=={'status','hard_constraints','soft_preferences','substitution_allowed','unresolved_fields'}
    planner=planner_output_schema(max_ranked_skus=7)
    assert planner['properties']['ranked_skus']['type']=='array'
    with pytest.raises(DomainError):
        planner_output_schema(max_ranked_skus=0)


def test_prompt_bounds_fail_before_transport():
    t=FakeTransport(envelope())
    m=adapter(t)
    with pytest.raises(DomainError,match='prompt size'):
        m.complete_json(system='',user='u')
    with pytest.raises(DomainError,match='prompt size'):
        m.complete_json(system='s',user='x'*128001)
    assert not t.calls


def test_schema_and_prompt_additional_fail_closed_paths():
    t=FakeTransport(envelope())
    with pytest.raises(DomainError,match='response_schema must be object'):
        OpenAIResponsesJsonModel(api_key='x',model='m',response_schema=['bad'],response_name='x',transport=t)
    with pytest.raises(DomainError,match='JSON serializable'):
        OpenAIResponsesJsonModel(api_key='x',model='m',response_schema={'type':'object','x':object()},response_name='x',transport=t)
    with pytest.raises(DomainError,match='size limit'):
        OpenAIResponsesJsonModel(api_key='x',model='m',response_schema={'type':'object','x':'a'*33000},response_name='x',transport=t)
    m=adapter(t)
    with pytest.raises(DomainError,match='prompts must be strings'):
        m.complete_json(system=1,user='u')


def test_oversized_and_missing_structured_output_fail_closed():
    m=adapter(FakeTransport(b'x'*2_000_001))
    with pytest.raises(ModelFailure,match='exceeds size limit'):
        m.complete_json(system='s',user='u')
    for output in [
        [{'type':'reasoning'}],
        [{'type':'message','content':'not-a-list'}],
        [{'type':'message','content':[None,{'type':'unknown'}]}],
        [{'type':'message','content':[{'type':'output_text','text':''}]}],
    ]:
        with pytest.raises(ModelFailure,match='missing or oversized'):
            adapter(FakeTransport({'status':'completed','output':output})).complete_json(system='s',user='u')


def test_no_usage_object_is_valid_and_records_no_usage():
    e=envelope(); e.pop('usage')
    m=adapter(FakeTransport(e))
    assert m.complete_json(system='s',user='u')=={'ok':True}
    assert m.usage==()


def test_schema_builder_rejects_empty_vocabularies():
    with pytest.raises(DomainError,match='vocabularies'):
        intent_output_schema(constraint_fields=[],clarification_fields=['budget'])
    with pytest.raises(DomainError,match='vocabularies'):
        intent_output_schema(constraint_fields=['price_paise'],clarification_fields=[])


def test_urllib_transport_success_and_failures(monkeypatch):
    import io
    from urllib.error import HTTPError, URLError
    import agentcommit.ai.openai_provider as provider

    class Resp:
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def read(self): return b'{"ok":true}'

    monkeypatch.setattr(provider,'urlopen',lambda request,timeout: Resp())
    transport=provider.UrllibHttpJsonTransport()
    assert transport.post_json(url='https://example.com',headers={},body=b'{}',timeout_s=1)==b'{"ok":true}'

    err=HTTPError('https://example.com',500,'boom',{},io.BytesIO(b'server exploded'))
    monkeypatch.setattr(provider,'urlopen',lambda request,timeout: (_ for _ in ()).throw(err))
    with pytest.raises(ModelFailure,match='HTTP 500'):
        transport.post_json(url='https://example.com',headers={},body=b'{}',timeout_s=1)

    monkeypatch.setattr(provider,'urlopen',lambda request,timeout: (_ for _ in ()).throw(URLError('down')))
    with pytest.raises(ModelFailure,match='transport failure'):
        transport.post_json(url='https://example.com',headers={},body=b'{}',timeout_s=1)
