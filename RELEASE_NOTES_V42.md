# AgentCommit V4.2 Provider-Ready RC

Tag: `v4.2-provider-ready-rc`

This release adds a real OpenAI Responses API adapter using strict Structured Outputs and a live evaluator over the frozen V4.1 60-case held-out dataset.

It is **provider-ready/offline certified**, not real-model certified: `OPENAI_API_KEY` was unavailable during certification, so the live model stage is `NOT_RUN`.

The deterministic AgentCommit safety boundary remains authoritative even when a real model is connected.
