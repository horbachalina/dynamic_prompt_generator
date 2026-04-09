# Dynamic Prompt Generator

A two-prompt SEO content generation pipeline built on the **WAT framework** (Workflows, Agents, Tools). Given an H1 and URL, it runs a strategist prompt to produce a structured page blueprint, then a writer prompt to produce a semantic HTML article — all via a LiteLLM proxy. Supports multiple locales (en, fr, de, es, pt-BR, nl, it) with per-market tone rules.

---

## Architecture

The system is organized into three layers:

| Layer | Directory | Role |
|-------|-----------|------|
| **Workflows** | `workflows/` | Markdown SOPs defining objectives, steps, and edge cases |
| **Agent** | Claude Code (you) | Orchestration: reads workflows, calls tools, handles failures |
| **Tools** | `tools/` | Deterministic Python scripts for execution |

**Pipeline overview:**

```
h1 + URL + locale
    │
    ▼
[Prompt 1 — Strategist]
    Keyword intelligence + structured page blueprint
    │
    ▼  (blueprint extracted from <blueprint>...</blueprint>)
[Prompt 2 — Writer]
    Semantic HTML article (no divs, no classes, no inline styles)
    │
    ▼
output/
```

All LLM calls are routed through a LiteLLM proxy, which lets you switch between OpenAI, Anthropic, Google, or any other supported provider without changing the pipeline code.

---

## Setup

**1. Install dependencies:**
```bash
pip install -r requirements.txt
```

**2. Configure `.env`:**
```
LITELLM_PROXY_URL=https://your-litellm-proxy
LITELLM_API_KEY=sk-...
```

**3. Verify config loads correctly:**
```bash
python tools/load_config.py --cluster group_annotate --test
```

---

## Usage

### Batch generation (primary workflow)

```bash
# All pages, one model
python tools/batch_generate.py --models openai/gpt-4o-mini

# Filter to a single cluster
python tools/batch_generate.py --models openai/gpt-4o-mini --cluster group_annotate

# Limit pages (useful for testing before a full run)
python tools/batch_generate.py --models openai/gpt-4o-mini --cluster group_annotate --limit 3

# Use a fallback model: if the primary exhausts all retries, automatically try the fallback
python tools/batch_generate.py --models anthropic/claude-3-5-sonnet-20241022 --fallback-models openai/gpt-4o-mini

# Named run: stable CSV filename + separate progress file (enables clean resume)
python tools/batch_generate.py --models openai/gpt-4o-mini --run-label my_run
```

Safe to interrupt with Ctrl+C. Completed pages are marked `done` in `progress.csv` and skipped on the next run.

**All flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--models` | `openai/gpt-4o-mini` | Comma-separated provider-prefixed model names |
| `--fallback-models` | *(none)* | Fallback models tried in order if the primary exhausts all retries. Cannot be combined with multi-model `--models A,B`. |
| `--locale` | `en` | Language/market for this run. Filters `page_config.csv` by `locale` column and injects tone-of-voice rules. |
| `--cluster` | *(all)* | Filter to a single cluster |
| `--limit` | *(all)* | Cap number of pages processed in this run |
| `--temperature` | `0.3` | LLM sampling temperature |
| `--timeout` | `180` | Per-model call timeout in seconds |
| `--run-label` | *(none)* | Stable label for CSV and progress filenames (enables deterministic resume) |

### Single-page generation

Generates one page and saves outputs to disk. Useful for spot-checking or re-running a specific URL.

```bash
python tools/generate_page.py \
  --keyword "Annotate PDF Online" \
  --url "https://www.pdffiller.com/en/functionality/annotate-pdf"
```

With a fallback model:
```bash
python tools/generate_page.py \
  --keyword "Annotate PDF Online" \
  --url "https://www.pdffiller.com/en/functionality/annotate-pdf" \
  --model anthropic/claude-3-5-sonnet-20241022 \
  --fallback-models openai/gpt-4o-mini
```

Output: `output/{url-slug}/blueprint.md` and `output/{url-slug}/content.html`.

Note: single-page runs do not update `progress.csv`. To retry a failed page via the batch pipeline, re-run the batch — it automatically retries all `error` entries.

### Multi-model comparison

Run the same pages through multiple models side-by-side. Produces a CSV with two columns per model (`{slug}_blueprint`, `{slug}_content`) for direct comparison.

```bash
python tools/batch_generate.py \
  --models openai/gpt-4o-mini,anthropic/claude-3-5-sonnet-20241022 \
  --cluster group_annotate \
  --limit 3
