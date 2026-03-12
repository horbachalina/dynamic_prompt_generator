# Generate SEO Content Pages

## Objective

Run the two-prompt LLM pipeline to generate blueprint and HTML content for pages in `page_config.csv`.

**Pipeline:** `keyword → Prompt 1 (Strategist) → <blueprint> → Prompt 2 (Writer) → content`

**Two modes:**
- **Single page** (`generate_page.py`) → saves `blueprint.md` + `content.html` to `output/{url-slug}/`
- **Batch** (`batch_generate.py`) → saves all results to `output/batch_{timestamp}.csv` (supports 1+ models)

---

## Prerequisites

- `LITELLM_API_KEY` and `LITELLM_PROXY_URL` are set in `.env`
- Python dependencies installed: `pip install -r requirements.txt`
- `inputs/` contains: `prompt_1.md`, `prompt_2.md`, `global_config.csv`, `cluster_config.csv`, `page_config.csv`

---

## Model Selection

The pipeline routes all calls through the LiteLLM proxy. Use provider-prefixed model names:

| Provider  | Example value                                   |
|-----------|-------------------------------------------------|
| OpenAI    | `openai/gpt-4o-mini` (default)                  |
| Anthropic | `anthropic/claude-3-5-sonnet-20241022`          |
| Google    | `gemini/gemini-1.5-pro`                         |

Parameter quirks (`max_completion_tokens` vs `max_tokens`, no-temperature models) are handled automatically — no manual tuning needed when switching models.

---

## Variable Scopes

| Scope | Changes when | Source file |
|---|---|---|
| Global | Switching to a different website | `global_config.csv` |
| Cluster | Switching to a different content cluster | `cluster_config.csv` (match by `cluster` column) |
| Page | Every page | `page_config.csv` (`cluster`, `url`, `keyword` columns) |

---

## Steps

### 1. Validate config loads correctly

```bash
python tools/load_config.py --cluster group_annotate --test
```

Check the output: GLOBAL_CONFIG and CLUSTER_CONFIG should be valid JSON. SECTION_MENU should show the full section definitions block from cluster_config.csv.

### 2. Run a test batch (recommended before full run)

```bash
python tools/batch_generate.py --models openai/gpt-4o-mini --cluster group_annotate --limit 3
```

Open `output/batch_{timestamp}.csv`. Verify the `_content` cells:
- Start with `<h2>` containing the keyword-based heading
- Followed by a `<p>` TL;DR
- Section headings use `<h3>`, subsections use `<h4>`
- No `<div>`, `<br>`, inline styles, classes, IDs, or markdown

### 3. Run the full batch

```bash
python tools/batch_generate.py --models openai/gpt-4o-mini --cluster group_annotate
```

Progress is tracked in `inputs/progress.csv`. The script prints `[i/total] keyword` for each page and `✓ model_name` or `✗ model_name` per result. The CSV is written after every model result.

**It is safe to interrupt at any time** (Ctrl+C). Completed pages are marked `done` in `progress.csv` and skipped on resume.

### 4. Resume after interruption

Re-run the same command. Pages with `status=done` are automatically skipped.

```bash
python tools/batch_generate.py --models openai/gpt-4o-mini --cluster group_annotate
```

Use `--run-label` to keep a stable CSV filename across resume runs:

```bash
python tools/batch_generate.py --models openai/gpt-4o-mini --run-label annotate_run1
# → writes to output/batch_annotate_run1.csv and inputs/progress_annotate_run1.csv
```

### 5. Multi-model batch (for model comparison)

```bash
python tools/batch_generate.py \
    --models openai/gpt-4o-mini,anthropic/claude-3-5-sonnet-20241022 \
    --cluster group_annotate \
    --limit 3
```

The CSV will have two columns per model: `{slug}_blueprint` and `{slug}_content`.

### 6. Single page (saves to folder)

```bash
python tools/generate_page.py \
  --keyword "Annotate Quitclaim Deed" \
  --url "https://www.pdffiller.com/en/document-management/quitclaim-deed-annotate"
```

Output goes to `output/{url-slug}/blueprint.md` and `output/{url-slug}/content.html`.

---

## Outputs

**Batch mode:**
```
inputs/
└── progress.csv                      # status: pending | done | error

output/
└── batch_{timestamp}.csv             # url, keyword, url_slug, cluster, {model}_blueprint, {model}_content, ...
```

**Single page mode:**
```
output/
└── {url-slug}/
    ├── blueprint.md                  # Prompt 1 output (keyword intelligence + page blueprint)
    └── content.html                  # Final HTML article
```

---

## batch_generate.py CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--models` | `openai/gpt-4o-mini` | Comma-separated model names (e.g. `openai/gpt-4o-mini,anthropic/claude-3-5-sonnet-20241022`) |
| `--cluster` | None (all) | Filter by cluster name |
| `--limit` | None | Max pages to process (for testing) |
| `--temperature` | `0.3` | Sampling temperature |
| `--timeout` | None | Timeout in seconds per model (shows `ERROR: timed out` in CSV on timeout) |
| `--run-label` | None | Stable label for CSV filename and progress file (enables deterministic resume) |

---

## Error Handling

### API rate limit or connection error
Retried automatically up to 3 times (5s → 15s → 45s). If all retries fail, the cell shows `ERROR: ...` in the CSV and the page is marked `error` in `progress.csv`. Re-running the batch retries all error pages.

### No `<blueprint>` block in Prompt 1 response
In batch mode: the `_blueprint` CSV cell contains the raw Prompt 1 response for diagnosis.
In single-page mode: raw response is saved to `output/{slug}/blueprint_raw.txt`.

### Re-running a single page
```bash
python tools/generate_page.py \
  --keyword "Annotate Quitclaim Deed" \
  --url "https://www.pdffiller.com/en/document-management/quitclaim-deed-annotate"
```
This does not update `progress.csv` — update it manually or re-run the batch (the batch will retry `error` pages automatically).

---

## Adding a New Cluster

1. Add a row to `inputs/cluster_config.csv` with columns: `cluster`, `target_word_count`, `cluster_context`, `section_menu`
2. Add the new cluster's pages to `inputs/page_config.csv` with columns: `cluster`, `url`, `keyword`
3. Validate: `python tools/load_config.py --cluster {new_cluster} --test`
4. Run: `python tools/batch_generate.py --models openai/gpt-4o-mini --cluster {new_cluster}`

Note: `progress.csv` tracks by URL. New URLs are added as `pending` on first run automatically.

---

## Known Constraints

- Sequential execution — models for each page run one after another (no parallelism)
- `max_tokens=8192` for Prompt 2; target article length is 1200–1500 words (~6000–7000 tokens of HTML output, well within limit)
- Section menu `[VERIFY]` flags indicate unconfirmed product details — review those sections before publishing
- `progress.csv` is re-initialized only if missing or corrupt; all completed pages are preserved across runs
- Model names must be provider-prefixed when using the LiteLLM proxy (e.g. `openai/gpt-4o-mini`, not `gpt-4o-mini`)
