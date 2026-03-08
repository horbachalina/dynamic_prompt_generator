# Generate SEO Content Pages

## Objective

Run the two-prompt LLM pipeline to generate blueprint and HTML content for all pages in `page_config.csv` for a given cluster.

**Pipeline:** `keyword → Prompt 1 (Strategist) → <blueprint> → Prompt 2 (Writer) → content.html`

---

## Prerequisites

- `OPENAI_API_KEY` is set in `.env`
- Python dependencies installed: `pip install -r requirements.txt`
- `.tmp/` contains: `prompt_1.md`, `prompt_2.md`, `global_config.csv`, `cluster_config.csv`, `page_config.csv`

---

## Variable Scopes

| Scope | Changes when | Source file |
|---|---|---|
| Global | Switching to a different website | `global_config.csv` |
| Cluster | Switching to a different content cluster | `cluster_config.csv` (match by `cluster` column) |
| Page | Every page | `page_config.csv` (`keyword` column) |

In practice, the only thing that changes between pages is the keyword. Everything else stays constant within a cluster run.

---

## Steps

### 1. Validate config loads correctly

```bash
python tools/load_config.py --cluster group_annotate --test
```

Check the output: GLOBAL_CONFIG and CLUSTER_CONFIG should be valid JSON. SECTION_MENU should show the full section definitions block from cluster_config.csv. SHARED_RULES should show the banned words list.

### 2. Run a test batch (recommended before full run)

```bash
python tools/batch_generate.py --cluster group_annotate --limit 3
```

Open `.tmp/output/{url-slug}/content.html` for one of the 3 generated pages. Verify:
- Starts with `<h2>` containing the keyword-based heading
- Followed by a `<p>` TL;DR
- Section headings use `<h3>`, subsections use `<h4>`
- No `<div>`, `<br>`, inline styles, classes, IDs, or markdown

Open `.tmp/output/{url-slug}/blueprint.md`. Verify the blueprint covers sections A1–A5 (keyword intelligence), B6–B10 (page blueprint), and B0 METADATA (WEBSITE, LANGUAGE, TARGET_WORD_COUNT, AUDIENCE_SUMMARY).

### 3. Run the full batch

```bash
python tools/batch_generate.py --cluster group_annotate
```

Progress is tracked in `.tmp/progress.csv`. The script prints `[i/total] keyword` for each page and `✓ Done` or `✗ Error` per result.

**It is safe to interrupt at any time** (Ctrl+C). Completed pages are written to `progress.csv` after every page.

### 4. Resume after interruption

Re-run the same command. Pages with `status=done` are automatically skipped.

```bash
python tools/batch_generate.py --cluster group_annotate
```

---

## Outputs

```
.tmp/
├── progress.csv                      # status: pending | done | error
└── output/
    └── {url-slug}/
        ├── blueprint.md              # Prompt 1 output (keyword intelligence + page blueprint)
        └── content.html              # Final HTML article
```

---

## Error Handling

### API rate limit or connection error
Retried automatically up to 3 times (5s → 15s → 45s). If all retries fail, the page is marked `error` in `progress.csv`. Re-running the batch retries all error pages.

### No `<blueprint>` block in Prompt 1 response
The raw Prompt 1 response is saved to `.tmp/output/{slug}/blueprint_raw.txt` for diagnosis. Common causes: the model hit `max_tokens` before closing the tag, or the prompt was too long. Fix: reduce the prompt or increase `max_tokens` in `generate_page.py`.

### Re-running a single page

```bash
python tools/generate_page.py \
  --keyword "Annotate Quitclaim Deed" \
  --url "https://www.pdffiller.com/en/document-management/quitclaim-deed-annotate"
```

This does not update `progress.csv` — update it manually or re-run the batch (the batch will retry `error` pages automatically).

---

## Adding a New Cluster

1. Add a row to `.tmp/cluster_config.csv` with columns: `cluster`, `page_type`, `target_word_count`, `cluster_context`, `section_menu`
2. Add the new cluster's pages to `.tmp/page_config.csv` (or use a separate CSV)
3. Validate: `python tools/load_config.py --cluster {new_cluster} --test`
4. Run: `python tools/batch_generate.py --cluster {new_cluster}`

Note: `progress.csv` tracks by URL. If you reuse the same CSV for a new cluster's pages, the new URLs will be added as `pending` on first run automatically.

---

## Known Constraints

- Sequential execution only — no parallel API calls (avoids rate limit issues)
- `max_tokens=8192` for Prompt 2; target article length is 1200–1500 words (~6000–7000 tokens of HTML output, well within limit
- Section menu `[VERIFY]` flags indicate unconfirmed product details — review those sections before publishing
- `progress.csv` is re-initialized only if missing or corrupt; all completed pages are preserved across runs
