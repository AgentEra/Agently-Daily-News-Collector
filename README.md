# Agently Daily News Collector v4

Agently Daily News Collector has been rewritten on top of **Agently v4** and now uses:

- `TriggerFlow` for the end-to-end pipeline
- Agently v4 built-in `Search` and `Browse` tools
- structured output contracts instead of the old v3 workflow API

> Version constraint: this project requires **Agently v4.0.8.2 or newer**. Earlier v4 releases may not be compatible with the current `TriggerFlow` runtime resources, flow config import/export, and related runtime APIs used here.

The previous Agently v3 project has been archived under [`./v3`](./v3).

## Features

- Input a topic and generate a multi-column news briefing automatically
- Search, shortlist, browse, summarize, and assemble stories in one flow
- Save the final report as Markdown under `./outputs`
- Keep prompt templates in `./prompts` for easy editing
- Keep an independent `./tools` layer so search/browse can be replaced without touching the main workflow
- Keep flow construction in `./workflow` so orchestration can evolve independently from collector logic

## Quick Start

1. Install dependencies:

```bash
pip install -r requirements.txt
```

If you install Agently manually, make sure you use at least:

```bash
pip install "agently>=4.0.8.2"
```

2. Edit [`SETTINGS.yaml`](./SETTINGS.yaml):

- Keep the model block as environment placeholders
- Export the required environment variables:

```bash
export AGENTLY_NEWS_BASE_URL="https://api.openai.com/v1"
export AGENTLY_NEWS_MODEL="gpt-4.1-mini"
export AGENTLY_NEWS_API_KEY="your_api_key"
```

- Or put them in a local `.env` file:

```dotenv
AGENTLY_NEWS_BASE_URL=https://api.openai.com/v1
AGENTLY_NEWS_MODEL=gpt-4.1-mini
AGENTLY_NEWS_API_KEY=your_api_key
```

- Adjust language / search / concurrency settings if needed
- If your OpenAI-compatible endpoint does not require authentication, you can leave `AGENTLY_NEWS_API_KEY` unset and the project will skip `auth`.

3. Run:

```bash
python app.py
```

Or pass a topic directly:

```bash
python app.py "AI agents"
```

## Project Structure

```text
.
├── app.py
├── news_collector/
├── tools/
├── workflow/
├── prompts/
├── outputs/
├── logs/
└── v3/
```

## Important v3 -> v4 Changes

The business chain is still roughly:

`outline -> search -> pick -> browse + summarize -> write column -> render markdown`

What changed is the engineering shape around that chain.

### Project-level changes

- The old v3 project used a main workflow plus a nested column workflow under `./workflows`, with custom `search.py` / `browse.py` helpers and storage-style state passing.
- The v4 project separates responsibilities more clearly:
  - `news_collector/`: app/integration layer
  - `workflow/`: TriggerFlow definition and concrete chunk logic
  - `tools/`: search/browse adapter layer
  - `prompts/`: structured prompt contracts
- Model configuration is no longer hardcoded in Python. It now uses `${ENV.xxx}` placeholders from `SETTINGS.yaml`, so deployment and local switching are simpler.
- Tool wiring is no longer buried inside workflow code. Search, browse, and logger are injected as TriggerFlow runtime resources, which makes the workflow easier to replace or test.

### Agently v4 features used here

- **TriggerFlow orchestration**
  - Replaces the old v3 workflow style with a more explicit flow graph (`to`, `for_each`, branching-ready composition).
  - Unlike the old v3 Workflow chain, TriggerFlow here runs columns concurrently and also summarizes picked stories concurrently within each column.
  - Meaning for this project: the end-to-end news pipeline is easier to inspect, evolve, and split into chunks without mixing orchestration with business logic, while total runtime is often reduced from a long serial chain to tens of seconds when model/search/browse latency is stable.
- **Structured output contracts**
  - YAML prompts now define output schema directly for outline generation, news picking, summarizing, and column writing.
  - Meaning for this project: much less handwritten parsing glue, clearer interfaces between steps, and easier prompt iteration.
- **Built-in Search / Browse tools**
  - The project now defaults to Agently v4 built-in tool implementations instead of the old project-local helpers.
  - Meaning for this project: less custom infrastructure code, and users can still swap implementations through `./tools` without rewriting the workflow.
- **Runtime resources and state namespaces**
  - TriggerFlow runtime resources are used to inject logger/search/browse dependencies, while runtime state stores execution data such as request, outline, and intermediate results.
  - Meaning for this project: dependency wiring and execution state are separated cleanly, which keeps chunk code thinner and more maintainable.
- **Environment-aware settings**
  - Agently v4 `set_settings(..., auto_load_env=True)` works directly with `${ENV.xxx}` placeholders.
  - Meaning for this project: model endpoint, model name, and API key can be switched by environment instead of editing code or committing secrets.

### Overall effect on this project

- The core product behavior remains familiar to v3 users, but the project now has a cleaner app/workflow/tools/prompts split.
- More logic is expressed in Agently-native capabilities instead of project-specific glue code.
- True concurrency is now part of the default execution model. The v3 version was effectively serial, while the v4 version can process columns and per-column summaries in parallel through TriggerFlow.
- Replacing tools, adjusting prompts, or evolving workflow steps is now lower-risk than in the old v3 layout.

## Notes

- Python `>=3.10` is required because Agently v4 requires it.
- This project requires Agently `>=4.0.8.2`.
- Model settings now use Agently v4 `auto_load_env=True` with `${ENV.xxx}` placeholders.
- `tools/` defaults to Agently v4 built-in implementations, but you can replace the factories there with your own tools.
- `workflow/` now contains both the flow definition and the concrete chunk implementations.
- `news_collector/` acts as the app/integration layer for configuration, model wiring, and CLI entry support.
- The current sample [`SETTINGS.yaml`](./SETTINGS.yaml) enables `BROWSE.enable_playwright: true` by default because many news pages need a real browser to return usable content.
- If you do not want to install Playwright, set `BROWSE.enable_playwright` to `false` manually, but expect weaker browse quality on dynamic or protected sites.
- The settings loader keeps basic compatibility with the old v3 keys such as `MODEL_PROVIDER`, `MODEL_URL`, `MODEL_AUTH`, `MODEL_OPTIONS`, `MAX_COLUMN_NUM`, and `USE_CUSTOMIZE_OUTLINE`.
