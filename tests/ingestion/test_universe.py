from __future__ import annotations

from datetime import date

import pandas as pd
from sqlmodel import Session, select

from app.db.models import SectorTag, Security
from app.ingestion import universe
from app.ingestion import vix as vix_module


def test_seed_universe_updates_existing_security_and_inserts_missing_ones(db_engine):
    with Session(db_engine) as session:
        session.add(
            Security(
                ticker="NVDA",
                name="Old Name",
                exchange="NYSE",
                sector_tag=SectorTag.ai,
                currency="USD",
                is_active=True,
            )
        )
        session.commit()

    inserted_count = universe.seed_universe(db_engine)

    assert inserted_count == len(universe.UNIVERSE) - 1
    with Session(db_engine) as session:
        updated = session.exec(select(Security).where(Security.ticker == "NVDA")).one()
        assert updated.name == "NVIDIA Corporation"
        assert updated.exchange == "NASDAQ"
        assert updated.sector_tag == SectorTag.ai


def test_seed_universe_is_noop_when_database_already_matches_the_universe(db_engine):
    with Session(db_engine) as session:
        session.add_all(
            [
                Security(
                    ticker=entry.ticker,
                    name=entry.name,
                    exchange=entry.exchange,
                    sector_tag=entry.sector_tag,
                    currency="USD",
                    is_active=True,
                )
                for entry in universe.UNIVERSE
            ]
        )
        session.commit()

    inserted_count = universe.seed_universe(db_engine)

    assert inserted_count == 0


def test_to_macro_rows_flattens_multiindex_columns_and_drops_missing_close_values():
    frame = pd.DataFrame(
        [
            [1.0, 2.0, None],
            [3.0, 4.0, 5.0],
        ],
        index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
        columns=pd.MultiIndex.from_tuples(
            [
                ("VIX", "Open"),
                ("VIX", "Close"),
                ("VIX", "Volume"),
            ]
        ),
    )

    rows = vix_module.to_macro_rows(frame)

    assert len(rows) == 2
    assert rows[0].obs_date == date(2024, 1, 1)
    assert rows[0].vix == vix_module.Decimal("2.000")
    assert rows[1].obs_date == date(2024, 1, 2)
    assert rows[1].vix == vix_module.Decimal("4.000")


def test_ingest_vix_fetches_history_maps_rows_and_upserts_them(monkeypatch, db_engine):
    frame = pd.DataFrame(
        {"Close": [10.5, None]},
        index=pd.to_datetime(["2024-02-01", "2024-02-02"]),
    )

    monkeypatch.setattr(vix_module, "fetch_vix_history", lambda: frame)
    captured = {}

    def fake_upsert(rows, engine=None):
        captured["rows"] = rows

    monkeypatch.setattr(vix_module, "upsert_macro_daily_rows", fake_upsert)

    row_count = vix_module.ingest_vix(engine=db_engine)

    assert row_count == 1
    assert len(captured["rows"]) == 1
    assert captured["rows"][0].obs_date == date(2024, 2, 1)
    assert captured["rows"][0].vix == vix_module.Decimal("10.500")
