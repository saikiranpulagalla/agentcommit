# Codex handoff

This ZIP intentionally includes `.git/`.

## Goal
Publish the final AgentCommit repository to:
`https://github.com/saikiranpulagalla/agentcommit`

## First checks
```bash
git status --short
git log --oneline --decorate --graph --all -20
git tag --list
python scripts/submission_check.py
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest --collect-only -q
```

Read `RECONSTRUCTED_GIT_HISTORY.md` before interpreting commit SHAs. History was reconstructed from certified release artifacts after the original `.git` metadata was lost.

## Publication
The final desired public repository should contain this working tree, **not** temporary `.publish/` files from earlier connector attempts.

If needed:
```bash
git remote add origin https://github.com/saikiranpulagalla/agentcommit.git
git fetch origin
```

Because a partial public repo already exists, inspect it before pushing. Make remote `main` represent this final working tree. Preserve reconstructed milestone tags when feasible. Do not claim these reconstructed SHAs are original development SHAs.

After push verify:
- `README.md`
- `src/agentcommit/`
- `tests/`
- `evals/`
- `docs/`
- no `.publish/` directory
- public visibility

## Evidence honesty
Do not alter these claims without real evidence:
- real LLM held-out accuracy: `NOT_RUN`
- real Razorpay Test Mode end-to-end certification: `NOT_RUN`
