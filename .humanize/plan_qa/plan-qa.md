# Refine Plan QA

## Summary

One inline `<cmt>...</cmt>` block (`CMT-1`) was extracted from the plan. The comment, attached to the predict-before-call paragraph in the original-draft appendix, redirected the predict-before-call design from an isolated extra LLM round-trip with a modal-rendered summary to a system-prompt augmentation that asks the main LLM to write a visible prediction before it emits a tool call. The comment was classified as a `change_request` and required user input on the exact mechanism (three options offered: model self-prediction, hybrid predict-and-revise, or keeping the converged plan). The user chose **model self-prediction**, so AC-5 was rewritten as a system-prompt augmentation, AC-4's `visible_summary` field was demoted to a reserved `None` field, the predictor helper and 30-second timeout were removed, DEC-5 (timeout) was marked SUPERSEDED, a new DEC-8 was added to record the refined design choice, and the affected task rows, milestones, deliberation entries, and Implementation Notes were updated. Convergence status remains `converged` with a refinement-round annotation.

## Comment Ledger

| CMT-ID | Classification | Location | Original Text (excerpt) | Disposition |
|--------|----------------|----------|-------------------------|-------------|
| CMT-1 | change_request | Original Design Draft Start → Primary Direction → Approach Summary, item 3 (`Predict-before-call as a policy consumer`) | "My idea is to provide the LLM with prediction-related prompts, so that before each tool call, the LLM can think based on its own predictions and make more sensible and rational tool calls." | applied |

## Answers

(No `question`-type comments were extracted; this section intentionally records that there are no answers to log.)

## Research Findings

(No `research_request`-type comments were extracted; the change-request was resolved through user clarification rather than repository research.)

## Plan Changes Applied

### CMT-1: Predict-before-call should let the main LLM self-predict, not run an isolated predictor

**Original Comment:**
```
My idea is to provide the LLM with prediction-related prompts, so that before each tool call, the LLM can think based on its own predictions and make more sensible and rational tool calls.
```

**User clarification (DEC-8):**
The comment had three plausible readings (isolated predictor, hybrid predict-and-revise, model self-prediction). The user selected **model self-prediction (B)**: the system prompt should be augmented with a predict-before-call instruction so the main LLM writes a visible prediction in its reasoning/content stream before emitting a tool call. No second isolated LLM call is performed at runtime.

**Changes Made:**

- AC-5 was rewritten end-to-end. It now describes a system-prompt augmentation in `get_system_prompt()` (`src/config.py`) gated by `GEM_CODE_PREDICT_BEFORE_CALL`. Three sub-criteria were added:
  - AC-5.1: system-prompt body differs only when the flag is enabled
  - AC-5.2: the runtime tool-call path issues no extra LLM request regardless of the flag
  - AC-5.3: the gate does not mutate `Session.history`
