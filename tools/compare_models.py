"""
compare_models.py — Run the two-prompt pipeline across multiple models and compare outputs.

Generates content for each model, saves per-model files to output/{model_slug}/{url_slug}/,
and writes a comparison CSV to output/compare_{timestamp}.csv.

Features:
- Parallel model execution per page (all models run concurrently for the same URL)
- Live CSV updates after every individual model result
- 30s timeout per LLM call (shows TIMEOUT in CSV)
- Detailed logging with timestamps and elapsed times

Usage (single page):
    python tools/compare_models.py \
        --models openai/gpt-4o-mini anthropic/claude-3-5-sonnet-20241022 gemini/gemini-1.5-pro \
        --keyword "Annotate PDF" \
        --url "https://www.pdffiller.com/en/document-management/quitclaim-deed-annotate"

Usage (batch — reads page_config.csv):
    python tools/compare_models.py \
        --models openai/gpt-4o-mini anthropic/claude-3-5-sonnet-20241022 gemini/gemini-1.5-pro
"""

import argparse
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_page import generate_single_page


def _default_base_dir():
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(tools_dir), "inputs")


def _ts():
    """Return a short timestamp for log lines."""
    return datetime.now().strftime("%H:%M:%S")


def sanitize_model_name(model: str) -> str:
    """Convert a model name to a filesystem-safe slug."""
    slug = model.replace("/", "_")
    slug = re.sub(r"[^a-zA-Z0-9._-]", "", slug)
    return slug.lower()


def _derive_url_slug(url: str) -> str:
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    if not slug:
        raise ValueError(f"Could not derive url_slug from URL: {url!r}")
    return slug


def write_comparison_csv(rows: list, models: list, output_path: str) -> None:
    """Write comparison results to a CSV file (thread-safe via external lock)."""
    model_slugs = [sanitize_model_name(m) for m in models]
    records = []
    for row in rows:
        record = {
            "url": row["url"],
            "keyword": row["keyword"],
            "url_slug": row["url_slug"],
        }
        for slug in model_slugs:
            record[slug] = row["model_results"].get(slug, "")
        records.append(record)
    df = pd.DataFrame(records, columns=["url", "keyword", "url_slug"] + model_slugs)
    df.to_csv(output_path, index=False, encoding="utf-8")


def _run_single_model(
    model: str,
    keyword: str,
    url: str,
    cluster: str,
    base_dir: str,
    temperature: float,
    timeout: int,
    page_idx: int,
    total_pages: int,
) -> tuple:
    """Run generate_single_page for one model. Returns (model_slug, html_or_error)."""
    model_slug = sanitize_model_name(model)
    model_short = model.split("/")[-1]
    start = time.time()
    print(f"  [{_ts()}] [{page_idx}/{total_pages}] ▶ Starting {model_short}")
    sys.stdout.flush()

    model_output_dir = os.path.join(os.path.dirname(base_dir), "output", model_slug)
    try:
        result = generate_single_page(
            keyword=keyword,
            url=url,
            cluster=cluster,
            base_dir=base_dir,
            output_dir=model_output_dir,
            model=model,
            temperature=temperature,
            timeout=timeout,
        )
    except Exception as e:
        elapsed = time.time() - start
        error_str = str(e)
        if "timed out" in error_str.lower() or "timeout" in error_str.lower():
            print(f"  [{_ts()}] [{page_idx}/{total_pages}] ⏱ TIMEOUT {model_short} ({elapsed:.1f}s)")
            sys.stdout.flush()
            return model_slug, "TIMEOUT"
        print(f"  [{_ts()}] [{page_idx}/{total_pages}] ✗ {model_short} crashed ({elapsed:.1f}s): {error_str[:120]}")
        sys.stdout.flush()
        return model_slug, f"ERROR: {error_str}"

    elapsed = time.time() - start
    if result["status"] == "done":
        with open(result["content_path"], "r", encoding="utf-8") as f:
            html = f.read()
        word_count = len(html.split())
        print(f"  [{_ts()}] [{page_idx}/{total_pages}] ✓ {model_short} done ({elapsed:.1f}s, ~{word_count} words)")
        sys.stdout.flush()
        return model_slug, html
    else:
        error_str = result["error"] or "unknown error"
        if "timed out" in error_str.lower() or "timeout" in error_str.lower():
            print(f"  [{_ts()}] [{page_idx}/{total_pages}] ⏱ TIMEOUT {model_short} ({elapsed:.1f}s)")
            sys.stdout.flush()
            return model_slug, "TIMEOUT"
        print(f"  [{_ts()}] [{page_idx}/{total_pages}] ✗ {model_short} error ({elapsed:.1f}s): {error_str[:120]}")
        sys.stdout.flush()
        return model_slug, f"ERROR: {error_str}"


