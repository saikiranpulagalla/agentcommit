from __future__ import annotations
import os, sys
from agentcommit.store.sqlite_store import MerchantStore, Conflict

if __name__ == '__main__':
    db, suffix, quote = sys.argv[1:4]
    s=MerchantStore(db)
    try:
        s.activate_plan_from_quote(
            plan_id=f'P-{suffix}',grant_id=f'G-{suffix}',execution_id=f'E-{suffix}',reservation_id=f'R-{suffix}',
            delegation_id='D',quote_id=quote,now_ms=1_800_000_000_000,ttl_ms=50_000,
        )
        print('OK', flush=True)
    except Conflict as exc:
        print(f'CONFLICT:{exc}', flush=True)
