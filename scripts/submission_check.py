from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def main() -> None:
    v5 = load("V5_DEMO_CERTIFICATION.json")
    v42 = load("V42_CERTIFICATION.json")
    live = load("V42_LIVE_EVAL_STATUS.json")

    assert v5["promotion"] == "PASS_DEMO_RC"
    assert v5["demo_scenario_failures"] == 0
    assert v5["razorpay_test_mode"] == "NOT_RUN"
    assert v5["real_llm_accuracy"] == "NOT_RUN"
    assert v42["promotion_pass"] is True
    assert v42["live_model_status"] == "NOT_RUN"
    assert live["status"] == "NOT_RUN"

    required = [
        "README.md",
        "PITCH_5_MIN.md",
        "SUBMISSION_RC.json",
        "docs/ARCHITECTURE_OVERVIEW.md",
        "docs/DEMO_RUNBOOK.md",
        "docs/EVALUATION.md",
        "docs/PANEL_QA.md",
        "docs/SUBMISSION_CHECKLIST.md",
        "docs/THREAT_MODEL.md",
        "docs/WHAT_BROKE.md",
    ]
    missing = [path for path in required if not (ROOT / path).is_file()]
    assert not missing, f"missing submission files: {missing}"

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# AgentCommit\n")
    assert "OFFLINE DEMO — NOT REAL MONEY" in readme
    assert "Real LLM evaluation | **NOT_RUN**" in readme
    assert "Razorpay Test Mode | **NOT_RUN**" in readme

    print("SUBMISSION CHECK: PASS")


if __name__ == "__main__":
    main()
