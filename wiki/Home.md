# Forseti wiki

**Forseti** is an AI decision-support system for swing-trade research on US equities (AI, neoclouds, defence, nuclear, green energy, quantum, robotics, space). It returns **Trade / Watchlist / No trade** with deterministic risk math (entry, stop-loss, take-profit, position size) and an evidence-grounded explanation. It is not an autonomous trading bot.

## Start here

- [Roadmap](Roadmap): project goal, design principles, architecture, tech stack and the week-by-week plan
- [Trading Framework](Trading-Framework): the trading rules and checklist the engine encodes
- [VIX & Panic Regime](VIX-and-Panic-Regime): what VIX is and why `VIX > 30` gates the entire universe to `no_trade`
- [Coding Agent Clean Code Instructions](Coding-Agent-Clean-Code-Instructions): binding code style for all contributors and coding agents

## Execution plans

| Phase | Page |
| --- | --- |
| Weeks 1–2: Service foundation | [GET /ticker/{symbol}](Execution-Plan-GET-ticker-symbol) |
| Weeks 3–4: Structured data + rules engine | [Structured Data Ingestion](Execution-Plan-Structured-Data-Ingestion) · [Evaluation Engine](Execution-Plan-Evaluation-Engine) |
| Weeks 5–6: RAG + vector database | [RAG + Vector Database](Execution-Plan-RAG-and-Vector-Database) |
| Weeks 7–8: Agentic workflow | [Agentic Workflow with ADK](Execution-Plan-Agentic-Workflow-with-ADK) |
| Week 9: Trust and hardening | [Prove the Agent Workflow Is Trustworthy](Execution-Plan-Prove-the-Agent-Workflow-Is-Trustworthy) · [Technical Analyst Hardening](Execution-Plan-Technical-Analyst-Hardening) · [Agent-Layer Loose Ends](Execution-Plan-Agent-Layer-Loose-Ends) |
| Week 10: GCP deployment | [Week 10: GCP Deployment Decisions](Week-10-GCP-Deployment-Decisions) |

## Other

- [Parent session prompt template](Parent-session-prompt-template)
- Code: <https://github.com/ibudasov/forseti> · Issues: <https://github.com/ibudasov/forseti/issues>
