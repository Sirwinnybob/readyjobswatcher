# Manual Consolidation Action Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GUI action that lets an operator manually run the same tracker consolidation and metadata sweep used by the scheduled end-of-day job.

**Architecture:** Reuse `MetadataRefreshService.run_scheduled_sweep(consolidate_trackers=True)` from the SettingsWindow Actions tab. The GUI only triggers and reports the existing service result; it does not duplicate consolidation logic.

**Tech Stack:** Python, PyQt6, pytest.

## Global Constraints

- Manual and scheduled consolidation must use the same service method.
- The GUI must not block while the sweep runs.
- Missing service or runtime errors must produce an operator-visible warning/error.

---

### Task 1: Add Manual Consolidation GUI Action

**Files:**
- Modify: `ready_jobs_watcher/gui.py`
- Test: `tests/test_gui_actions.py`

**Interfaces:**
- Consumes: `app_instance.metadata_refresh_service.run_scheduled_sweep(consolidate_trackers=True) -> dict`
- Produces: `SettingsWindow.trigger_run_consolidation()`

- [ ] **Step 1: Write failing tests**

Add tests that verify the Actions tab includes a consolidation button and that `trigger_run_consolidation()` invokes the metadata refresh service with `consolidate_trackers=True`.

- [ ] **Step 2: Run tests and confirm red**

Run: `python -m pytest tests/test_gui_actions.py -q`

- [ ] **Step 3: Implement the GUI action**

Add a `Run Tracker Consolidation Now` button to `setup_actions_tab()`. Implement `trigger_run_consolidation()` with background-thread execution and message-box reporting.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_gui_actions.py -q`

- [ ] **Step 5: Run regression checks**

Run: `python -m pytest tests/test_gui_actions.py tests/test_metadata_scheduler_integration.py -q`
