# Compare Models

## Objective

Run the two-prompt content pipeline across multiple LLM models for the same page(s) and produce a side-by-side CSV for quality comparison.

**When to use:** Before switching the production model, after adding a new provider to the LiteLLM proxy, or when validating that output quality is consistent across providers.

---

## Steps

### 1. Quick test — single cluster, 2 models, 1 page

```bash
python tools/batch_generate.py \
    --models openai/gpt-4o-mini,anthropic/claude-3-5-sonnet-20241022 \
    --cluster group_annotate \
    --limit 1
```

### 2. Full batch — all pages, 3 models

```bash
python tools/batch_generate.py \
    --models openai/gpt-4o-mini,anthropic/claude-3-5-sonnet-20241022,gemini/gemini-1.5-pro
```

### 3. Localized comparison (add `--locale`)

To compare models for a specific market, add `--locale`:

```bash
python tools/batch_generate.py \
    --models openai/gpt-4o-mini,anthropic/claude-3-5-sonnet-20241022 \
    --locale fr \
    --cluster group_annotate \
    --limit 3
```

See `inputs/locale_config.csv` for available locale codes (`en`, `fr`, `de`, `es`, `pt-BR`, `nl`, `it`).

---

## Output

```
output/
└── batch_{timestamp}.csv   # rows=URLs, columns: url, keyword, url_slug, cluster, {slug}_blueprint, {slug}_content, ...
```

Each model has two columns: `{model_slug}_blueprint` (Prompt 1 output) and `{model_slug}_content` (Prompt 2 HTML).

---

## Reviewing Results

Open the CSV in a spreadsheet app. For each URL row, compare across models:

- Heading quality and keyword placement
- Section coverage and structure (matches blueprint)
- Word count and depth
- Tone consistency with prompt_2 writing rules
- Brand mention density (target: 5–6 per article)

---

## Known Constraints

- **`--fallback-models` is incompatible with multi-model comparison.** Use a single `--models` value when using fallbacks. Multi-model runs (`--models A,B`) do not support fallback chains.
- **Comparison runs mark pages `done` in `progress.csv`.** This means a subsequent production run with the same `progress.csv` will skip pages that were only run for comparison. To avoid this, use `--run-label` to isolate the comparison into its own progress file:
  ```bash
  python tools/batch_generate.py \
      --models openai/gpt-4o-mini,anthropic/claude-3-5-sonnet-20241022 \
      --run-label model_comparison_apr
  ```
- **If one model fails mid-batch**, the page is marked `error` in `progress.csv` but the CSV still contains whatever that model produced (partial output or error message). Other models' output for the same page is preserved. Re-run to retry failed pages.
- Model names must be provider-prefixed (e.g. `openai/gpt-4o-mini`, not `gpt-4o-mini`).

---

## CLI Options

See `workflows/generate_pages.md` for the full `batch_generate.py` CLI reference.
