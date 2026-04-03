"""
batch_generate.py — Batch runner for the two-prompt SEO content pipeline.

Reads page_config.csv, skips already-completed pages, tracks progress in
output/progress.csv. Output saved to output/batch_{timestamp}.csv.
Safe to interrupt and resume at any time.

Usage:
    python tools/batch_generate.py --models openai/gpt-4o-mini
    python tools/batch_generate.py --models openai/gpt-4o-mini,anthropic/claude-3-5-sonnet-20241022 --cluster group_annotate --limit 3
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_page import generate_single_page


def _default_base_dir():
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(tools_dir), "inputs")


def sanitize_model_name(model: str) -> str:
    """Convert a model name to a filesystem-safe column slug."""
    slug = model.replace("/", "_")
    slug = re.sub(r"[^a-zA-Z0-9._-]", "", slug)
    return slug.lower()


def write_batch_csv(rows: list, models: list, output_path: str) -> None:
    """Write batch results to CSV — one row per URL, two columns per model.

    When a fallback fired for a model slot, an extra {slug}_model_used column is
    written so it's clear which model actually produced the output.
    """
    model_slugs = [sanitize_model_name(m) for m in models]

    # Detect if any row recorded a model_used override (i.e. fallback fired)
    has_model_used = any(
        "model_used" in row["model_results"].get(slug, {})
        for row in rows
        for slug in model_slugs
    )

    columns = ["url", "h1", "url_slug", "cluster"]
    for slug in model_slugs:
        columns += [f"{slug}_blueprint", f"{slug}_content"]
        if has_model_used:
            columns.append(f"{slug}_model_used")

    records = []
    for row in rows:
        record = {
            "url": row["url"],
            "h1": row["h1"],
            "url_slug": row["url_slug"],
            "cluster": row["cluster"],
        }
        for slug in model_slugs:
            result_data = row["model_results"].get(slug, {})
            record[f"{slug}_blueprint"] = result_data.get("blueprint", "")
            record[f"{slug}_content"] = result_data.get("content", "")
            if has_model_used:
                record[f"{slug}_model_used"] = result_data.get("model_used", "")
        records.append(record)
    df = pd.DataFrame(records, columns=columns)
    df.to_csv(output_path, index=False, encoding="utf-8")


def _url_to_slug(url: str) -> str:
    """Extract a url_slug from a URL, falling back to subdomain for subdomain-only URLs."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    slug = parsed.path.rstrip("/").split("/")[-1]
    if not slug:
        hostname = parsed.hostname or ""
        slug = hostname.split(".")[0] if hostname else ""
    return slug


def _init_progress(page_config_path: str, progress_path: str) -> pd.DataFrame:
    """Create progress.csv from page_config.csv with all pages as pending."""
    pages = pd.read_csv(page_config_path)
    url_slugs = [_url_to_slug(u) for u in pages["url"]]

    progress = pd.DataFrame(
        {
            "url": pages["url"],
            "h1": pages["h1"],
            "url_slug": url_slugs,
            "status": "pending",
            "error": "",
            "timestamp": "",
        }
    )
    progress.to_csv(progress_path, index=False)
    return progress


def _load_progress(progress_path: str, page_config_path: str) -> pd.DataFrame:
    """Load existing progress.csv, falling back to re-init if corrupt."""
    try:
        df = pd.read_csv(progress_path)
        required = {"url", "h1", "url_slug", "status", "error", "timestamp"}
        if not required.issubset(df.columns):
            raise ValueError("Missing columns")
        for col in ("url_slug", "status", "error", "timestamp"):
            df[col] = df[col].fillna("").astype(str)
        return df
    except Exception:
        print(f"  {os.path.basename(progress_path)} appears corrupt — reinitializing from page_config.csv")
        return _init_progress(page_config_path, progress_path)


