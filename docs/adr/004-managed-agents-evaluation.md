# ADR 004 — Managed Agents evaluation

## Status

Accepted evaluation; no implementation planned in this phase.

## Decision

Use coded Google ADK agents for Forseti's workflow. Managed Agents in the
Gemini API were evaluated as an alternative, but they do not provide a better
fit for the current requirement to keep deterministic tools, risk math, and
trace persistence inside the application boundary.

Managed Agents may be reconsidered when the deployment and tenancy model needs
managed lifecycle features. This ADR deliberately does not add a dependency or
runtime path for them.