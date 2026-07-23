"""Standalone helper process for the real (non-mocked) os.execv quoting test
in test_main_observer_resilience.py.

Not a pytest test module -- the leading underscore keeps it out of pytest's
default collection (test_*.py). It is invoked as its own subprocess by
test_execv_with_quoted_argv_actually_relaunches_on_windows, in two stages:

  1. "launch" -- imports the real ready_jobs_watcher.main._win32_quote_argv
     fix and calls the *actual* os.execv (not a Mock) to relaunch this same
     script with a "relaunched" marker argument.
  2. "relaunched" -- writes a marker file to prove the relaunch actually
     started and received its arguments intact.

If the quoting fix regresses, stage 1's os.execv silently fails to produce a
working process (per the reviewer's repro, this is not a catchable Python
exception) and the marker file from stage 2 never appears -- which is what
the calling test asserts on.
"""
import os
import sys


def main():
    mode = sys.argv[1]
    marker_path = sys.argv[2]

    if mode == "launch":
        repo_root = sys.argv[3]
        sys.path.insert(0, repo_root)
        from ready_jobs_watcher.main import _win32_quote_argv

        exec_args = _win32_quote_argv(
            [sys.executable, os.path.abspath(__file__), "relaunched", marker_path]
        )
        os.execv(sys.executable, exec_args)
        # Unreachable if execv succeeds; if it returns at all, something is
        # very wrong (execv either replaces the process or raises).
        raise RuntimeError("os.execv returned instead of replacing the process")

    elif mode == "relaunched":
        with open(marker_path, "w", encoding="utf-8") as f:
            f.write("relaunched-ok")

    else:
        raise SystemExit(f"unknown mode {mode!r}")


if __name__ == "__main__":
    main()
