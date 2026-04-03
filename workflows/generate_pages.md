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
- `inputs/` contains: `prompt_1.md`, `prompt_2.md`, `global_config.csv`, `cluster_config.csv`, `page_config.csv`, `locale_config.csv`

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
| Locale | Switching language/market | `locale_config.csv` (match by `locale` column) — sets `language` and `tone_of_voice` in prompts |
| Cluster | Switching to a different content cluster | `cluster_config.csv` (match by `cluster` column) |
| Page | Every page | `page_config.csv` (`locale`, `cluster`, `url`, `keyword` columns) |

---

## Supported Locales

| Locale code | Language | Key tone rules |
|---|---|---|
| `en` | English | "you", active voice, outcome-led, no jargon, social proof anchors |
| `fr` | French | "vous", no exclamation marks, RGPD mention, logic-driven |
| `es` | Spanish | "tú", warm, practical outcomes, social proof prominent |
| `pt-BR` | Portuguese (Brazil) | "você", energetic, anti-bureaucracy angle, mobile-friendly |
| `nl` | Dutch | Direct, "jij/je", ZZP'ers segment, AVG compliance |
| `de` | German | "Sie", DSGVO mandatory, facts not emotions, no hype |
| `it` | Italian | "Lei/voi", warm, eIDAS compliance, PMI focus |

Full tone rules per locale are in `inputs/locale_config.csv`. Edit that file to update or extend market guidance without touching any code or prompts.

---

## Adding Localized Pages

Add rows to `inputs/page_config.csv` with the localized URL and English keyword:

```csv
locale,cluster,url,keyword
fr,group_annotate,https://www.pdffiller.com/fr/functionality/pdf-annotate,Annotate PDF Online
de,group_compress,https://www.pdffiller.com/de/functionality/pdf-compress,Compress PDF Online
```

Keywords stay in **English** — the LLM generates content in the target language but uses the English keyword as its SEO anchor. Do not translate keywords in `page_config.csv`.

---

## Steps

### 1. Validate config loads correctly

```bash
# English (default)
python tools/load_config.py --cluster group_annotate --test

# Any other locale
python tools/load_config.py --cluster group_annotate --locale fr --test
```

Check the output: GLOBAL_CONFIG and CLUSTER_CONFIG should be valid JSON. LOCALE_CONFIG should show the language and tone block for the target locale. SECTION_MENU shows the first 300 chars of the section definitions block from cluster_config.csv.

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

Progress is tracked in `output/progress.csv`. The script prints `[i/total] keyword` for each page and `✓ model_name` or `✗ model_name` per result. The CSV is written after every model result.

**It is safe to interrupt at any time** (Ctrl+C). Completed pages are marked `done` in `progress.csv` and skipped on resume.

### 4. Resume after interruption

Re-run the same command. Pages with `status=done` are automatically skipped.

```bash
python tools/batch_generate.py --models openai/gpt-4o-mini --cluster group_annotate
```

Use `--run-label` to keep a stable CSV filename across resume runs:

```bash
python tools/batch_generate.py --models openai/gpt-4o-mini --run-label annotate_run1
# → writes to output/batch_annotate_run1.csv and output/progress_annotate_run1.csv
```

> Note: Without `--run-label`, progress is tracked in `output/progress.csv`. With `--run-label`, a separate `output/progress_{run_label}.csv` is used — useful when running multiple clusters or locales in parallel without cross-contaminating progress state.

### 5. Run with a fallback model

If your primary model fails all 3 retries, the pipeline automatically tries the fallback before marking the page as error.

**Single page:**
```bash
python tools/generate_page.py \
  --keyword "Annotate Quitclaim Deed" \
  --url "https://www.pdffiller.com/en/document-management/quitclaim-deed-annotate" \
  --model anthropic/claude-3-5-sonnet-20241022 \
  --fallback-models openai/gpt-4o-mini
```

**Batch:**
```bash
python tools/batch_generate.py \
  --models anthropic/claude-3-5-sonnet-20241022 \
  --fallback-models openai/gpt-4o-mini \
  --cluster group_annotate
```

When a fallback fires, the CSV includes a `{slug}_model_used` column showing which model actually produced the output. The batch output also prints which fallback was used:
```
✓ claude-3-5-sonnet → ~1342 words (fallback: gpt-4o-mini)
```