def run_comparison(
    models: list,
    keyword: str = None,
    url: str = None,
    cluster: str = "group_annotate",
    base_dir: str = None,
    temperature: float = 0.3,
    timeout: int = 30,
    max_workers: int = None,
) -> str:
    """Orchestrate single-page or batch model comparison with parallel execution."""
    if base_dir is None:
        base_dir = _default_base_dir()

    if len(models) < 2:
        raise ValueError("--models requires at least 2 model names.")

    model_slugs = [sanitize_model_name(m) for m in models]
    if len(set(model_slugs)) != len(model_slugs):
        dupes = [s for s in model_slugs if model_slugs.count(s) > 1]
        raise ValueError(f"Model names sanitize to duplicate slugs: {dupes}. Use distinct model names.")

    single_page_mode = (keyword is not None) and (url is not None)
    batch_mode = (keyword is None) and (url is None)
    if not single_page_mode and not batch_mode:
        raise ValueError(
            "Provide both --keyword and --url for single-page mode, "
            "or neither for batch mode (reads page_config.csv)."
        )

    if single_page_mode:
        pages = [(keyword.strip(), url.strip())]
    else:
        page_config_path = os.path.join(base_dir, "page_config.csv")
        if not os.path.exists(page_config_path):
            raise FileNotFoundError(f"page_config.csv not found at: {page_config_path}")
        config_df = pd.read_csv(page_config_path)
        if "url" not in config_df.columns or "keyword" not in config_df.columns:
            raise ValueError("page_config.csv must have 'url' and 'keyword' columns.")
        pages = [(str(row["keyword"]).strip(), str(row["url"]).strip()) for _, row in config_df.iterrows()]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = os.path.join(os.path.dirname(base_dir), "output")
    os.makedirs(output_root, exist_ok=True)
    csv_path = os.path.join(output_root, f"compare_{timestamp}.csv")

    if max_workers is None:
        max_workers = len(models)

    total = len(pages)
    total_calls = total * len(models)
    print(f"{'=' * 60}")
    print(f"[{_ts()}] Comparison starting")
    print(f"  Pages:    {total}")
    print(f"  Models:   {len(models)} ({', '.join(m.split('/')[-1] for m in models)})")
    print(f"  Total:    {total_calls} API calls")
    print(f"  Parallel: {max_workers} workers per page")
    print(f"  Timeout:  {timeout}s per LLM call")
    print(f"  Cluster:  {cluster} | Temperature: {temperature}")
    print(f"  CSV:      {csv_path}")
    print(f"{'=' * 60}")
    print()

    csv_lock = threading.Lock()
    rows = []
    done_count = 0
    error_count = 0
    timeout_count = 0
    run_start = time.time()

    for i, (kw, u) in enumerate(pages, 1):
        page_start = time.time()
        try:
            url_slug = _derive_url_slug(u)
        except ValueError as e:
            url_slug = ""
            error_msg = f"ERROR: {e}"
            print(f"\n[{_ts()}] [{i}/{total}] ✗ Bad URL: {u}")
            row = {
                "url": u,
                "keyword": kw,
                "url_slug": url_slug,
                "model_results": {sanitize_model_name(m): error_msg for m in models},
            }
            rows.append(row)
            error_count += len(models)
            with csv_lock:
                write_comparison_csv(rows, models, csv_path)
            continue

        print(f"\n[{_ts()}] [{i}/{total}] 📄 \"{kw}\"")
        print(f"  URL: {u}")
        sys.stdout.flush()

        row = {
            "url": u,
            "keyword": kw,
            "url_slug": url_slug,
            "model_results": {},
        }
        rows.append(row)

        # Run all models in parallel for this page
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _run_single_model,
                    model, kw, u, cluster, base_dir, temperature, timeout, i, total,
                ): model
                for model in models
            }

            for future in as_completed(futures):
                model_slug, result_content = future.result()
                row["model_results"][model_slug] = result_content

                # Update counters
                if result_content == "TIMEOUT":
                    timeout_count += 1
                elif result_content.startswith("ERROR:"):
                    error_count += 1
                else:
                    done_count += 1

                # Write CSV immediately after each model result
                with csv_lock:
                    write_comparison_csv(rows, models, csv_path)

        page_elapsed = time.time() - page_start
        page_done = sum(1 for v in row["model_results"].values() if not v.startswith("ERROR:") and v != "TIMEOUT")
        page_err = len(row["model_results"]) - page_done
        print(f"  [{_ts()}] Page done in {page_elapsed:.1f}s — {page_done} ok, {page_err} failed")
        sys.stdout.flush()

        # Rate limit delay between pages
        if i < total:
            print(f"  [{_ts()}] Waiting 3s before next page...")
            sys.stdout.flush()
            time.sleep(3)

    total_elapsed = time.time() - run_start
    print()
    print(f"{'=' * 60}")
    print(f"[{_ts()}] Comparison complete in {total_elapsed:.1f}s")
    print(f"  ✓ Done:    {done_count}")
    print(f"  ⏱ Timeout: {timeout_count}")
    print(f"  ✗ Error:   {error_count}")
    print(f"  CSV:       {csv_path}")
    print(f"{'=' * 60}")
    return csv_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare LLM model outputs for SEO content generation.")
    parser.add_argument(
        "--models",
        nargs="+",
        required=True,
        help="Model names to compare (space-separated, provider-prefixed). Min 2.",
    )
    parser.add_argument("--keyword", default=None, help="Target keyword (single-page mode)")
    parser.add_argument("--url", default=None, help="Target URL (single-page mode)")
    parser.add_argument("--cluster", default="group_annotate", help="Cluster name")
    parser.add_argument("--temperature", type=float, default=0.3, help="Sampling temperature")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout in seconds per LLM call (default: 30)")
    parser.add_argument("--workers", type=int, default=None, help="Max parallel workers per page (default: number of models)")
    args = parser.parse_args()

    try:
        run_comparison(
            models=args.models,
            keyword=args.keyword,
            url=args.url,
            cluster=args.cluster,
            temperature=args.temperature,
            timeout=args.timeout,
            max_workers=args.workers,
        )
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}")
        sys.exit(1)