def _sync_new_pages(progress_df: pd.DataFrame, page_config_path: str, progress_path: str) -> pd.DataFrame:
    """Append rows to progress.csv for any page_config URLs not already tracked."""
    all_pages = pd.read_csv(page_config_path)
    existing_urls = set(progress_df["url"])
    new_pages = all_pages[~all_pages["url"].isin(existing_urls)]

    if new_pages.empty:
        return progress_df

    url_slugs = [_url_to_slug(u) for u in new_pages["url"]]
    new_rows = pd.DataFrame({
        "url": new_pages["url"].values,
        "h1": new_pages["h1"].values,
        "url_slug": url_slugs,
        "status": "pending",
        "error": "",
        "timestamp": "",
    })
    result = pd.concat([progress_df, new_rows], ignore_index=True)
    result.to_csv(progress_path, index=False)
    print(f"  Added {len(new_rows)} new page(s) from page_config.csv to progress tracking.")
    return result


def _load_existing_batch_rows(csv_path: str, models: list) -> list:
    """Load completed rows from an existing batch CSV into the rows dict format.

    Used to pre-populate the rows list when resuming a run_label run, so the
    final CSV contains all pages (previously completed + newly processed).
    Only called when --run-label is set and the CSV already exists.
    """
    if not os.path.exists(csv_path):
        return []
    try:
        df = pd.read_csv(csv_path)
        model_slugs = [sanitize_model_name(m) for m in models]
        rows = []
        for _, r in df.iterrows():
            model_results = {}
            for slug in model_slugs:
                bp_col = f"{slug}_blueprint"
                ct_col = f"{slug}_content"
                mu_col = f"{slug}_model_used"
                if bp_col in df.columns and ct_col in df.columns:
                    entry = {
                        "blueprint": r.get(bp_col, ""),
                        "content": r.get(ct_col, ""),
                    }
                    if mu_col in df.columns:
                        entry["model_used"] = r.get(mu_col, "")
                    model_results[slug] = entry
            rows.append({
                "url": r["url"],
                "h1": r["h1"],
                "url_slug": r.get("url_slug", ""),
                "cluster": r.get("cluster", ""),
                "model_results": model_results,
            })
        return rows
    except Exception:
        return []


