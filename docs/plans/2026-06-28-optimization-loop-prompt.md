# /loop Prompt for Optimization Pass

Copy-paste everything below as the `/loop` prompt:

---

Work through the optimization worklist in `docs/plans/2026-06-28-optimization-loop.md`. Each iteration:

1. Read the worklist file and find the next unchecked `- [ ]` item.
2. Read CLAUDE.md for project constraints.
3. Implement the fix described in that item. Follow the guidance in the item description.
4. Run `python -m pytest` and verify no new failures (3 test_dae_converter failures are pre-existing/environmental — ignore those).
5. If tests pass, commit with a descriptive message. Update the worklist item from `- [ ]` to `- [x]`.
6. If the change is risky or you're unsure, mark the item `[SKIPPED: reason]` in the worklist and move to the next item.
7. If you encounter a pattern that warrants a migration proposal, append it under "## Migration Proposals" in the worklist file.
8. When all items are checked or skipped, stop the loop.

Constraints:
- Do NOT change deployment_gate.json schema fields.
- Do NOT introduce new frameworks or async patterns.
- Each commit = one logical change.
- Build on existing patterns. No speculative abstractions.
- Run tests after EVERY change.
