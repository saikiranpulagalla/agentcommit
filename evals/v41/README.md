# V4.1 AI Evaluation

`heldout_intents.jsonl` is the versioned intent-extraction benchmark. Treat it as held-out once the V4.1 prompt/schema is frozen: do not tune prompts on individual failures and then report the same set as an unbiased score.

The dataset covers:
- explicit money and quantity constraints;
- structured product requirements;
- soft preferences and substitution policy;
- material ambiguity / clarification;
- instruction-like adversarial text;
- compositional requests.

The V4.1 evaluator reports exact hard-constraint match, precision/recall, critical money/quantity exact match, clarification exact match, substitution accuracy, and compile failures.

A scripted/gold replay model may be used only to validate the evaluator plumbing. It is **not** a model-quality result. Real AI accuracy must come from an actual model provider run and should record provider/model/version/date plus the dataset SHA-256.
