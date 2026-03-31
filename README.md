# Dynamic Prompt Generator

A two-prompt SEO content generation system for pdfFiller. Given a keyword and URL, it runs a strategist prompt (blueprint) followed by a writer prompt (HTML article) via an LLM API.

Built on the **WAT framework** (Workflows, Agents, Tools): workflows define SOPs, tools handle deterministic execution, and an AI agent coordinates the two.

---

## Setup

**Install dependencies:**
```bash
pip install -r requirements.txt
```

**Configure `.env`:**
```
LITELLM_PROXY_URL=https://your-litellm-proxy
LITELLM_API_KEY=sk-...
```

---

## Usage

### Batch generation (primary workflow)

```bash
# All pages, one model
python tools/batch_generate.py --models openai/gpt-4o-mini

# Filter by cluster
python tools/batch_generate.py --models openai/gpt-4o-mini --cluster group_annotate

# Multi-model comparison
python tools/batch_generate.py --models openai/gpt-4o-mini,anthropic/claude-3-5-sonnet-20241022 --cluster group_annotate

# With fallback: if primary model fails all retries, automatically try gpt-4o-mini
python tools/batch_generate.py --models anthropic/claude-3-5-sonnet-20241022 --fallback-models openai/gpt-4o-mini

# Resumable run (stable filename + progress file)
python tools/batch_generate.py --models openai/gpt-4o-mini --run-label my_run
```

Safe to interrupt (Ctrl+C). Completed pages are marked `done` and skipped on resume.

**Flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--models` | `openai/gpt-4o-mini` | Comma-separated provider-prefixed model names |
| `--fallback-models` | *(none)* | Comma-separated fallback models tried if the primary exhausts all retries |
| `--cluster` | *(all)* | Filter to a single cluster |
| `--limit` | *(all)* | Cap number of pages (useful for testing) |
| `--temperature` | `0.3` | LLM sampling temperature |
| `--timeout` | *(none)* | Per-model timeout in seconds |
| `--run-label` | *(none)* | Stable label for CSV and progress filenames |

### Single page

```bash
python tools/generate_page.py \
  --keyword "Annotate PDF Online" \
  --url "https://www.pdffiller.com/en/functionality/annotate-pdf"

# With fallback model
python tools/generate_page.py \
  --keyword "Annotate PDF Online" \
  --url "https://www.pdffiller.com/en/functionality/annotate-pdf" \
  --model anthropic/claude-3-5-sonnet-20241022 \
  --fallback-models openai/gpt-4o-mini
```

Output saved to `output/{url-slug}/blueprint.md` and `output/{url-slug}/content.html`.

### Validate config

```bash
python tools/load_config.py --cluster group_annotate --test
```

---

## File Structure

```
inputs/
├── global_config.csv       # Website name, language, audience, positioning
├── cluster_config.csv      # Per-cluster word count, context, section menu
├── page_config.csv         # Pages to generate: cluster, URL, keyword
├── prompt_1.md             # Strategist prompt template
└── prompt_2.md             # Writer prompt template

output/                     # Runtime outputs (not committed)
├── progress.csv            # Per-URL status: pending | done | error
├── batch_{timestamp}.csv   # Batch results: blueprint + HTML per model
└── {url-slug}/             # Single-page mode outputs

tools/
├── batch_generate.py       # Batch runner with progress tracking
├── generate_page.py        # Single-page orchestrator
└── load_config.py          # Config loader and validator

workflows/
├── generate_pages.md       # Main SOP for running the pipeline
└── compare_models.md       # SOP for multi-model evaluation
```

---

## How It Works

1. `load_config.py` resolves template variables from the three config CSVs
2. Prompt 1 (Strategist) generates a keyword intelligence report and structured page blueprint
3. Prompt 2 (Writer) uses the blueprint to produce semantic HTML (no classes, IDs, divs, or inline styles)
4. Results are written to a CSV after each page; progress is tracked for resumable runs

Errors are logged per-cell in the output CSV and retried automatically on the next run. If a fallback chain is configured (`--fallback-models`), each fallback is tried before the page is marked as failed.