def run_batch(
    models: list,
    fallback_models: list = None,
    cluster: str = None,
    limit: int = None,
    base_dir: str = None,
    temperature: float = 0.3,
    timeout: int = None,
    run_label: str = None,
    locale: str = "en",
):
    if base_dir is None:
        base_dir = _default_base_dir()

    page_config_path = os.path.join(base_dir, "page_config.csv")

    output_root = os.path.join(os.path.dirname(base_dir), "output")
    os.makedirs(output_root, exist_ok=True)

    progress_path = (
        os.path.join(output_root, f"progress_{run_label}.csv")
        if run_label
        else os.path.join(output_root, "progress.csv")
    )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_name = f"batch_{run_label}.csv" if run_label else f"batch_{timestamp}.csv"
    csv_path = os.path.join(output_root, csv_name)

    # Initialize or load progress, then sync any new pages added to page_config.csv
    if not os.path.exists(progress_path):
        print("No progress.csv found — initializing from page_config.csv")
        progress_df = _init_progress(page_config_path, progress_path)
    else:
        progress_df = _load_progress(progress_path, page_config_path)
        progress_df = _sync_new_pages(progress_df, page_config_path, progress_path)

    # Build url → cluster mapping from page_config.csv (filtered by locale)
    page_df = pd.read_csv(page_config_path)
    all_locales = locale == "all"
    if "locale" in page_df.columns and not all_locales:
        page_df = page_df[page_df["locale"] == locale].copy()
    url_to_cluster = dict(zip(page_df["url"], page_df["cluster"]))
    url_to_locale = dict(zip(page_df["url"], page_df["locale"])) if "locale" in page_df.columns else {}

    # Select pending rows, optionally filtered by cluster
    pending = progress_df[progress_df["status"] != "done"].copy()
    # Filter to URLs that belong to this locale
    pending = pending[pending["url"].isin(url_to_cluster)].copy()
    if cluster is not None:
        pending = pending[pending["url"].map(url_to_cluster) == cluster].copy()
    if limit is not None:
        pending = pending.head(limit)

    total = len(pending)
    if total == 0:
        print("Nothing to do — all pages are already done.")
        return

    done_count = (progress_df["status"] == "done").sum()
    model_names = ", ".join(m.split("/")[-1] for m in models)
    print(f"Batch starting: {total} pages to process ({done_count} already done).")
    print(f"Locale: {'all' if all_locales else locale}")
    if cluster:
        print(f"Cluster filter: {cluster}")
    if fallback_models:
        fallback_names = ", ".join(m.split("/")[-1] for m in fallback_models)
        print(f"Models: {model_names} (fallback: {fallback_names})")
    else:
        print(f"Models: {model_names}")
    print(f"Temperature: {temperature}")
    if timeout:
        print(f"Timeout: {timeout}s per model")
    print(f"CSV: {csv_path}")
    if run_label:
        print(f"Run label: {run_label}")
    print()

    completed = 0
    errors = 0

    # When resuming a labeled run, pre-load previously completed rows so the final
    # CSV contains all pages, not just those processed in this invocation.
    rows = _load_existing_batch_rows(csv_path, models) if run_label else []
    # Remove any URLs we're about to process (they'll be re-added with fresh results)
    pending_urls = set(pending["url"])
    rows = [r for r in rows if r["url"] not in pending_urls]

    # Load prompt templates and per-cluster config caches.
    # Each cluster's config is loaded once on first encounter.
    from load_config import load_config
    _dummy_url = "https://example.com/placeholder"
    with open(os.path.join(base_dir, "prompt_1.md"), "r", encoding="utf-8") as f:
        prompt1_template = f.read()
    with open(os.path.join(base_dir, "prompt_2.md"), "r", encoding="utf-8") as f:
        prompt2_template = f.read()

    cluster_caches: dict = {}

    for i, row in enumerate(pending.itertuples(), 1):
        print(f"[{i}/{total}] {row.h1}")

        row_cluster = url_to_cluster.get(row.url)
        if not row_cluster:
            page_row = {
                "url": row.url,
                "h1": row.h1,
                "url_slug": row.url_slug or "",
                "cluster": "",
                "model_results": {
                    sanitize_model_name(m): {
                        "blueprint": "ERROR: cluster not found",
                        "content": "ERROR: cluster not found",
                    }
                    for m in models
                },
            }
            rows.append(page_row)
            write_batch_csv(rows, models, csv_path)

            idx = progress_df[progress_df["url"] == row.url].index[0]
            progress_df.at[idx, "status"] = "error"
            progress_df.at[idx, "error"] = "No cluster found for URL"
            progress_df.at[idx, "timestamp"] = datetime.now().isoformat(timespec="seconds")
            progress_df.to_csv(progress_path, index=False)
            errors += 1
            print(f"  ✗ Error: No cluster found for URL")
            continue

        row_locale = url_to_locale.get(row.url, locale) if all_locales else locale

        cache_key = (row_cluster, row_locale)
        if cache_key not in cluster_caches:
            cache = load_config(cluster=row_cluster, keyword="__cache__", url=_dummy_url, base_dir=base_dir, locale=row_locale)
            cache["PROMPT_1_TEMPLATE"] = prompt1_template
            cache["PROMPT_2_TEMPLATE"] = prompt2_template
            cluster_caches[cache_key] = cache
        config_cache = cluster_caches[cache_key]

        page_row = {
            "url": row.url,
            "h1": row.h1,
            "url_slug": row.url_slug or "",
            "cluster": row_cluster,
            "model_results": {},
        }
        rows.append(page_row)

        page_ok = True
        last_result = None

        for model in models:
            model_slug = sanitize_model_name(model)
            model_short = model.split("/")[-1]

            last_result = generate_single_page(
                keyword=row.h1,
                url=row.url,
                cluster=row_cluster,
                base_dir=base_dir,
                model=model,
                fallback_models=fallback_models,
                temperature=temperature,
                timeout=timeout,
                config_cache=config_cache,
                return_raw=True,
                locale=row_locale,
            )

            if last_result["status"] == "done":
                used_model = last_result.get("model_used", model)
                result_entry = {
                    "blueprint": last_result["blueprint"],
                    "content": last_result["content"],
                }
                if fallback_models:
                    result_entry["model_used"] = used_model
                page_row["model_results"][model_slug] = result_entry
                word_count = len(last_result["content"].split())
                if used_model != model:
                    print(f"  ✓ {model_short} → ~{word_count} words (fallback: {used_model.split('/')[-1]})")
                else:
                    print(f"  ✓ {model_short} → ~{word_count} words")
            else:
                error_msg = last_result.get("error") or "unknown error"
                blueprint_val = last_result.get("blueprint") or f"ERROR: {error_msg}"
                result_entry = {
                    "blueprint": blueprint_val,
                    "content": f"ERROR: {error_msg}",
                }
                if fallback_models:
                    result_entry["model_used"] = ""
                page_row["model_results"][model_slug] = result_entry
                page_ok = False
                print(f"  ✗ {model_short}: {error_msg}")

            # Write CSV after every model result (safe to interrupt mid-page)
            write_batch_csv(rows, models, csv_path)

        # Update progress
        idx = progress_df[progress_df["url"] == row.url].index[0]
        progress_df.at[idx, "status"] = "done" if page_ok else "error"
        progress_df.at[idx, "url_slug"] = page_row["url_slug"]
        progress_df.at[idx, "error"] = "" if page_ok else "one or more models failed — see CSV"
        progress_df.at[idx, "timestamp"] = datetime.now().isoformat(timespec="seconds")
        progress_df.to_csv(progress_path, index=False)

        if page_ok:
            completed += 1
        else:
            errors += 1

        # Rate limit delay between pages (skip after last page)
        if i < total:
            error_msg = (last_result.get("error") or "") if last_result else ""
            delay = 3 if "rate limit" in error_msg.lower() else 0.5
            time.sleep(delay)

    print()
    print(f"Batch complete.")
    print(f"  {completed} done, {errors} errors")
    print(f"  CSV: {csv_path}")
    if errors > 0:
        print(f"  Error details in: {progress_path}")
        print(f"  Re-run to retry failed pages.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch generate SEO content pages.")
    parser.add_argument(
        "--models",
        default="openai/gpt-4o-mini",
        help="Comma-separated model names (provider-prefixed, e.g. openai/gpt-4o-mini,anthropic/claude-3-5-sonnet-20241022)",
    )
    parser.add_argument(
        "--fallback-models",
        default=None,
        help="Comma-separated fallback models tried in order if the primary fails (e.g. openai/gpt-4o-mini). Cannot be combined with multi-model comparison.",
    )
    parser.add_argument("--cluster", default=None, help="Filter by cluster name (optional — omit to process all clusters)")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max pages to process in this run (for testing)",
    )
    parser.add_argument("--temperature", type=float, default=0.3, help="Sampling temperature")
    parser.add_argument("--timeout", type=int, default=180, help="Timeout in seconds per model call (default: 180)")
    parser.add_argument(
        "--run-label",
        default=None,
        help="Label for this run (creates separate progress file and fixed CSV name for resume)",
    )
    parser.add_argument("--locale", default="en", help="Locale code (e.g. en, fr, de, es, pt-BR, nl, it) or 'all' to process every locale in one run")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        print("Error: --models must specify at least one model.")
        sys.exit(1)

    fallback_models = None
    if args.fallback_models:
        fallback_models = [m.strip() for m in args.fallback_models.split(",") if m.strip()]
        if len(models) > 1:
            print("Error: --fallback-models cannot be combined with multi-model comparison (--models A,B).")
            print("  Use a single primary model with --models, then list fallbacks with --fallback-models.")
            sys.exit(1)

    run_batch(
        models=models,
        fallback_models=fallback_models,
        cluster=args.cluster,
        limit=args.limit,
        temperature=args.temperature,
        timeout=args.timeout,
        run_label=args.run_label,
        locale=args.locale,
    )