```

When evaluating output, compare across models:
- Heading quality and keyword placement
- Section coverage and match to the blueprint
- Word count and content depth
- Tone consistency
- Brand mention density (target: 5–6 per article)

### Validate config

```bash
python tools/load_config.py --cluster group_annotate --test
```

---

## File Structure

```
inputs/
├── global_config.csv       # Website name, language, target audience, positioning statement
├── locale_config.csv       # Per-locale: language and tone-of-voice rules
├── cluster_config.csv      # Per-cluster: context, word count target, section menu
├── page_config.csv         # Pages to generate: locale, cluster, URL, h1
├── prompt_1.md             # Strategist prompt template (produces <blueprint>)
└── prompt_2.md             # Writer prompt template (produces HTML article)

output/                     # Runtime outputs — not committed, regenerated as needed
├── progress.csv            # Per-URL status: pending | done | error
├── batch_{timestamp}.csv   # Batch results: blueprint + HTML per model
└── {url-slug}/             # Single-page mode outputs
    ├── blueprint.md
    └── content.html

tools/
├── batch_generate.py       # Batch runner with progress tracking and CSV output
├── generate_page.py        # Single-page orchestrator (also used internally by batch)
└── load_config.py          # Config loader and validator

workflows/
├── generate_pages.md       # Main SOP: running the full pipeline
└── compare_models.md       # SOP: multi-model evaluation
```

**What goes where:**
- `inputs/` — permanent inputs (prompt templates, config CSVs). Do not delete.
- `output/` — disposable runtime outputs. Regenerated on every run.
- `.env` — API keys. Never stored anywhere else, never committed.

---

## Config System

Template variables are resolved from three CSVs:

| Scope | File | Changes when |
|-------|------|--------------|
| **Global** | `global_config.csv` | Switching to a different website |
| **Locale** | `locale_config.csv` | Switching language/market |
| **Cluster** | `cluster_config.csv` | Switching to a different content cluster |
| **Page** | `page_config.csv` | Every page |

`load_config.py` merges these into template variables injected into the prompts:

| Variable | Source | Contains |
|----------|--------|---------|
| `{{GLOBAL_CONFIG}}` | `global_config.csv` | JSON: website, language, audience, positioning |
| `{{LOCALE_CONFIG}}` | `locale_config.csv` | Language name and tone-of-voice rules for the target locale |
| `{{CLUSTER_CONFIG}}` | `cluster_config.csv` | JSON: cluster context, target word count |
| `{{SECTION_MENU}}` | `cluster_config.csv` | Full section definitions block |
| `{{PAGE_CONFIG}}` | `page_config.csv` | JSON: h1 and URL |
| `{{BLUEPRINT}}` | Prompt 1 output | Extracted from `<blueprint>...</blueprint>` |

### Adding a new cluster

1. Add a row to `inputs/cluster_config.csv`: `cluster`, `target_word_count`, `cluster_context`, `section_menu`
2. Add the cluster's pages to `inputs/page_config.csv`: `locale`, `cluster`, `url`, `h1`
3. Validate: `python tools/load_config.py --cluster {new_cluster} --test`
4. Run: `python tools/batch_generate.py --models openai/gpt-4o-mini --cluster {new_cluster}`

New URLs are picked up automatically as `pending` on first run.

---

## Model Selection

All calls route through the LiteLLM proxy. Use provider-prefixed names:

| Provider | Example |
|----------|---------|
| OpenAI | `openai/gpt-4o-mini` (default) |
| Anthropic | `anthropic/claude-3-5-sonnet-20241022` |
| Google | `gemini/gemini-1.5-pro` |

Model-specific quirks (`max_completion_tokens` vs `max_tokens`, models that don't support `temperature`) are detected and handled automatically on first call — no manual tuning needed when switching providers.

---

## Error Handling

| Error | Behavior |
|-------|----------|
| Rate limit / connection error / timeout | Retried up to 3 times with exponential backoff (5s → 15s → 45s) |
| All retries exhausted (primary model) | If `--fallback-models` is set, each fallback is tried in order (also with 3 retries each) |
| All models exhausted | Page marked `error` in `progress.csv`; error message logged in CSV |
| No `<blueprint>` block in Prompt 1 | Batch: raw Prompt 1 response written to `_blueprint` CSV cell. Single-page: saved to `output/{slug}/blueprint_raw.txt` |
| Malformed Prompt 2 output (no `<h2>`) | Page marked `error`; check the prompt or reduce temperature |
| Re-run after errors | Re-run the same command — batch automatically retries all `error` pages |

When a fallback fires, the batch output shows which model was actually used:
```
✓ claude-3-5-sonnet → ~1342 words (fallback: gpt-4o-mini)
```

In multi-model runs with fallbacks, the CSV includes a `{slug}_model_used` column.

---

## How It Works

1. **Config resolution** — `load_config.py` reads the three CSVs and assembles template variables
2. **Prompt 1 (Strategist)** — LLM receives keyword, URL, global config, cluster context, and section menu; produces a keyword intelligence report and structured `<blueprint>`
3. **Blueprint extraction** — pipeline extracts content between `<blueprint>...</blueprint>` tags
4. **Prompt 2 (Writer)** — LLM receives the blueprint and global config; produces a semantic HTML article
5. **Output** — results written to CSV (batch) or files (single-page); progress tracked in `progress.csv`

The batch runner caches config and prompt templates per cluster to avoid redundant file reads across pages.

---

## Localization

The pipeline supports multiple locales. Each locale maps to a language and tone-of-voice rules stored in `inputs/locale_config.csv`.

| Locale | Language |
|--------|----------|
| `en` | English |
| `fr` | French |
| `de` | German |
| `es` | Spanish |
| `pt-BR` | Portuguese (Brazil) |
| `nl` | Dutch |
| `it` | Italian |

**Adding localized pages:** Add rows to `inputs/page_config.csv` with the `locale` column set and the localized URL. H1s stay in **English** — the LLM generates content in the target language but uses the English H1 as its SEO anchor.

```csv
locale,cluster,url,h1
fr,group_annotate,https://www.pdffiller.com/fr/functionality/pdf-annotate,Annotate PDF Online
de,group_compress,https://www.pdffiller.com/de/functionality/pdf-compress,Compress PDF Online
```

**Running a single locale:**

```bash
python tools/batch_generate.py --models openai/gpt-4o-mini --locale fr --cluster group_annotate
```

**Running all locales sequentially:**

```bash
for locale in en fr de es pt-BR nl it; do
  python tools/batch_generate.py --models openai/gpt-4o-mini --locale $locale --run-label ${locale}_run1
