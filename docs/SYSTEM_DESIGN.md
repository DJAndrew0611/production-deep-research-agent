# System Design: Scaling the Deep Research Agent

## Current Architecture (Single-User, Single-Machine)

```
User → Streamlit UI → ResearchWorkflow (sync pipeline) → SQLite WAL
                            ↓
              Research Agent → Elaboration Agent → Verification Agent
                            ↓
                     Firecrawl API / LLM API
```

**Limitations**: Single-threaded, no concurrent users, no horizontal scaling.

## Production Architecture (Multi-User, Distributed)

```
                    ┌─────────────────┐
                    │   Load Balancer  │
                    │   (nginx / GCP)  │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────▼──┐   ┌──────▼─────┐   ┌───▼────────┐
     │ API Server │   │ API Server │   │ API Server  │
     │ (FastAPI)  │   │ (FastAPI)  │   │ (FastAPI)   │
     └────────┬──┘   └──────┬─────┘   └───┬────────┘
              │              │              │
              └──────────────┼──────────────┘
                             │
                    ┌────────▼────────┐
                    │   Task Queue     │
                    │ (Celery + Redis) │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────▼──────┐ ┌────▼────────┐ ┌──▼──────────┐
     │ Research       │ │ Elaboration│ │ Verification │
     │ Worker Pool    │ │ Worker Pool│ │ Worker Pool  │
     └────────┬──────┘ └────┬────────┘ └──▼──────────┘
              │              │              │
              └──────────────┼──────────────┘
                             │
              ┌──────────────┼──────────────┐
              │                         │
     ┌────────▼──────┐      ┌──────────▼─────┐
     │ PostgreSQL     │      │ Vector DB       │
     │ (Claims/State) │      │ (Embeddings)    │
     └───────────────┘      └────────────────┘
```

### Key Design Decisions

| Decision | Current | Production | Rationale |
|:---------|:--------|:-----------|:----------|
| API Layer | Streamlit (sync) | FastAPI (async) | Supports WebSocket for streaming, OpenAPI docs, horizontal scaling |
| Task Execution | Synchronous pipeline | Celery workers | Decouple request handling from long-running research tasks |
| Storage | SQLite WAL | PostgreSQL + pgvector | Multi-writer concurrency, vector search, replication |
| Caching | None | Redis | Session state, rate limiting, LLM response caching |
| Embeddings | In-memory numpy | pgvector / Qdrant | Persistent semantic search at scale |
| Observability | RunTelemetry (memory) | OpenTelemetry → Grafana | Distributed tracing, alerting, SLO monitoring |

### Scaling Considerations

1. **LLM API Rate Limiting**: Token bucket per user, shared circuit breaker across workers
2. **Research Task Queuing**: Priority queue (paid users > free), estimated completion time
3. **Evidence Deduplication**: Content-hash based dedup across sessions to reduce search costs
4. **Cost Control**: Per-user daily budget caps, model tier routing (fast model for elaboration, strong model for verification)

### Data Consistency

The Claim-Evidence model is naturally suited for **eventual consistency**:
- Claims are immutable once created (append-only ledger)
- Status transitions are monotonic (unverified → disputed → corroborated, never backwards without explicit rollback)
- Rollback creates new revisions, preserving full audit trail

### Security at Scale

- **Multi-tenancy**: Row-level security on PostgreSQL, session isolation
- **Rate limiting**: Per-IP and per-API-key limits at the API gateway
- **Prompt injection**: Current scan_for_prompt_injection runs before every LLM call — would add a dedicated guardrail model (e.g., Llama Guard) for production