- AC-4 was edited so `PermissionDecision.visible_summary` is documented as a reserved Optional field that is always `None` in v1 (the prediction lives in the model's visible streaming output, not in the modal).
- AC-11 (test matrix) was updated to test system-prompt presence/absence and history non-mutation, replacing the obsolete 30-second-timeout and auto-deny-skip tests.
- Goal Description bullet 2 was rewritten to match the system-prompt design.
- Path Boundaries Upper Bound was rewritten to drop the 30-second timeout and isolated-request language and to describe the system-prompt clause; Lower Bound was rewritten to say predict-before-call is a no-op at the lower bound when the flag is unwired.
- Allowed Choices "Can use" gained "Augmenting the system prompt in `get_system_prompt()`" and dropped the obsolete `chat_one_step()` reuse line. "Cannot use" gained "Issuing an extra `tool_choice='none'` LLM round-trip from inside the gate solely to generate a prediction" and "Mutating `Session.history` from inside the gate or the permission policy"; the obsolete "Auto-denying when predictor times out" line was removed.
- Conceptual Approach: the `_predict_tool_impact` helper paragraph was replaced with a paragraph describing the `get_system_prompt()` augmentation; the closure-context paragraph was edited to drop the predict-before-call branch from the gate's runtime path.
- Milestone 4 was rewritten as "system-prompt augmentation" with two phases (extend `get_system_prompt()`, verify runtime non-mutation). Its dependency was changed from Milestone 1 Phase B to Milestone 1 Phase C (the Config field must exist; the modal does not need to exist).
- Task Breakdown task5 was rewritten to "Augment `get_system_prompt()` ..."; its `Depends On` changed from `task2, task4` to `task4`. task8 was edited to remove the "predict-before-call is skipped under `auto_deny`" clause; its `Depends On` changed from `task4, task5` to `task4`.
- Resolved Disagreements: the predict-before-call protocol-risk entry was rewritten to point to the new design under DEC-8.
- DEC-5 was marked SUPERSEDED with a pointer to DEC-8 and an explanation that the 30-second timeout assumed a round-trip that no longer exists.
- DEC-8 was added with the user's decision recorded as the resolution.
- Implementation Notes: dropped the `_predict_tool_impact` reference from the naming bullet; replaced the "Predict-before-call MUST NOT log full prompts to stdout" line with a line stating predict-before-call is a system-prompt augmentation with no runtime LLM call.
- Convergence Status: a refinement-round annotation was appended noting the AC-5 redesign and DEC-5 supersession; final status remains `converged`.

**Affected Sections:**
- Goal Description: bullet 2 rewritten to describe the system-prompt design.
- Acceptance Criteria: AC-4 `visible_summary` clarified as reserved; AC-5 rewritten with three sub-criteria; AC-11 predict tests updated.
- Path Boundaries: Upper Bound, Lower Bound, and the Can-use / Cannot-use lists rewritten.
- Feasibility Hints and Suggestions: Conceptual Approach two paragraphs rewritten.
- Dependencies and Sequence: Milestone 4 phases and Milestone-4 dependency line rewritten.
- Task Breakdown: task5 rewritten; task8 edited; dependency lists updated.
- Claude-Codex Deliberation: Resolved Disagreements predict entry rewritten; Convergence Status refinement-round annotation appended.
- Pending User Decisions: DEC-5 superseded; DEC-8 added.
- Implementation Notes: naming bullet adjusted; predict-before-call rule rewritten.

**Cross-Reference Updates:**
- Task5 dependencies: `task2, task4` → `task4`.
- Task8 dependencies: `task4, task5` → `task4`.
- Milestone 4 dependency: now reads "Milestone 4 depends on Milestone 1 Phase C" instead of "Phase B".
- AC-4 explicitly notes `visible_summary` is reserved (always `None` in v1).
- AC-5 is the canonical reference for the predict mechanism; downstream sections defer to it.
- DEC-5 status carries forward as `SUPERSEDED by DEC-8` rather than being deleted, so the deliberation history remains traceable.

---

## Remaining Decisions

(All comments were resolved through user input during refinement. DEC-8 was added during refinement and recorded with its final user decision; it is not pending. No `PENDING` decisions remain.)

---

## Refinement Metadata

- **Input Plan:** docs/plan.md
- **Output Plan:** docs/plan.md (in-place)
- **QA Document:** .humanize/plan_qa/plan-qa.md
- **Total Comments Processed:** 1
  - Questions: 0
  - Change Requests: 1
  - Research Requests: 0
- **Plan Sections Modified:** Goal Description; Acceptance Criteria (AC-4, AC-5, AC-11); Path Boundaries (Upper Bound, Lower Bound, Allowed Choices); Feasibility Hints and Suggestions (Conceptual Approach); Dependencies and Sequence (Milestone 4 + dependency notes); Task Breakdown (task5, task8); Claude-Codex Deliberation (Resolved Disagreements, Convergence Status); Pending User Decisions (DEC-5 superseded, DEC-8 added); Implementation Notes (Code Style Requirements).
- **Convergence Status:** converged
- **Refinement Date:** 2026-05-15
