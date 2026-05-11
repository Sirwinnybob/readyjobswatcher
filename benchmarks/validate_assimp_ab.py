"""
A/B conversion validator for DAE -> GLB Assimp migration.

Compares, per DAE:
1) Existing watcher output (3d_medium.glb)
2) Assimp raw conversion
3) Assimp minimal-clean conversion (init_from path normalization only)
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import uuid
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ready_jobs_watcher import dae_converter


_INIT_FROM = re.compile(r"<init_from>([\s\S]*?)</init_from>")


def _fix_windows_paths(content: str) -> str:
    return _INIT_FROM.sub(
        lambda m: "<init_from>" + m.group(1).replace("\\\\", "/").replace("\\", "/") + "</init_from>",
        content,
    )


def _watcher_prepare(content: str) -> str:
    # Match watcher preprocessing path.
    return dae_converter._triangulate_collada_polygons(_fix_windows_paths(content))


def _find_assimp_explicit_or_default(explicit: Optional[str]) -> Optional[str]:
    if explicit:
        p = Path(explicit)
        return str(p) if p.exists() else None

    env_path = Path(str(Path.cwd()))
    _ = env_path  # keep linter quiet in simple runtime env

    for candidate in (
        shutil.which("assimp"),
        r"C:\Scripts\Assimp\assimp\build_windows\bin\Release\assimp.exe",
        r"C:\Scripts\Assimp\assimp-v6.0.5-isolated\install\bin\assimp.exe",
        r"C:\Scripts\Assimp\assimp-v6.0.5-isolated\build\bin\Release\assimp.exe",
    ):
        if not candidate:
            continue
        p = Path(candidate)
        if p.exists() and p.is_file():
            return str(p)
    return None


@dataclass
class SceneStats:
    meshes: int
    vertices: int
    faces: int

    def to_dict(self) -> dict:
        return {"meshes": self.meshes, "vertices": self.vertices, "faces": self.faces}


def _scene_stats(glb_path: Path) -> SceneStats:
    import trimesh

    scene = trimesh.load(str(glb_path), force="scene")
    geoms = list(scene.geometry.values()) if hasattr(scene, "geometry") else []
    return SceneStats(
        meshes=len(geoms),
        vertices=sum(len(g.vertices) for g in geoms if hasattr(g, "vertices") and g.vertices is not None),
        faces=sum(len(g.faces) for g in geoms if hasattr(g, "faces") and g.faces is not None),
    )


def _run_assimp(assimp_exe: str, cwd: Path, src_name: str, dst_name: str, timeout_sec: int) -> subprocess.CompletedProcess:
    cmd = [
        assimp_exe,
        "export",
        f".\\{src_name}",
        f".\\{dst_name}",
        "glb2",
        "-tri",
        "-gn",
        "-jiv",
        "-et",
        "-emb",
    ]
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout_sec, check=False)


def _collect_daes(test_root: Path) -> list[Path]:
    return sorted(p for p in test_root.rglob("*") if p.is_file() and p.suffix.lower() == ".dae")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-root", default=r"C:\Scripts\KKCSheetTracker\test\3D")
    parser.add_argument("--assimp", default=None, help="Path to assimp.exe under test (e.g. isolated v6.0.5 build).")
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--timeout-sec", type=int, default=300)
    args = parser.parse_args()

    test_root = Path(args.test_root)
    if not test_root.exists():
        print(f"ERROR: test root does not exist: {test_root}")
        return 2

    assimp_exe = _find_assimp_explicit_or_default(args.assimp)
    if not assimp_exe:
        print("ERROR: assimp.exe not found. Pass --assimp or install/build Assimp.")
        return 2

    daes = _collect_daes(test_root)
    if not daes:
        print(f"ERROR: no .dae files found under {test_root}")
        return 2

    report = {
        "assimpExe": assimp_exe,
        "testRoot": str(test_root),
        "files": [],
    }

    failures = 0
    for dae in daes:
        room_dir = dae.parent
        watcher_glb = room_dir / "3d_medium.glb"

        tmp_prefix = uuid.uuid4().hex
        raw_name = f".tmp_ab_{tmp_prefix}_raw.dae"
        min_name = f".tmp_ab_{tmp_prefix}_minimal.dae"
        raw_out = f".tmp_ab_{tmp_prefix}_raw.glb"
        min_out = f".tmp_ab_{tmp_prefix}_minimal.glb"
        raw_dae = room_dir / raw_name
        min_dae = room_dir / min_name
        raw_glb = room_dir / raw_out
        min_glb = room_dir / min_out

        entry = {"dae": str(dae), "watcherGlb": str(watcher_glb), "assimpRaw": {}, "assimpMinimal": {}}

        try:
            text = dae.read_text(encoding="utf-8", errors="ignore")
            raw_dae.write_text(text, encoding="utf-8")
            min_dae.write_text(_watcher_prepare(text), encoding="utf-8")

            raw_proc = _run_assimp(assimp_exe, room_dir, raw_name, raw_out, args.timeout_sec)
            min_proc = _run_assimp(assimp_exe, room_dir, min_name, min_out, args.timeout_sec)

            entry["assimpRaw"]["returnCode"] = raw_proc.returncode
            entry["assimpMinimal"]["returnCode"] = min_proc.returncode
            entry["assimpRaw"]["stderr"] = (raw_proc.stderr or "").strip()[:500]
            entry["assimpMinimal"]["stderr"] = (min_proc.stderr or "").strip()[:500]
            entry["assimpRaw"]["stdout"] = (raw_proc.stdout or "").strip()[:500]
            entry["assimpMinimal"]["stdout"] = (min_proc.stdout or "").strip()[:500]

            if watcher_glb.exists():
                entry["watcherStats"] = _scene_stats(watcher_glb).to_dict()
            else:
                entry["watcherStats"] = None

            if raw_glb.exists():
                raw_stats = _scene_stats(raw_glb)
                entry["assimpRaw"]["stats"] = raw_stats.to_dict()
            else:
                raw_stats = None
                entry["assimpRaw"]["stats"] = None

            if min_glb.exists():
                min_stats = _scene_stats(min_glb)
                entry["assimpMinimal"]["stats"] = min_stats.to_dict()
            else:
                min_stats = None
                entry["assimpMinimal"]["stats"] = None

            # Regression gate:
            # - minimal path must succeed
            # - if raw succeeds too, minimal should match raw geometry counts
            if min_stats is None:
                entry["minimalMatchesRaw"] = False
                failures += 1
            elif raw_stats is None:
                entry["minimalMatchesRaw"] = None
                entry["note"] = "raw assimp path failed (expected for files with <ph>/<h>); minimal path used as canonical"
            else:
                same = (
                    raw_stats.meshes == min_stats.meshes
                    and raw_stats.vertices == min_stats.vertices
                    and raw_stats.faces == min_stats.faces
                )
                entry["minimalMatchesRaw"] = same
                if not same:
                    failures += 1
        except Exception as exc:
            entry["error"] = str(exc)
            failures += 1
        finally:
            for p in (raw_dae, min_dae, raw_glb, min_glb):
                try:
                    if p.exists():
                        p.unlink()
                except OSError:
                    pass

        report["files"].append(entry)

    if args.output_json:
        Path(args.output_json).write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Assimp under test: {assimp_exe}")
    print(f"DAE files checked: {len(report['files'])}")
    for file_entry in report["files"]:
        watcher = file_entry.get("watcherStats")
        raw = file_entry["assimpRaw"].get("stats")
        minimal = file_entry["assimpMinimal"].get("stats")
        print(f"\n- {file_entry['dae']}")
        print(f"  watcher  : {watcher}")
        print(f"  raw      : {raw}")
        print(f"  minimal  : {minimal}")
        print(f"  matched  : {file_entry.get('minimalMatchesRaw')}")
        if file_entry.get("error"):
            print(f"  error    : {file_entry['error']}")

    if failures:
        print(f"\nA/B validation FAILURES: {failures}")
        return 1

    print("\nA/B validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
