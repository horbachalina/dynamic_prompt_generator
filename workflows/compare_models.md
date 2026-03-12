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

## CLI Options

See `workflows/generate_pages.md` for the full `batch_generate.py` CLI reference.
