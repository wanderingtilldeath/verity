---
description: Onboard to an unfamiliar codebase with architecture, schema, request tracing, system design interview prep, and FEATURES.md setup
argument-hint: "[optional focus area, e.g., auth, payments, database]"
---

Analyze this repository using the universal codebase onboarding and architecture discovery standard.

### Parameters & Target

- Target: Current workspace directory
- Focus Area: $ARGUMENTS (if provided, dive deep into this specific module or flow)

---

### Execution Protocol

#### Phase 1: Blueprint & Stack Discovery

Do not read every file. Inspect manifest and configuration files:

1. **Language & Runtime**: Identify build files and package manifests (`package.json`, `go.mod`, `Cargo.toml`, `pyproject.toml`, `pom.xml`, `Gemfile`, etc.).
2. **Entry Points**: Locate application bootstrap and input handlers (HTTP server bootstrap, CLI runner, worker daemons, message consumers).
3. **Environment & Infrastructure**: Review `.env.example`, Dockerfiles, docker-compose, orchestration specs, or CI/CD pipelines. Do not read private `.env` files or echo secrets.

#### Phase 2: Architecture & Data Model (How Data Flows)

1. **Topology Style**: Determine if the system is a monolith, modular monolith, microservices, or an event-driven worker/pub-sub pipeline.
2. **Data & Schema**:
    - Locate database definitions (SQL migrations, ORM schemas, NoSQL models).
    - Summarize core entities, relationships, foreign keys, and indexes.
3. **Trace One Core Path**: Follow one representative end-to-end user request and name the real files at each hop:
`Router/Handler` → `Auth/Validation` → `Business Logic/Service` → `Storage/Cache` → `Response`.

#### Phase 3: Production Patterns & System Design Interview Prep

Explain findings in plain, language-neutral technical terms:

- **Caching**: Where is temporary data stored (Redis, in-memory)? What is the invalidation strategy?
- **Statelessness & Scaling**: Can multiple app server instances run behind a load balancer? Where does shared state live?
- **Resilience**: Look for retries, timeouts, circuit breakers, rate limiters, fallback responses, or dead-letter queues.
- **System Design Interview Prep**:
    - State the classic interview question this system represents (e.g., *"Design an idempotency-backed payment engine"*).
    - Analyze real-world trade-offs: what was prioritized (consistency vs. availability, simplicity vs. horizontal scale) and where this design breaks under 10x traffic.
    - Provide 3 targeted interview Q&As based on actual files and implementations in this repo.

#### Phase 4: Bootstrap FEATURES.md

1. Check if `FEATURES.md` exists in the project root. If not, create it.
2. Seed it with 5 to 15 core active subsystems using the standard ledger template:

```markdown
### [Feature / Subsystem Name]
- **Status**: Active
- **Last Updated**: YYYY-MM-DD (git commit hash if available)
- **Entry Points / Key Files**:
  - `path/to/entry.ext`: router or handler
  - `path/to/service.ext`: business logic
  - `path/to/schema.ext`: data model
- **Implementation Summary**: Plain-language description of how it works internally (2 to 5 sentences).
- **Data & Schema Dependencies**: Tables, collections, external APIs, queues, or caches touched.
- **Production Trade-Offs**: Known edge cases, locking/idempotency rules, caching behavior, or scaling limits.
```
