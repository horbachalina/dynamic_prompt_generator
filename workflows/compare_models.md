# Compare Models

## Objective

Run the two-prompt content pipeline across multiple LLM models for the same page(s) and produce a side-by-side CSV for quality comparison.

**When to use:** Before switching the production model, after adding a new provider to the LiteLLM proxy, or when validating that output quality is consistent across providers.

---

## Prerequisites

- `LITELLM_API_KEY` and `LITELLM_PROXY_URL` set in `.env`
- Python dependencies installed: `pip install -r requirements.txt`
- `inputs/` contains: `prompt_1.md`, `prompt_2.md`, `global_config.csv`, `cluster_config.csv`, `page_config.csv`
- All models to be tested must be registered in the LiteLLM proxy config. Check available models:
  ```bash
  curl "$LITELLM_PROXY_URL/models" -H "Authorization: Bearer $LITELLM_API_KEY"
  ```

---

## Model Name Format

Use provider-prefixed names (same as `generate_pages.md`):

| Provider  | Example `--models` value                        |
|-----------|-------------------------------------------------|
| OpenAI    | `openai/gpt-4o-mini`                            |
| Anthropic | `anthropic/claude-3-5-sonnet-20241022`          |
| Google    | `gemini/gemini-1.5-pro`                         |

---

## Steps

### 1. Quick test — single page, 2+ models

```bash
python tools/compare_models.py \
    --models openai/gpt-4o-mini anthropic/claude-3-5-sonnet-20241022 \
    --keyword "Highlight Formula and Header in PDF" \
    --url "https://www.pdffiller.com/en/functionality/highlight-formula-and-header-in-pdf"
```

Check the output CSV in `output/compare_{timestamp}.csv`. Each model's HTML is in its own column. Verify:
- Cells start with `<h2>` containing the keyword-based heading
- No cell reads `ERROR: ...`

### 2. Full batch — all pages in page_config.csv, 3 models

```bash
python tools/compare_models.py \
    --models openai/gpt-4o-mini anthropic/claude-3-5-sonnet-20241022 gemini/gemini-1.5-pro
```

The script prints `[i/total] keyword` for each page, and `✓ Done` or `✗ Error` per model. The CSV is written after every URL — safe to interrupt.

### 3. Review the CSV

Open `output/compare_{timestamp}.csv` in a spreadsheet app. For each URL row, compare HTML content across model columns. Look for:

- Heading quality and keyword placement
- Section coverage and structure (matches blueprint)
- Word count and depth
- Tone consistency with `content_rules`
- Brand mention density (target: 5–8 per article)

---

## Outputs

```
output/
├── compare_{timestamp}.csv          # rows=URLs, columns=model slugs, cells=HTML or ERROR
├── openai_gpt-4o-mini/
│   └── {url-slug}/
│       ├── blueprint.md
│       └── content.html
├── anthropic_claude-3-5-sonnet-20241022/
│   └── {url-slug}/
│       ├── blueprint.md
│       └── content.html
└── gemini_gemini-1.5-pro/
    └── {url-slug}/
        ├── blueprint.md
        └── content.html
```

Model folder names are sanitized versions of the `--models` arguments (`/` → `_`, lowercased).

---

## Error Handling

### One model fails, others succeed
The failing model's CSV cell contains `ERROR: {message}`. All other models and all remaining URLs continue. The CSV is still usable.

### All models fail for a URL
All model cells contain `ERROR:` messages. The row is still written. Re-run with `--keyword` and `--url` for that specific page after resolving the issue.

### Model not found in proxy (404)
The cell will read `ERROR: litellm.NotFoundError: ...`. Verify the model name is registered in the LiteLLM proxy config, then re-run.

### Re-running a single page
```bash
python tools/compare_models.py \
    --models openai/gpt-4o-mini anthropic/claude-3-5-sonnet-20241022 \
    --keyword "..." \
    --url "..."
```
This creates a new timestamped CSV. There is no resume functionality — each run starts fresh.

---

## CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--models` | (required) | Space-separated model names (min 2) |
| `--keyword` | None | Single-page mode keyword |
| `--url` | None | Single-page mode URL |
| `--cluster` | `group_annotate` | Cluster name |
| `--temperature` | `0.3` | Sampling temperature |
| `--timeout` | `120` | Timeout in seconds for the full two-prompt pipeline per model. On timeout, CSV cell shows `TIMEOUT` |
| `--workers` | number of models | Max parallel workers per page |

---

## Known Constraints

- **No resume:** Each invocation creates a new CSV. If interrupted, the partial CSV contains results for all completed model calls (CSV is written after every individual model result).
- **Parallel within page:** All models for the same page run concurrently (one thread per model). Pages are processed sequentially.
- **Rate limit delay:** 3 seconds between pages; models for the same page run in parallel with no delay.
- **Timeout:** Default 120s for the full two-prompt pipeline per model (Prompt 1 + Prompt 2 combined). Timed-out cells show `TIMEOUT` in the CSV (not `ERROR:`). Use `--timeout 60` for OpenAI-only runs.
- **Live CSV:** The CSV is updated after every individual model result, not just after each page. You can open it at any time to see partial results.
- **CSV file size:** Each HTML cell is ~6–8 KB. A 5-URL × 3-model run produces ~90–120 KB CSV — normal for spreadsheet apps.
- **Model slug collision:** Two model names that sanitize to the same slug (e.g., `openai/gpt4` and `openai/gpt-4`) will cause an error before any API calls are made. Use distinct model names.
