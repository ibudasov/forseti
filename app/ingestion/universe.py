from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlmodel import select

from app.db.models import SectorTag, Security
from app.db.session import get_engine, get_session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UniverseSecurity:
    ticker: str
    name: str
    exchange: str
    sector_tag: SectorTag


UNIVERSE: tuple[UniverseSecurity, ...] = (
    UniverseSecurity("NVDA", "NVIDIA Corporation", "NASDAQ", SectorTag.ai),
    UniverseSecurity("MSFT", "Microsoft Corporation", "NASDAQ", SectorTag.ai),
    UniverseSecurity("GOOGL", "Alphabet Inc. (Class A)", "NASDAQ", SectorTag.ai),
    UniverseSecurity("AMD", "Advanced Micro Devices, Inc.", "NASDAQ", SectorTag.ai),
    UniverseSecurity("PLTR", "Palantir Technologies Inc.", "NASDAQ", SectorTag.ai),
    UniverseSecurity("LMT", "Lockheed Martin Corporation", "NYSE", SectorTag.defence),
    UniverseSecurity("RTX", "RTX Corporation", "NYSE", SectorTag.defence),
    UniverseSecurity("NOC", "Northrop Grumman Corporation", "NYSE", SectorTag.defence),
    UniverseSecurity("GD", "General Dynamics Corporation", "NYSE", SectorTag.defence),
    UniverseSecurity(
        "KTOS",
        "Kratos Defense & Security Solutions, Inc.",
        "NASDAQ",
        SectorTag.defence,
    ),
    UniverseSecurity("CEG", "Constellation Energy Corporation", "NASDAQ", SectorTag.nuclear),
    UniverseSecurity("VST", "Vistra Corp.", "NYSE", SectorTag.nuclear),
    UniverseSecurity("SMR", "NuScale Power Corporation", "NYSE", SectorTag.nuclear),
    UniverseSecurity("OKLO", "Oklo Inc.", "NYSE", SectorTag.nuclear),
    UniverseSecurity("BWXT", "BWX Technologies, Inc.", "NYSE", SectorTag.nuclear),
    UniverseSecurity("NEE", "NextEra Energy, Inc.", "NYSE", SectorTag.green_energy),
    UniverseSecurity("ENPH", "Enphase Energy, Inc.", "NASDAQ", SectorTag.green_energy),
    UniverseSecurity("FSLR", "First Solar, Inc.", "NASDAQ", SectorTag.green_energy),
    UniverseSecurity("BE", "Bloom Energy Corporation", "NYSE", SectorTag.green_energy),
    UniverseSecurity("PLUG", "Plug Power Inc.", "NASDAQ", SectorTag.green_energy),
    UniverseSecurity("IBM", "International Business Machines Corporation", "NYSE", SectorTag.quantum),
    UniverseSecurity("IONQ", "IonQ, Inc.", "NYSE", SectorTag.quantum),
    UniverseSecurity("RGTI", "Rigetti Computing, Inc.", "NASDAQ", SectorTag.quantum),
    UniverseSecurity("QBTS", "D-Wave Quantum Inc.", "NYSE", SectorTag.quantum),
    UniverseSecurity("QUBT", "Quantum Computing Inc.", "NASDAQ", SectorTag.quantum),
    UniverseSecurity("ISRG", "Intuitive Surgical, Inc.", "NASDAQ", SectorTag.robotics),
    UniverseSecurity("TER", "Teradyne, Inc.", "NASDAQ", SectorTag.robotics),
    UniverseSecurity("ZBRA", "Zebra Technologies Corporation", "NASDAQ", SectorTag.robotics),
    UniverseSecurity("SYM", "Symbotic Inc.", "NASDAQ", SectorTag.robotics),
    UniverseSecurity("SERV", "Serve Robotics Inc.", "NASDAQ", SectorTag.robotics),
    UniverseSecurity("RKLB", "Rocket Lab USA, Inc.", "NASDAQ", SectorTag.space),
    UniverseSecurity("LUNR", "Intuitive Machines, Inc.", "NASDAQ", SectorTag.space),
    UniverseSecurity("ASTS", "AST SpaceMobile, Inc.", "NASDAQ", SectorTag.space),
    UniverseSecurity("SPCE", "Virgin Galactic Holdings, Inc.", "NYSE", SectorTag.space),
    UniverseSecurity("PL", "Planet Labs PBC", "NYSE", SectorTag.space),
)


def seed_universe(engine=None) -> int:
    engine = engine or get_engine()
    inserted_count = 0
    updated_count = 0

    with get_session(engine) as session:
        for entry in UNIVERSE:
            security = session.exec(select(Security).where(Security.ticker == entry.ticker)).first()
            if security is None:
                session.add(
                    Security(
                        ticker=entry.ticker,
                        name=entry.name,
                        exchange=entry.exchange,
                        sector_tag=entry.sector_tag,
                        currency="USD",
                        is_active=True,
                    )
                )
                inserted_count += 1
                continue

            if (
                security.name != entry.name
                or security.exchange != entry.exchange
                or security.sector_tag != entry.sector_tag
            ):
                security.name = entry.name
                security.exchange = entry.exchange
                security.sector_tag = entry.sector_tag
                session.add(security)
                updated_count += 1

        session.commit()

    skipped_count = len(UNIVERSE) - inserted_count - updated_count
    logger.info(
        "seed_universe_complete: inserted=%s updated=%s skipped=%s",
        inserted_count,
        updated_count,
        skipped_count,
    )
    return inserted_count
