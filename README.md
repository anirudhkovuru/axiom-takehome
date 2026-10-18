# Axiom Workbench

Axiom Workbench is an agentic scientific analysis workspace for Axiom's multimodal experimental drug data. Scientists ask questions in plain language; the agent inspects the dataset, runs read-only SQL and controlled Python, streams its progress, and returns evidence-backed answers with downloadable code, tables, and figures.

The application runs with a live OpenAI model when `OPENAI_API_KEY` is configured. Without a key it enters a clearly labeled local demo mode that executes deterministic versions of the four flagship workflows against the real DuckDB database.

## Product capabilities

- Natural-language analysis over DuckDB
- Visible analysis plans and tool execution
- Read-only SQL with persisted query artifacts
- Controlled Python for statistics, clustering, and figures
- Streaming progress and answer text over server-sent events
- Durable chats, messages, runs, steps, and artifacts
- Saved experiments with reproducibility-bundle export
- Dataset-aware QC, coverage, and interpretation rules

## Quick start

Requirements: Node.js 22+, Python 3.11+, and `curl`.

```bash
npm install
npm run setup:python
npm run download:data
cp .env.example .env
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The API runs on [http://127.0.0.1:8000](http://127.0.0.1:8000).

To enable the live agent, set the following in `.env`:

```bash
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-6-astra
```

No key is required to explore the interface and run the built-in analysis recipes.

## Suggested demo

1. Start with “Create a scatterplot of bioactivity versus cell viability.”
2. Watch the plan, dataset inspection, and Python analysis stream into the run trace.
3. Open the generated figure, CSV, and Python source in the artifact panel.
4. Ask a follow-up question in the same chat.
5. Save the chat as an experiment.
6. Export the experiment ZIP and inspect `report.md`, `manifest.json`, and its artifacts.

Other strong prompts:

- “Find consistent phenotypic clusters of compounds using morphology profiles.”
- “What is the strongest supported mechanism of toxicity for compound 1?”
- “Compare compound 4 and compound 18. Why might their toxicity profiles differ?”

## Architecture

```text
Next.js workspace
    │ POST message
    │ GET event stream
    ▼
FastAPI orchestration service
    ├── OpenAI Responses tool loop
    ├── read-only DuckDB queries
    ├── controlled Python worker
    ├── SQLite application state
    └── local artifact store
```

The backend owns orchestration and credentials. The browser never receives the OpenAI key and never connects directly to DuckDB. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the detailed streaming, persistence, experiment, safety, and scaling design.

## Repository map

```text
app/                    Next.js scientific workspace
backend/app/            FastAPI API, agent, tools, and persistence
backend/tests/          Data, persistence, and end-to-end demo tests
docs/                   Architecture and interview notes
scripts/download-data.sh  Reproducible dataset download
.context/               Local SQLite, artifacts, and development data (ignored)
```

## Validation

```bash
npm test
```

This builds the frontend, verifies server-rendered output, and runs backend tests against the take-home dataset.

## Intentional MVP boundaries

- Single-user local workspace; no authentication or team permissions
- One local analysis worker; production would dispatch jobs to isolated containers
- Application-owned conversation history; no dependency on provider-side conversation storage
- No external literature retrieval or chemical identity enrichment
- Scientific interpretation is evidence-oriented and explicitly non-causal

## Production evolution

The API and worker are separated conceptually even though they share one local process. For production, the run executor can move to Modal or another isolated job system, SQLite to Postgres, artifacts to object storage, and the event log to a durable queue. The frontend and event contract do not need to change.
