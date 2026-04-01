# Generate Localized SEO Content Pages

## Objective

Run the two-prompt LLM pipeline to generate content for landing pages in any supported language. Every run uses a `--locale` flag that selects the target language and injects market-specific tone of voice instructions into both prompts. This applies to all locales, including English.

**See also:** [`workflows/generate_pages.md`](generate_pages.md) for full CLI reference, error handling, fallback models, and batch/single-page modes.

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

Tone rules are stored in `inputs/locale_config.csv`. Edit that file to update or extend market guidance.

---

## Prerequisites

1. `LITELLM_API_KEY` and `LITELLM_PROXY_URL` set in `.env`
2. Python dependencies installed: `pip install -r requirements.txt`
3. Localized page rows added to `inputs/page_config.csv` with the correct `locale` column value (e.g. `fr`, `de`)
4. `inputs/locale_config.csv` contains a row for the target locale

---

## Adding Localized Pages

Add rows to `inputs/page_config.csv` with the localized URL and English keyword:

```csv
locale,cluster,url,keyword
fr,group_annotate,https://www.pdffiller.com/fr/functionality/pdf-annotate,Annotate PDF Online
de,group_compress,https://www.pdffiller.com/de/functionality/pdf-compress,Compress PDF Online
```

Keywords stay in **English** — the LLM generates content in the target language but uses the English keyword as its SEO anchor.

---

## Steps

### 1. Validate config loads correctly for the target locale

```bash
python tools/load_config.py --cluster group_annotate --locale fr --test
```

Check: `GLOBAL_CONFIG` shows `"language": "French"`. `TONE_OF_VOICE` shows the French tone block.

### 2. Run a test batch (recommended before full run)

```bash
python tools/batch_generate.py --models openai/gpt-4o-mini --locale fr --cluster group_annotate --limit 2
```

Open `output/batch_{timestamp}.csv`. Verify:
- Content is in French
- Uses "vous" (not "tu")
- No exclamation marks in headings
- RGPD mentioned at least once

### 3. Run the full batch

```bash
python tools/batch_generate.py --models openai/gpt-4o-mini --locale fr --cluster group_annotate
```

Progress is tracked in `output/progress.csv`. Pages without the matching locale are automatically skipped.

### 4. Single page

```bash
python tools/generate_page.py \
  --keyword "Annotate PDF Online" \
  --url "https://www.pdffiller.com/fr/functionality/pdf-annotate" \
  --cluster group_annotate \
  --locale fr
```

---

## Example commands per language

```bash
# English
python tools/batch_generate.py --models openai/gpt-4o-mini --locale en

# French
python tools/batch_generate.py --models openai/gpt-4o-mini --locale fr

# German
python tools/batch_generate.py --models openai/gpt-4o-mini --locale de

# Spanish
python tools/batch_generate.py --models openai/gpt-4o-mini --locale es

# Portuguese (Brazil)
python tools/batch_generate.py --models openai/gpt-4o-mini --locale pt-BR

# Dutch
python tools/batch_generate.py --models openai/gpt-4o-mini --locale nl

# Italian
python tools/batch_generate.py --models openai/gpt-4o-mini --locale it
```

---

## Running All Locales

To process all supported locales sequentially, run each with its own `--run-label` so progress files stay isolated:

```bash
for locale in en fr de es pt-BR nl it; do
  python tools/batch_generate.py --models openai/gpt-4o-mini --locale $locale --run-label ${locale}_run1
done
```

Each locale writes to its own `output/progress_${locale}_run1.csv` and a shared `output/batch_${locale}_run1.csv`. Safe to interrupt and resume any locale independently.

---

## Tone Validation Checklist

After generating, verify the content against these per-market rules:

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

- The `--locale` flag filters `page_config.csv` by the `locale` column — pages with a different locale value are skipped entirely in that run.
- `progress.csv` tracks by URL. Localized URLs (e.g. `/fr/...`) are tracked separately from English URLs (e.g. `/en/...`).
- Keywords remain in English regardless of locale. Do not translate them in `page_config.csv`.
- Tone rules for all locales including `en` live in `inputs/locale_config.csv`. Edit that file to update tone guidance without touching any code or prompts.
- **Progress file collision risk:** Running multiple locales without `--run-label` shares `output/progress.csv`. If two locale runs overlap or are resumed out of order, one locale's progress can interfere with the other. Use `--run-label` per locale to avoid this:
  ```bash
  python tools/batch_generate.py --models openai/gpt-4o-mini --locale fr --run-label fr_annotate
  python tools/batch_generate.py --models openai/gpt-4o-mini --locale de --run-label de_annotate
  ```
- All other constraints from `generate_pages.md` apply: sequential execution, 3-retry logic, fallback models, etc.
