"""Integration tests for RAG pipeline with seeded data."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlmodel import SQLModel, create_engine

from testcontainers.community.postgres import PostgresContainer

from app.db.models import DocumentChunk, DocumentSourceType
from app.db.repository import bulk_create_document_chunks
from app.services.retrieval import retrieve
from app.services.synthesis import synthesize_evidence
from app.ingestion.ingestion_manager import IngestionManager


def _create_test_engine(database_url: str):
    return create_engine(database_url, echo=False, future=True)


def _create_test_database(engine):
    SQLModel.metadata.create_all(engine)


@pytest.fixture
def db_engine():
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        engine = _create_test_engine(url)
        SQLModel.metadata.drop_all(engine)
        _create_test_database(engine)
        yield engine
    else:
        container = PostgresContainer("postgres:15")
        with container:
            url = container.get_connection_url()
            engine = _create_test_engine(url)
            _create_test_database(engine)
            yield engine


class TestRAGIntegrationSeededData:
    """Integration tests with realistic seeded data."""
    
    AAPL_EARNINGS_CONTENT = """
    Apple Q1 2024 Earnings Call Summary
    
    Apple reported record revenue of $119.6 billion in Q1 2024, representing strong growth
    across all product categories. iPhone revenue increased 7% year-over-year. Services
    segment showed exceptional growth at 10% YoY, driven by Apple Music and cloud services.
    
    Gross margin improved to 48.5% from 46.2% in the prior year, demonstrating operational
    efficiency improvements. The company continues to benefit from strong demand in emerging
    markets, particularly in India and Southeast Asia.
    
    Management expects continued growth in Q2 2024, though notes some near-term supply chain
    challenges. AI capabilities are being integrated into upcoming product releases.
    
    Key metrics:
    - Operating cash flow: $110 billion
    - Free cash flow: $95 billion
    - R&D investment: $29 billion
    - Employee headcount: 161,000
    
    Outlook: Management provided guidance for Q2 2024 revenue growth of 3-5% YoY,
    with typical seasonal variations. Margin guidance expects 47-48% gross margin.
    """
    
    AAPL_NEWS_CONTENT = """
    Apple Launches New AI Features in iOS 18
    
    Apple announced a comprehensive AI strategy with new features available in iOS 18.
    The company introduced "Apple Intelligence," a suite of AI capabilities designed to
    integrate naturally into iPhone, iPad, and Mac.
    
    Key features include:
    - On-device AI processing for privacy
    - Enhanced Siri with contextual understanding
    - AI-powered image generation
    - Smart email categorization
    
    Industry analysts view this as a significant competitive move against Google and Microsoft,
    both of whom have more advanced AI offerings. Early reviews are positive, with expectations
    that this could drive upgrade cycles.
    
    The move positions Apple strongly in the AI arms race and could provide tailwinds for
    the 2024-2025 fiscal years as users upgrade to access new features.
    """
    
    AAPL_RISK_CONTENT = """
    Apple 10-K Risk Factors
    
    Key Risk Factors:
    1. Concentration of sales in few products: iPhone represents 50% of revenue
    2. Intense competition in smartphone market from Samsung, Google, other manufacturers
    3. Dependence on China manufacturing and market: ~20% of revenue from China
    4. Supply chain disruptions could impact production
    5. Regulatory risks: Antitrust investigations in EU, US, China
    6. Currency fluctuations impact international sales
    7. Cybersecurity and data privacy risks
    8. Dependence on key personnel (CEO, Chief Design Officer)
    9. Climate change and environmental regulations
    10. Geopolitical tensions (US-China relations impact supply chain)
    
    These risks could materially impact Apple's financial performance and stock price.
    """
    
    SECTOR_NEWS_CONTENT = """
    Technology Sector Report: AI Boom Driving Hardware Upgrades
    
    The technology sector is experiencing strong tailwinds from AI adoption. Both consumers
    and enterprises are upgrading hardware to access new AI capabilities.
    
    Key trends:
    - Smartphone shipments up 12% in 2024, driven by AI features
    - Enterprise cloud spending up 18% YoY
    - Semiconductor stocks rallying on strong demand
    - Software companies (Microsoft, Salesforce) showing double-digit growth
    
    However, risks include:
    - AI hype cycle could deflate if capabilities don't deliver
    - Regulators increasingly scrutinizing AI safety
    - Margin compression as competition intensifies
    
    Overall sector outlook: Positive through 2025, with normalization in 2026.
    """
    
    @pytest.mark.asyncio
    async def test_full_rag_pipeline(self, db_engine):
        """Test full pipeline: ingest -> retrieve -> synthesize."""
        manager = IngestionManager()
        
        # Step 1: Ingest documents
        documents = [
            {
                "ticker": "AAPL",
                "source_type": DocumentSourceType.earnings_call,
                "content": self.AAPL_EARNINGS_CONTENT,
                "source_url": "https://investor.apple.com/q1-2024-earnings",
                "published_at": datetime.now(timezone.utc),
            },
            {
                "ticker": "AAPL",
                "source_type": DocumentSourceType.company_news,
                "content": self.AAPL_NEWS_CONTENT,
                "source_url": "https://apple.com/newsroom/ai-launch",
                "published_at": datetime.now(timezone.utc),
            },
            {
                "ticker": "AAPL",
                "source_type": DocumentSourceType.filing_risk,
                "content": self.AAPL_RISK_CONTENT,
                "source_url": "https://sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193",
                "published_at": datetime.now(timezone.utc),
            },
            {
                "ticker": "AAPL",
                "source_type": DocumentSourceType.sector_news,
                "content": self.SECTOR_NEWS_CONTENT,
                "source_url": "https://techanalysis.com/sector-report",
                "published_at": datetime.now(timezone.utc),
            },
        ]
        
        results = await manager.ingest_documents_batch(documents, engine=db_engine)
        
        # Verify ingestion succeeded
        assert len(results) == 4
        assert all(r.success for r in results)
        assert sum(r.chunks_created for r in results) > 0
        
        # Step 2: Retrieve documents
        retrieval_results = retrieve("AAPL", top_k=10, engine=db_engine)
        
        # Verify retrieval succeeded
        assert len(retrieval_results) > 0
        assert all(r.ticker == "AAPL" for r in retrieval_results)
        
        # Step 3: Synthesize evidence
        synthesis = synthesize_evidence(retrieval_results)
        
        # Verify synthesis succeeded
        assert synthesis.status == "complete"
        # With our seeded data, should find bullish drivers (growth, AI)
        # and bearish risks (competition, China risk)
        assert len(synthesis.bullish_drivers) > 0 or len(synthesis.bearish_risks) > 0
        
        # Verify guardrails
        for item in synthesis.bullish_drivers + synthesis.bearish_risks + synthesis.red_flags:
            # No numeric trade parameters
            assert "entry" not in item.text.lower() or "$" not in item.text
            assert "stop loss" not in item.text.lower()
            assert "take profit" not in item.text.lower()
    
    @pytest.mark.asyncio
    async def test_retrieval_with_source_filters(self, db_engine):
        """Test retrieval with source type filtering."""
        manager = IngestionManager()
        
        # Ingest different source types
        await manager.ingest_document(
            ticker="MSFT",
            source_type=DocumentSourceType.earnings_call,
            content="Microsoft Q1 earnings discussion",
            source_url="https://microsoft.com/earnings",
        )
        
        await manager.ingest_document(
            ticker="MSFT",
            source_type=DocumentSourceType.company_news,
            content="Microsoft launches new AI service",
            source_url="https://news.microsoft.com/ai",
        )
        
        # Retrieve only earnings calls
        earnings_results = retrieve(
            "MSFT",
            source_types=[DocumentSourceType.earnings_call],
            engine=db_engine,
        )
        
        assert len(earnings_results) > 0
        assert all(r.source_type == DocumentSourceType.earnings_call for r in earnings_results)
        
        # Retrieve only news
        news_results = retrieve(
            "MSFT",
            source_types=[DocumentSourceType.company_news],
            engine=db_engine,
        )
        
        assert len(news_results) > 0
        assert all(r.source_type == DocumentSourceType.company_news for r in news_results)
    
    @pytest.mark.asyncio
    async def test_no_trade_evidence_synthesis(self, db_engine):
        """Test evidence synthesis for no-trade recommendation."""
        from app.services.synthesis import synthesize_no_trade_evidence
        
        manager = IngestionManager()
        
        # Ingest risk-heavy content
        await manager.ingest_document(
            ticker="RISKY",
            source_type=DocumentSourceType.filing_risk,
            content=self.AAPL_RISK_CONTENT,  # Reuse risk content
            source_url="https://sec.gov/risk-factors",
        )
        
        # Retrieve and synthesize for no-trade
        retrieval_results = retrieve("RISKY", top_k=5, engine=db_engine)
        synthesis = synthesize_no_trade_evidence(retrieval_results)
        
        # Should emphasize risks and red flags
        assert synthesis.status == "complete" or synthesis.status == "insufficient_data"


import os  # Import at end for clarity


# Golden test fixtures
GOLDEN_FIXTURES = {
    "AAPL_BULLISH": {
        "ticker": "AAPL",
        "expected_drivers": ["growth", "AI", "margin"],
        "expected_risks": ["competition", "China", "concentration"],
    },
    "MSFT_GROWTH": {
        "ticker": "MSFT",
        "expected_drivers": ["cloud", "AI", "enterprise"],
        "expected_risks": ["competition", "regulation"],
    },
}
