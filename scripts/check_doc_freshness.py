"""Verify the claims in tracked documentation that can be recomputed from the repo.

WHAT
    Documentation states things that are true when written and quietly stop being
    true: counts of migrations or tests, an ADR index that should list exactly the
    ADR files on disk, committed fixtures that should match what the generator
    produces today. Each of those is recomputable, so each can be checked instead
    of remembered.

WHY IT EXISTS
    Every one of these checks has already been run by hand after drift was found
    in it. Doing them by hand only catches drift once someone thinks to look.

WHAT IT DOES NOT DO
    A check catches what it was written to catch. Prose claims, stale rationale,
    and a rotting statement in a brand-new document are all invisible here — those
    need a reader. Add a check whenever a new class of recomputable claim appears.

USAGE
    uv run python scripts/check_doc_freshness.py            # fast checks only
    uv run python scripts/check_doc_freshness.py --fixtures # also regenerate and diff
    uv run python scripts/check_doc_freshness.py --tests    # also collect pytest

    Exit code 0 when every check passes, 1 when any fails.
"""

from __future__ import annotations

import argparse
import filecmp
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

MIGRATIONS = REPO / "supabase" / "migrations"
ADR_DIR = REPO / "docs" / "adr"
ADR_INDEX = ADR_DIR / "README.md"
README = REPO / "README.md"
SCENARIOS = REPO / "fixtures" / "synthetic-scenarios"
MATERIALIZED = REPO / "fixtures" / "synthetic"

#: Supabase CLI reads everything before the first underscore as the migration
#: version, so a YYYYMMDD_NNNNNN_ name collides across same-day migrations and
#: aborts `supabase start`. Names must carry a single 14-digit timestamp.
MIGRATION_NAME_RE = re.compile(r"^\d{14}_[a-z0-9_]+\.sql$")


@dataclass
class Result:
    name: str
    ok: bool
    detail: str


def check_migration_filenames() -> Result:
    bad = [p.name for p in sorted(MIGRATIONS.glob("*.sql")) if not MIGRATION_NAME_RE.match(p.name)]
    if bad:
        return Result(
            "migration filenames",
            False,
            "not in the CLI's YYYYMMDDHHMMSS_name.sql form (breaks `supabase start`): "
            + ", ".join(bad),
        )
    count = len(list(MIGRATIONS.glob("*.sql")))
    return Result("migration filenames", True, f"{count} files, all well-formed")


def check_migration_count() -> Result:
    actual = len(list(MIGRATIONS.glob("*.sql")))
    text = README.read_text()
    claims = re.findall(r"(\d+) numbered SQL migrations", text)
    if not claims:
        return Result("migration count in README", True, "no count claimed")
    mismatched = [c for c in claims if int(c) != actual]
    if mismatched:
        return Result(
            "migration count in README",
            False,
            f"README claims {mismatched[0]}, found {actual}",
        )
    return Result("migration count in README", True, f"claims {actual}, found {actual}")


def check_adr_index() -> Result:
    on_disk = {p.stem for p in ADR_DIR.glob("adr-*.md")}
    index = ADR_INDEX.read_text()
    listed = set(re.findall(r"\(\.?/?(adr-[a-z0-9-]+)\.md\)", index))
    missing = on_disk - listed
    extra = listed - on_disk
    problems = []
    if missing:
        problems.append(f"on disk but not in the index: {', '.join(sorted(missing))}")
    if extra:
        problems.append(f"in the index but not on disk: {', '.join(sorted(extra))}")
    if problems:
        return Result("ADR index", False, "; ".join(problems))
    return Result("ADR index", True, f"{len(on_disk)} ADRs, index matches disk")


def check_test_count() -> Result:
    text = README.read_text()
    claims = re.findall(r"\((\d+) passing\)", text)
    if not claims:
        return Result("test count in README", True, "no count claimed")
    proc = subprocess.run(
        ["uv", "run", "pytest", "--collect-only", "-q"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    m = re.search(r"(\d+) tests? collected", proc.stdout)
    if not m:
        return Result("test count in README", False, "could not parse `pytest --collect-only`")
    actual = int(m.group(1))
    mismatched = [c for c in claims if int(c) != actual]
    if mismatched:
        return Result(
            "test count in README", False, f"README claims {mismatched[0]}, collected {actual}"
        )
    return Result("test count in README", True, f"claims {actual}, collected {actual}")


def check_fixtures_match_generator() -> Result:
    """Regenerate each materialized scenario into a temp dir and compare.

    Committed fixtures are a snapshot of generator output. When the generator
    changes and the snapshot is not refreshed, the two silently disagree — and
    the committed copy is what tests and `process-fixtures` consume.
    """
    stale: list[str] = []
    for scenario_dir in sorted(MATERIALIZED.iterdir()):
        if not scenario_dir.is_dir():
            continue
        yaml = SCENARIOS / f"{scenario_dir.name}.yaml"
        if not yaml.exists():
            continue
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / scenario_dir.name
            proc = subprocess.run(
                [
                    "uv", "run", "python", "-m", "src.worker", "synthesise-fixtures",
                    "--scenario", str(yaml), "--out", str(out),
                ],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                stale.append(f"{scenario_dir.name} (generation failed)")
                continue
            for produced in sorted(out.rglob("*.json")):
                rel = produced.relative_to(out)
                # last_run.json records a wall-clock generation timestamp.
                if rel.name == "last_run.json":
                    continue
                committed = scenario_dir / rel
                if not committed.exists() or not filecmp.cmp(produced, committed, shallow=False):
                    stale.append(f"{scenario_dir.name}/{rel}")
    if stale:
        shown = ", ".join(stale[:5])
        more = f" (+{len(stale) - 5} more)" if len(stale) > 5 else ""
        return Result(
            "committed fixtures vs generator",
            False,
            f"differ from freshly generated output: {shown}{more}. "
            "Refresh with `synthesise-fixtures` for the affected scenarios.",
        )
    return Result("committed fixtures vs generator", True, "committed output matches the generator")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Check recomputable claims in tracked docs")
    p.add_argument("--tests", action="store_true", help="Also verify the README test count (slow)")
    p.add_argument(
        "--fixtures", action="store_true", help="Also regenerate fixtures and diff (slow)"
    )
    p.add_argument("--all", action="store_true", help="Run every check")
    args = p.parse_args(argv)

    checks = [check_migration_filenames, check_migration_count, check_adr_index]
    if args.tests or args.all:
        checks.append(check_test_count)
    if args.fixtures or args.all:
        checks.append(check_fixtures_match_generator)

    results = [c() for c in checks]
    width = max(len(r.name) for r in results)
    for r in results:
        mark = "ok  " if r.ok else "FAIL"
        print(f"[{mark}] {r.name.ljust(width)}  {r.detail}")

    failed = [r for r in results if not r.ok]
    if failed:
        print(f"\n{len(failed)} check(s) failed.")
        return 1
    print(f"\nAll {len(results)} check(s) passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
