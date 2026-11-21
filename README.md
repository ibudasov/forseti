```text
     ᛟ  ═══════════════════════════════════════════════════════════════  ᛟ
    ╱│╲                                                                 ╱│╲
   ╱ │ ╲     ███████╗ ██████╗ ██████╗ ███████╗███████╗████████╗██╗     ╱ │ ╲
  ╱  │  ╲    ██╔════╝██╔═══██╗██╔══██╗██╔════╝██╔════╝╚══██╔══╝██║    ╱  │  ╲
 ▕───┼───▏   █████╗  ██║   ██║██████╔╝███████╗█████╗     ██║   ██║   ▕───┼───▏
  ╲  │  ╱    ██╔══╝  ██║   ██║██╔══██╗╚════██║██╔══╝     ██║   ██║    ╲  │  ╱
   ╲ │ ╱     ██║     ╚██████╔╝██║  ██║███████║███████╗   ██║   ██║     ╲ │ ╱
    ╲│╱      ╚═╝      ╚═════╝ ╚═╝  ╚═╝╚══════╝╚══════╝   ╚═╝   ╚═╝      ╲│╱
     ᛟ  ═════  Norse God of Justice · Deterministic Trade Verdict  ═════  ᛟ
```

[![CI Tests](https://github.com/ibudasov/forseti/actions/workflows/test.yml/badge.svg)](https://github.com/ibudasov/forseti/actions/workflows/test.yml)
[![Coverage Badge](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/ibudasov/c379641f823db1522632b363883f81f4/raw/coverage.json)](https://github.com/ibudasov/forseti/actions/workflows/test.yml)

## Goal

**Forseti** is an AI-assisted decision-support system for swing-trade research on US equities across high-conviction themes (AI, neoclouds, defence, nuclear, green energy, quantum, robotics, and space). Given a ticker, it evaluates market, technical, fundamental, and earnings data to issue a **Trade**, **Watchlist**, or **No Trade** verdict backed by deterministic risk math (entry range, stop-loss, take-profit, and position sizing) and an evidence-grounded explanation — it is not an autonomous trading bot.

A deterministic rules and risk engine is authoritative in every mode: optional AI specialists (fundamental, technical, synthesizer, and critic) can contribute evidence or downgrade a verdict, but can never inflate scores or alter risk numbers.

## Decision-Making Flow

```mermaid
flowchart TD
    A["Ticker Request (POST /analyze)"] --> B["1. Data Gate<br/>Freshness & completeness checks"]
    B --> C{"2. Hard Vetoes<br/>VIX > 30 · RSI > 70<br/>Earnings ≤ 7d · Deep < SMA200"}
    C -- "Veto triggered" --> NT["No Trade"]
    C -- "Clear" --> D["3. Checklist Scoring (0–11 pts)<br/>Fundamentals (6) + Technicals & VIX (5)"]
    D --> E{"4. Decision Thresholds"}
    E -- "Score ≥ 8" --> T["Trade"]
    E -- "Score 5–7" --> W["Watchlist"]
    E -- "Score ≤ 4" --> NT
    T --> F["5. Deterministic Risk Math<br/>Entry · Stop-Loss · Take-Profit · Size (R:R ≥ 1.5)"]
    F --> G["6. Confidence Score<br/>clamp(score / 11 − 0.05 × warnings)"]
    W --> G
    NT --> G
    G --> H{"PIPELINE_MODE = agentic?"}
    H -- "linear (default)" --> I["Persist Recommendation & Return"]

    subgraph AG["ADK SequentialAgent: trade_analyst_supervisor (no peer transfers; state-key handoff)"]
        direction TB
        PREP["Deterministic Tools & RAG<br/>structured_data_collector · retriever · rules_engine · risk_manager"]
        FA["Fundamental Analyst<br/>Evidence + score adj [-2, 0]"]
        TA["Technical Analyst<br/>Trend & momentum (downgrade-only)"]
        DS["Decision Synthesizer<br/>Cited memo (risk math locked)"]
        CR["Critic / Guardrail<br/>Checks contradictions & hard rules<br/>(downgrade-only / force no_trade)"]

        PREP --> FA
        FA -- "writes state: fundamental_view" --> TA
        TA -- "reads fundamental_view<br/>writes state: technical_view" --> DS
        FA -. "fundamental_view" .-> DS
        DS -- "reads both views<br/>writes state: draft_recommendation" --> CR
        FA -. "fundamental_view" .-> CR
        TA -. "technical_view" .-> CR
    end

    H -- "agentic" --> PREP
    CR -- "writes state: critique" --> I
```