done
```

Each locale writes to `output/progress_${locale}_run1.csv`. Safe to interrupt and resume any locale independently.

> **Note:** Running multiple locales without `--run-label` shares `output/progress.csv` and can cause progress collisions. Always use `--run-label` per locale when running them in parallel or in sequence.

---

## Tone Validation Checklist

After generating, verify content against these per-market rules:

**English (en)**
- [ ] "you" throughout — no "one" or passive constructions
- [ ] Active voice dominant
- [ ] Lead with concrete outcomes ("Sign documents in seconds", "Work from anywhere")
- [ ] "Trusted by 68M+ users" and/or "1M+ ready-to-use templates" present
- [ ] No jargon or enterprise-speak ("robust solution", "leverage", "seamlessly")

**French (fr)**
- [ ] "vous" throughout — no "tu"
- [ ] No exclamation marks in headings
- [ ] RGPD mentioned at least once
- [ ] No American-style hype ("la meilleure solution", etc.)

**German (de)**
- [ ] "Sie" throughout
- [ ] No exclamation marks in headings
- [ ] DSGVO mentioned at least once
- [ ] Claims backed by specific numbers (68M users, 1M+ templates)
- [ ] No hype words ("revolutionär", "erstaunlich")

**Spanish (es)**
- [ ] "tú" throughout
- [ ] "68M+ usuarios confían" or equivalent social proof present
- [ ] Practical outcomes emphasized ("firmar en segundos", "sin imprimir")

**Portuguese Brazil (pt-BR)**
- [ ] "você" throughout
- [ ] Anti-bureaucracy angle present
- [ ] Mobile-friendly angle included

**Dutch (nl)**
- [ ] Short, direct sentences
- [ ] ZZP'ers or freelancer segment addressed
- [ ] AVG-conform mentioned
- [ ] No marketing fluff

**Italian (it)**
- [ ] "Lei" or "voi" for B2B
- [ ] eIDAS or EU compliance mentioned
- [ ] PMI segment addressed

---

## Known Constraints

- Execution is sequential — pages and models run one at a time (no parallelism)
- `max_tokens=4096` per LLM call; target article length is ~1200–1500 words
- Section menu `[VERIFY]` flags indicate unconfirmed product details — review before publishing
- `--fallback-models` and multi-model comparison (`--models A,B`) are mutually exclusive
- Model names must be provider-prefixed when using the LiteLLM proxy (e.g. `openai/gpt-4o-mini`, not `gpt-4o-mini`)
- The `--locale` flag filters `page_config.csv` by the `locale` column — pages with a different locale value are skipped entirely in that run
- Localized URLs (e.g. `/fr/...`) are tracked separately in `progress.csv` from English URLs (e.g. `/en/...`)
- **Progress file collision risk:** Running multiple locales without `--run-label` shares `output/progress.csv`. Use `--run-label` per locale to keep progress files isolated
