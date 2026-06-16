## Structured Design Review

**Document**: “Hermes + Pi Memory System — Design Plan” (Jenny, 2026-06-16)
**Context**: Single-user local memory for Hermes & Pi Coding Agent

### 1. Architectural Soundness
**Rating: Good, with one structural gap**

The 4-tier recall flow (injected context → hybrid search → reranker → cited answer) faithfully mirrors the composite pattern from the source video and is architecturally sound for a local agent system. Tier 0 (injected memory) is correctly the cheapest, fastest path; Tier 1–2 vector + cross-encoder rerank is a well‑proven stack.

**Gap**: The plan delays embedding until a daily batch (“batch‑embedded once per day by the cron evaluator,” §4.5). This means any memory captured during the day is **invisible to semantic recall until the next cron run**. For a conversational agent that might refer back to something said 30 minutes ago, that is a critical flaw. The batch strategy was introduced to avoid “re‑embedding every single turn”, but embedding is a cheap local operation (~20 ms on CPU for BGE‑small). There is no reason not to embed immediately after summarization. *Confidence: High.*

**Other notes**:
- The shared store uses a single SQLite file with concurrent writers (Hermes sidecar, Pi extension, cron evaluator). The plan does not address WAL mode, retry logic, or write contention, which **will** cause `SQLITE_BUSY` under real concurrent use. *Confidence: Medium.*
- The “cron evaluator” spot‑check loop (§5.2) is a creative mitigation for stale‑entry blindness, but its reliance on an LLM for every evaluation cycle could become expensive and slow if run often; the document does not specify a frequency. *Confidence: Low.*

### 2. Build Order
**Sequence is logical, but one dependency is under‑explored**

Phases 1 → 2 → 3 → 4 → 5 are correct for incremental delivery. However, **Phase 1 “Foundation”** says it will “build the shared memory CLI + local sqlite‑vec store + manual auto‑capture demo”. That demo will need a working Hermes integration (sidecar or hook) to actually capture turns. The plan identifies that Hermes lacks a native post‑turn hook and proposes a sidecar reading `state.db`. If that sidecar proves unreliable, the entire capture pipeline for Hermes is at risk. This should be a **primary deliverable of Phase 1**, not just a demo, because Phase 2 depends on it.

Also, Phase 4 cross‑agent coherence could be started alongside Phase 3 (Pi integration) as soon as the shared store exists, since dedup logic is purely at the database layer and does not require agent‑specific recall features.

### 3. Risk Assessment
The document lists four risks. It **underestimates** or misses the following:

| Risk | Likelihood / Impact | Missing from plan | Confidence |
|------|---------------------|-------------------|------------|
| **Day‑old recall gap** (batched embedding) | High / **High** – semantic recall silently returns nothing for hours. | Immediate embedding (see above). | High |
| **SQLite concurrency** under real multi‑process load | Medium / High – writes fail, capture is lost, agents see stale data. | WAL mode, write‑ahead logging, or a central writer process. | High |
| **Remote summarization API** downtime (OpenCode Go) | Low-Medium / Medium – capture halts entirely. | Fallback local summarizer or queue‑and‑