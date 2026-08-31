# Architecture

## The one diagram that matters

Solid lines carry money decisions. Dashed lines carry text. **They never cross.**

```mermaid
flowchart TB
    subgraph INGEST["1 · Ingest"]
        WH["Razorpay webhook<br/>payment.failed / subscription.*"]
        SIG{"HMAC-SHA256<br/>signature valid?"}
        ES[("Event store<br/>append-only")]
        WH --> SIG
        SIG -->|no| REJ["record rejected event · stop"]
        SIG -->|yes| ES
    end

    subgraph DIAGNOSE["2 · Diagnose"]
        CLS["Decline classifier<br/>deterministic lookup"]
        PROP["Propensity table<br/>transparent rules · ADR-0002"]
        ES --> CLS --> PROP
    end

    subgraph DECIDE["3 · Decide — deterministic only"]
        POL["Policy engine<br/>pure functions · named rule ids"]
        STOP{"Stopping rules<br/>hard decline? attempt cap?"}
        GATE{"Risk tier<br/>above HITL threshold?"}
        PROP --> POL --> STOP
        STOP -->|stop| LED
        STOP -->|continue| GATE
        GATE -->|high risk| HITL["Human review queue"]
        HITL --> LED
    end

    subgraph EXECUTE["4 · Execute — bounded + idempotent"]
        MAN{"Mandate permits?<br/>amount · method · expiry"}
        IDEM{"Idempotency key<br/>claimed atomically?"}
        GW["Razorpay SDK<br/>test mode"]
        GATE -->|low risk| MAN
        MAN -->|no| LED
        MAN -->|yes| IDEM
        IDEM -->|already claimed| REPLAY["return recorded result<br/>no gateway call"]
        IDEM -->|claimed| GW
        GW --> LED
        REPLAY --> LED
    end

    LED[("Decision ledger<br/>append-only · hash-chained")]

    subgraph LLM["LLM surface — advisory only · ADR-0001"]
        MCP["Razorpay MCP<br/>READ_ONLY"]
        EXP["Root-cause explainer"]
        MSG["Dunning message drafter"]
        VAL{"Strict schema<br/>valid?"}
        TPL["Deterministic template<br/>fail closed"]
        MCP -.-> EXP
        EXP -.-> VAL
        MSG -.-> VAL
        VAL -.->|no| TPL
    end

    POL -.->|decision already made| EXP
    POL -.->|decision already made| MSG
    VAL -.->|yes| LED
    TPL -.-> LED
    ES -.->|read only| MCP

    LED --> EVAL["Eval harness → EVAL.md"]

    classDef money fill:#0b3d2e,stroke:#12805c,color:#e6fff5
    classDef text fill:#1e1b3a,stroke:#5b4bd6,color:#eae7ff
    class WH,SIG,ES,CLS,PROP,POL,STOP,GATE,HITL,MAN,IDEM,GW,LED,REPLAY,REJ money
    class MCP,EXP,MSG,VAL,TPL text
```

Read the diagram for one property: **there is no path from the dashed subgraph into
any decision node.** The LLM receives decisions that have already been made and
writes prose about them. That is the whole of ADR-0001, and it is checkable by
looking at the picture.

## Why it is shaped this way

**The agent is a state machine with a few LLM steps, not an LLM with tools.** Money
movement is deterministic and replayable from the event store. LangGraph (Phase 4)
is the orchestration shell around that state machine — checkpointing, conditional
branching, and first-class human-in-the-loop pauses — but the policy engine is a
pure function library that is unit-testable, and runnable, without it. If LangGraph
were removed tomorrow, the recovery logic would survive intact. That is deliberate
insurance.

**Four independent controls, not one clever one.** The LLM containment (ADR-0001),
the mandate envelope (ADR-0003), the human gate (ADR-0003) and the idempotency claim
(ADR-0004) each fail independently. No single mistake produces a wrong debit.

**Writes go through the SDK; MCP is read-only.** MCP exists so a model can call
tools. Our model is not allowed to move money, so routing writes through MCP would
contradict the architecture. The official Razorpay MCP server is mounted READ_ONLY
as an analyst surface: the model can read everything and move nothing.

## Component map

| Module | Responsibility | Phase |
| --- | --- | --- |
| `domain/` | typed vocabulary — actions, decline classes, mandates, outcomes | 0 |
| `generator/profiles.py` | subscription population and failure-mix sampling | 1 |
| `generator/outcome_model.py` | the stochastic world — latent probabilities, common random numbers, oracle | 1 |
| `eval/baselines.py` | do-nothing, retry-once, retry-3x, oracle | 1 |
| `eval/metrics.py` | recovery rate, money, false-action cost, safety invariants, bootstrap CIs | 1 |
| `eval/harness.py` | runs a policy against the world, one outcome per record | 1 |
| `eval/report.py` | renders EVAL.md | 1 |
| `ingest/` | signature-verified webhooks, normalisation | 2 |
| `store/` | append-only event store, hash-chained ledger, idempotency claims | 2 |
| `diagnose/classifier.py` | deterministic decline lookup, ambiguity flagged not guessed | 3 |
| `diagnose/propensity.py` | transparent rule table, every score decomposes into named factors | 3 |
| `diagnose/explainer.py` | advisory LLM narration, fails closed to a template | 3 |
| `diagnose/redact.py` | PII redaction on model output, before storage | 3 |
| `policy/` | rules, stopping rules, mandates, HITL queue, executor | 4 |
| `obs/` | JSONL traces (source of truth), optional Langfuse adapter | 5 |

## Data flow through one failed charge

1. `payment.failed` arrives. Signature verified, appended to the event store.
2. The classifier maps the error tuple to a `DeclineClass`. Ambiguous tuples resolve
   to the most likely class and are flagged as low confidence.
3. The propensity table scores recoverability from class, attempt number, method,
   tenure and history — a named rule fires and its id is recorded.
4. Stopping rules run first: hard decline, attempt cap, expired mandate, or closed
   recovery window all terminate here.
5. Surviving decisions are tiered. High risk goes to the review queue; low risk
   proceeds.
6. The mandate is checked, the idempotency key is claimed atomically, and only then
   is the gateway called.
7. The outcome, the rule id, the propensity, the LLM rationale and the action are
   written as one chained ledger row.