> Note: `--fallback-models` and multi-model comparison (`--models A,B`) are mutually exclusive. Use one primary model with `--models` when using `--fallback-models`.

### 6. Multi-model batch (for model comparison)

```bash
python tools/batch_generate.py \
    --models openai/gpt-4o-mini,anthropic/claude-3-5-sonnet-20241022 \
    --cluster group_annotate \
    --limit 3
```

The CSV will have two columns per model: `{slug}_blueprint` and `{slug}_content`.

### 7. Single page (saves to folder)

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
output/
├── progress.csv                      # status: pending | done | error
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
| `--fallback-models` | None | Comma-separated fallback models tried in order if the primary exhausts all retries (e.g. `openai/gpt-4o-mini`). Cannot be combined with multi-model comparison. |
| `--locale` | `en` | Language/market for this run. Filters `page_config.csv` by `locale` column and injects tone of voice. See `inputs/locale_config.csv` for available codes. |
| `--cluster` | None (all) | Filter by cluster name |
| `--limit` | None | Max pages to process (for testing) |
| `--temperature` | `0.3` | Sampling temperature |
| `--timeout` | `180` | Timeout in seconds per model call |
| `--run-label` | None | Stable label for CSV filename and progress file (enables deterministic resume) |

---

## Error Handling

### API rate limit or connection error
Retried automatically up to 3 times (5s → 15s → 45s). If all retries fail and a `--fallback-models` chain is configured, each fallback is tried next (also with up to 3 retries). Only after all models in the chain are exhausted does the page get marked `error` in `progress.csv`. Re-running the batch retries all error pages.

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

## Localized Runs

Run a single locale batch by adding `--locale`:

```bash
python tools/batch_generate.py --models openai/gpt-4o-mini --locale fr --cluster group_annotate --limit 2  # test first
python tools/batch_generate.py --models openai/gpt-4o-mini --locale fr --cluster group_annotate            # full run
```

Single page with locale:

```bash
python tools/generate_page.py \
  --keyword "Annotate PDF Online" \
  --url "https://www.pdffiller.com/fr/functionality/pdf-annotate" \
  --cluster group_annotate \
  --locale fr
```

Run all locales sequentially with isolated progress files:

```bash
for locale in en fr de es pt-BR nl it; do
  python tools/batch_generate.py --models openai/gpt-4o-mini --locale $locale --run-label ${locale}_run1
done
```

Each locale writes to `output/progress_${locale}_run1.csv`. Safe to interrupt and resume any locale independently.

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

## Adding a New Cluster

1. Add a row to `inputs/cluster_config.csv` with columns: `cluster`, `target_word_count`, `cluster_context`, `section_menu`
2. Add the new cluster's pages to `inputs/page_config.csv` with columns: `locale`, `cluster`, `url`, `keyword`
3. Validate: `python tools/load_config.py --cluster {new_cluster} --test`
4. Run: `python tools/batch_generate.py --models openai/gpt-4o-mini --cluster {new_cluster}`

Note: `progress.csv` tracks by URL. New URLs are added as `pending` on first run automatically.

---

## Known Constraints

- Sequential execution — models for each page run one after another (no parallelism)
- `max_tokens=4096` for both Prompt 1 and Prompt 2; target article length is 1200–1500 words (~6000–7000 tokens of HTML output, well within limit)
- Section menu `[VERIFY]` flags indicate unconfirmed product details — review those sections before publishing
- `progress.csv` is re-initialized only if missing or corrupt; all completed pages are preserved across runs
- Model names must be provider-prefixed when using the LiteLLM proxy (e.g. `openai/gpt-4o-mini`, not `gpt-4o-mini`)
- The `--locale` flag filters `page_config.csv` by the `locale` column — pages with a different locale value are skipped entirely in that run
- Localized URLs (e.g. `/fr/...`) are tracked separately in `progress.csv` from English URLs (e.g. `/en/...`)
- **Progress file collision risk:** Running multiple locales without `--run-label` shares `output/progress.csv`. Use `--run-label` per locale to keep progress files isolated:
  ```bash
  python tools/batch_generate.py --models openai/gpt-4o-mini --locale fr --run-label fr_annotate
  python tools/batch_generate.py --models openai/gpt-4o-mini --locale de --run-label de_annotate
  ```
