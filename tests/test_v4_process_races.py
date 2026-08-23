from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import threading

from agentcommit.domain.models import ExecutionState, GrantState
from agentcommit.store.sqlite_store import Conflict, MerchantStore
from conftest import NOW
from test_v4_persistence import setup_v4, activate


def test_process_level_replan_budget_has_exactly_one_winner(store, db):
    setup_v4(store,max_replans=1); activate(store,'a','A')
    # same target SKU, abundant inventory; budget must be the limiting resource
    for i in range(8):
        store.create_quote(quote_id=f'QB-{i}',merchant_id='m',sku='B')
    env=dict(os.environ); env['PYTHONPATH']=str(Path(__file__).parents[1]/'src')
    procs=[subprocess.Popen(
        [sys.executable,str(Path(__file__).with_name('v4_process_worker.py')),str(db),f'b{i}',f'QB-{i}'],
        cwd=str(Path(__file__).parents[1]),env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
    ) for i in range(8)]
    outputs=[]
    for p in procs:
        out,err=p.communicate(timeout=30)
        assert p.returncode==0, err
        outputs.append(out.strip())
    assert outputs.count('OK')==1
    assert sum('REPLAN_BUDGET_EXHAUSTED' in x for x in outputs)==7
    assert store.scalar("SELECT replans_used FROM delegation_intents WHERE delegation_id='D'")==1
    assert store.scalar("SELECT COUNT(*) FROM plans WHERE delegation_id='D' AND state='ACTIVE'")==1


def test_product_facts_update_vs_commit_is_linearizable(store):
    setup_v4(store); g=activate(store,'a','A')
    barrier=threading.Barrier(2)
    def commit():
        barrier.wait()
        try:
            store.commit(request_id='req',grant_id=g.grant_id,now_ms=NOW+1)
            return 'committed'
        except Conflict as exc:
            return str(exc)
    def update():
        barrier.wait()
        store.put_product_facts(merchant_id='m',sku='A',attributes={
            'screen_size_inches':27,'resolution':'4K','usb_c':False,'gaming':False,
        })
        return 'updated'
    with ThreadPoolExecutor(max_workers=2) as ex:
        a=ex.submit(commit); b=ex.submit(update)
        result=a.result(timeout=20); assert b.result(timeout=20)=='updated'
    # If commit linearized first it may succeed. If update linearized first it must reject stale/violating facts.
    if result=='committed':
        assert store.scalar("SELECT state FROM execution_grants WHERE grant_id=?",(g.grant_id,))==GrantState.CONSUMED.value
        assert store.scalar("SELECT state FROM executions WHERE execution_id=?",('E-a',))==ExecutionState.CLAIMED.value
    else:
        assert ('STALE_PRODUCT_FACTS' in result) or ('HARD_CONSTRAINT_VIOLATION' in result)
        assert store.scalar("SELECT state FROM execution_grants WHERE grant_id=?",(g.grant_id,))==GrantState.ACTIVE.value
