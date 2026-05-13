from pathlib import Path

import pytest

from src.worker import main

_ELICIT_SHAPED = Path("fixtures/elicit-shaped")


@pytest.mark.skipif(
    not _ELICIT_SHAPED.exists(),
    reason="elicit pilot data moved to .private/; not present in tracked tree",
)
def test_worker_ingest_fixtures(capsys) -> None:
    main(["ingest-fixtures", "--scenario", "elicit-shaped"])
    out = capsys.readouterr().out
    assert "elicit-shaped" in out
    assert "external_id" in out


def test_worker_no_command(capsys) -> None:
    main([])
    out = capsys.readouterr().out
    assert "usage" in out.lower() or "ingest-fixtures" in out
