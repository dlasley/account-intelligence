from pathlib import Path

import pytest

from src.worker import main

_QUANTAS_LABS_SHAPED = Path("fixtures/quantas-labs-shaped")


@pytest.mark.skipif(
    not _QUANTAS_LABS_SHAPED.exists(),
    reason="quantas-labs pilot data moved to .private/; not present in tracked tree",
)
def test_worker_ingest_fixtures(capsys) -> None:
    main(["ingest-fixtures", "--scenario", "quantas-labs-shaped"])
    out = capsys.readouterr().out
    assert "quantas-labs-shaped" in out
    assert "external_id" in out


def test_worker_no_command(capsys) -> None:
    main([])
    out = capsys.readouterr().out
    assert "usage" in out.lower() or "ingest-fixtures" in out
