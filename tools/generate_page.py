"""
generate_page.py — Single-page orchestrator for the two-prompt LLM pipeline.

Loads config, assembles prompts, calls LLM twice via LiteLLM proxy, extracts blueprint,
saves outputs to output/{url-slug}/.

Usage (CLI):
    python tools/generate_page.py --keyword "Annotate Quitclaim Deed" \
        --url "https://www.pdffiller.com/en/document-management/quitclaim-deed-annotate"

    # With fallback: if the primary model fails, automatically try gpt-4o-mini
    python tools/generate_page.py --keyword "Annotate Quitclaim Deed" \
        --url "https://www.pdffiller.com/en/document-management/quitclaim-deed-annotate" \
        --model anthropic/claude-3-5-sonnet-20241022 \
        --fallback-models openai/gpt-4o-mini

Importable:
    from generate_page import generate_single_page
    result = generate_single_page(keyword="...", url="...")
    result = generate_single_page(keyword="...", url="...", model="anthropic/claude-3-5-sonnet-20241022",
                                  fallback_models=["openai/gpt-4o-mini"])
"""

import argparse
import os
import re
import sys
import time

import openai
from dotenv import load_dotenv
from openai import OpenAI

# Allow importing load_config from the same tools/ directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from load_config import load_config

# Process-level cache of discovered model capabilities.
# Keyed by model name; avoids repeated failed API calls to detect parameter support.
_MODEL_CAPS: dict = {}


def _default_base_dir():
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(tools_dir), "inputs")



def _call_llm_with_retry(
    client: OpenAI,
    prompt: str,
    model: str = "openai/gpt-4o-mini",
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> str:
    """Call LLM via LiteLLM proxy with exponential backoff retry on transient errors."""
    delays = [5, 15, 45]
    last_exc = None
    cached = _MODEL_CAPS.get(model, {})
    supports_temperature = cached.get("supports_temperature", True)
    use_max_completion_tokens = cached.get("use_max_completion_tokens", True)

    for attempt, delay in enumerate(delays + [None]):
        try:
            kwargs = dict(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
            if use_max_completion_tokens:
                kwargs["max_completion_tokens"] = max_tokens
            else:
                kwargs["max_tokens"] = max_tokens
            if supports_temperature:
                kwargs["temperature"] = temperature
            response = client.chat.completions.create(**kwargs)
            _MODEL_CAPS[model] = {
                "supports_temperature": supports_temperature,
                "use_max_completion_tokens": use_max_completion_tokens,
            }
            return response.choices[0].message.content
        except openai.RateLimitError as e:
            last_exc = e
            if delay is None:
                break
            print(f"  Rate limit (attempt {attempt + 1}/3): retrying in {delay}s...")
            time.sleep(delay)
        except openai.APIConnectionError as e:
            last_exc = e
            if delay is None:
                break
            print(f"  Connection error (attempt {attempt + 1}/3): retrying in {delay}s...")
            time.sleep(delay)
        except openai.APITimeoutError as e:
            last_exc = e
            if delay is None:
                break
            print(f"  Timeout (attempt {attempt + 1}/3): retrying in {delay}s...")
            time.sleep(delay)
        except openai.BadRequestError as e:
            err_str = str(e)
            # Some models don't support temperature — retry once without it
            # Check both e.param (standard) and the error body (some models omit param)
            if supports_temperature and (
                e.param == "temperature"
                or "'temperature'" in err_str
                or '"temperature"' in err_str
            ):
                supports_temperature = False
                continue
            # Some models expect max_tokens instead of max_completion_tokens
            if "max_completion_tokens" in err_str and use_max_completion_tokens:
                use_max_completion_tokens = False
                continue
            raise
        except openai.APIStatusError as e:
            # Only retry server-side errors (5xx); 4xx are non-retryable
            if e.status_code >= 500:
                last_exc = e
                if delay is None:
                    break
                print(f"  Server error {e.status_code} (attempt {attempt + 1}/3): retrying in {delay}s...")
                time.sleep(delay)
            else:
                raise

    raise last_exc


def _run_pipeline(
    client: OpenAI,
    model: str,
    prompt1: str,
    prompt2_template: str,
    config: dict,
    url_slug: str,
    slug_output_dir: str,
    temperature: float,
    return_raw: bool,
) -> dict:
    """
    Run both LLM prompts for a single model. Returns a result dict with status done|error.
    Called by generate_single_page() for each model in the fallback chain.
    """
    # --- Call LLM: Prompt 1 ---
    print(f"  Running Prompt 1 (Strategist)...")
    p1_response = _call_llm_with_retry(
        client, prompt1, model=model, temperature=temperature, max_tokens=4096
    )

    # --- Extract blueprint ---
    match = re.search(r"<blueprint>(.*?)</blueprint>", p1_response, re.DOTALL)
    if not match:
        if return_raw:
            return {
                "status": "error",
                "url_slug": url_slug,
                "blueprint": p1_response,
                "content": "",
                "blueprint_path": None,
                "content_path": None,
                "error": "No <blueprint> block found in Prompt 1 response",
            }
        os.makedirs(slug_output_dir, exist_ok=True)
        raw_path = os.path.join(slug_output_dir, "blueprint_raw.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(p1_response)
        return {
            "status": "error",
            "url_slug": url_slug,
            "blueprint_path": None,
            "content_path": None,
            "error": f"No <blueprint> block found. Raw response saved to {raw_path}",
        }

    blueprint = match.group(1).strip()

    # --- Save blueprint (skip in return_raw mode) ---
    blueprint_path = None
    if not return_raw:
        blueprint_path = os.path.join(slug_output_dir, "blueprint.md")
        with open(blueprint_path, "w", encoding="utf-8") as f:
            f.write(blueprint)

    # --- Assemble Prompt 2 ---
    prompt2 = prompt2_template
    prompt2 = prompt2.replace("{{BLUEPRINT}}", blueprint)
    prompt2 = prompt2.replace("{{GLOBAL_CONFIG}}", config["GLOBAL_CONFIG"])
    prompt2 = prompt2.replace("{{LOCALE_CONFIG}}", config.get("LOCALE_CONFIG", ""))

    # --- Call LLM: Prompt 2 ---
    print(f"  Running Prompt 2 (Writer)...")
    p2_response = _call_llm_with_retry(
        client, prompt2, model=model, temperature=temperature, max_tokens=4096
    )

    # --- Strip markdown code fence if LLM wrapped output in ```html ... ``` ---
    p2_response = p2_response.strip()
    p2_response = re.sub(r"^```(?:html)?\s*\n?", "", p2_response)
    p2_response = re.sub(r"\n?```\s*$", "", p2_response).strip()

    # --- Validate HTML output ---
    if "<h2>" not in p2_response:
        if return_raw:
            return {
                "status": "error",
                "url_slug": url_slug,
                "blueprint": blueprint,
                "content": "",
                "blueprint_path": None,
                "content_path": None,
                "error": "Prompt 2 response contains no <h2> tag — likely malformed output",
            }
        return {
            "status": "error",
            "url_slug": url_slug,
            "blueprint_path": blueprint_path,
            "content_path": None,
            "error": "Prompt 2 response contains no <h2> tag — likely malformed output",
        }

    # --- Return raw strings (skip file I/O in return_raw mode) ---
    if return_raw:
        return {
            "status": "done",
            "url_slug": url_slug,
            "blueprint": blueprint,
            "content": p2_response,
            "blueprint_path": None,
            "content_path": None,
            "error": None,
        }

    # --- Save HTML ---
    content_path = os.path.join(slug_output_dir, "content.html")
    with open(content_path, "w", encoding="utf-8") as f:
        f.write(p2_response)

    return {
        "status": "done",
        "url_slug": url_slug,
        "blueprint_path": blueprint_path,
        "content_path": content_path,
        "error": None,
    }


def generate_single_page(
    keyword: str,
    url: str,
    cluster: str = "group_annotate",
    base_dir: str = None,
    output_dir: str = None,
    model: str = "openai/gpt-4o-mini",
    fallback_models: list = None,
    temperature: float = 0.3,
    timeout: int = None,
    config_cache: dict = None,
    return_raw: bool = False,
    locale: str = "en",
) -> dict:
    """
    Run the full two-prompt pipeline for a single page.

    If the primary model fails all retries, each model in fallback_models is tried
    in order. fallback_models=None (default) means no fallback — existing behavior.

    When return_raw=False (default): saves blueprint.md and content.html to disk,
    returns paths. Used by generate_page CLI (single-page mode).

    When return_raw=True: skips file I/O, returns raw blueprint and content strings.
    Used by batch_generate for CSV output.

    Returns:
        {
            "status": "done" | "error",
            "model_used": str | None,       # which model produced the output (None on total failure)
            "url_slug": str,
            "blueprint_path": str | None,   # only set when return_raw=False
            "content_path": str | None,     # only set when return_raw=False
            "blueprint": str | None,        # only set when return_raw=True
            "content": str | None,          # only set when return_raw=True
            "error": str | None
        }
    """
    url_slug = None

    try:
        # --- Setup ---
        if base_dir is None:
            base_dir = _default_base_dir()
        if output_dir is None:
            output_dir = os.path.join(os.path.dirname(base_dir), "output")

        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        load_dotenv(env_path)
        api_key = os.environ.get("LITELLM_API_KEY")
        base_url = os.environ.get("LITELLM_PROXY_URL")
        if not api_key:
            raise ValueError("LITELLM_API_KEY not set in .env")
        if not base_url:
            raise ValueError("LITELLM_PROXY_URL not set in .env")
        client_kwargs = dict(api_key=api_key, base_url=base_url)
        if timeout is not None:
            client_kwargs["timeout"] = float(timeout)
        client = OpenAI(**client_kwargs)

        # --- Load config ---
        # If a pre-loaded cache is provided (e.g. from a batch run), reuse the stable
        # fields and only recompute the two values that change per page.
        if config_cache is not None:
            from urllib.parse import urlparse
            import json
            url_slug = urlparse(url).path.rstrip("/").split("/")[-1]
            if not url_slug:
                raise ValueError(f"Could not derive a url_slug from URL: {url!r}")
            config = dict(config_cache)
            config["url_slug"] = url_slug
            config["PAGE_CONFIG"] = json.dumps({"keyword": keyword, "url": url}, ensure_ascii=False)
        else:
            config = load_config(cluster=cluster, keyword=keyword, url=url, base_dir=base_dir, locale=locale)
        url_slug = config["url_slug"]
        slug_output_dir = os.path.join(output_dir, url_slug)
        if not return_raw:
            os.makedirs(slug_output_dir, exist_ok=True)

        # --- Read prompt templates (use cache if available) ---
        if config_cache is not None and "PROMPT_1_TEMPLATE" in config_cache:
            prompt1_template = config_cache["PROMPT_1_TEMPLATE"]
            prompt2_template = config_cache["PROMPT_2_TEMPLATE"]
        else:
            prompt1_path = os.path.join(base_dir, "prompt_1.md")
            prompt2_path = os.path.join(base_dir, "prompt_2.md")
            with open(prompt1_path, "r", encoding="utf-8") as f:
                prompt1_template = f.read()
            with open(prompt2_path, "r", encoding="utf-8") as f:
                prompt2_template = f.read()

        # --- Assemble Prompt 1 (model-agnostic — done once for all fallbacks) ---
        prompt1 = prompt1_template
        prompt1 = prompt1.replace("{{GLOBAL_CONFIG}}", config["GLOBAL_CONFIG"])
        prompt1 = prompt1.replace("{{LOCALE_CONFIG}}", config.get("LOCALE_CONFIG", ""))
        prompt1 = prompt1.replace("{{CLUSTER_CONFIG}}", config["CLUSTER_CONFIG"])
        prompt1 = prompt1.replace("{{PAGE_CONFIG}}", config["PAGE_CONFIG"])
        prompt1 = prompt1.replace("{{SECTION_MENU}}", config["SECTION_MENU"])

        # --- Fallback loop: try primary model, then each fallback in order ---
        models_to_try = [model] + (fallback_models or [])
        last_result = None

        for active_model in models_to_try:
            if active_model != model:
                print(f"  Falling back to {active_model}...")

            try:
                result = _run_pipeline(
                    client=client,
                    model=active_model,
                    prompt1=prompt1,
                    prompt2_template=prompt2_template,
                    config=config,
                    url_slug=url_slug,
                    slug_output_dir=slug_output_dir,
                    temperature=temperature,
                    return_raw=return_raw,
                )
            except Exception as e:
                result = {
                    "status": "error",
                    "url_slug": url_slug,
                    "blueprint_path": None,
                    "content_path": None,
                    "error": str(e),
                }
                if return_raw:
                    result["blueprint"] = ""
                    result["content"] = ""

            last_result = result

            if result["status"] == "done":
                result["model_used"] = active_model
                return result

            # Model failed — report and try next if available
            if active_model != models_to_try[-1]:
                print(f"  ✗ {active_model.split('/')[-1]} failed: {result.get('error', 'unknown error')}")

        # All models exhausted
        last_result["model_used"] = None
        return last_result

    except Exception as e:
        result = {
            "status": "error",
            "model_used": None,
            "url_slug": url_slug,
            "blueprint_path": None,
            "content_path": None,
            "error": str(e),
        }
        if return_raw:
            result["blueprint"] = ""
            result["content"] = ""
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a single SEO content page.")
    parser.add_argument("--keyword", required=True, help="Target keyword")
    parser.add_argument("--url", required=True, help="Target page URL")
    parser.add_argument("--cluster", default="group_annotate", help="Cluster name")
    parser.add_argument("--model", default="openai/gpt-4o-mini", help="Primary model name (e.g. openai/gpt-4o-mini, anthropic/claude-3-5-sonnet-20241022)")
    parser.add_argument(
        "--fallback-models",
        nargs="+",
        default=None,
        metavar="MODEL",
        help="Fallback models tried in order if the primary fails (space-separated, e.g. openai/gpt-4o-mini gemini/gemini-1.5-flash)",
    )
    parser.add_argument("--temperature", type=float, default=0.3, help="Sampling temperature")
    parser.add_argument("--output-dir", default=None, help="Override output directory")
    parser.add_argument("--locale", default="en", help="Locale code (e.g. en, fr, de, es, pt-BR, nl, it)")
    parser.add_argument("--timeout", type=int, default=None, help="Timeout in seconds per model call (default: no timeout)")
    args = parser.parse_args()

    fallback_info = f" → fallback: {', '.join(args.fallback_models)}" if args.fallback_models else ""
    print(f"Generating page for: {args.keyword}")
    print(f"  Model: {args.model}{fallback_info} | Temperature: {args.temperature} | Locale: {args.locale}")
    result = generate_single_page(
        args.keyword,
        args.url,
        args.cluster,
        output_dir=args.output_dir,
        model=args.model,
        fallback_models=args.fallback_models,
        temperature=args.temperature,
        timeout=args.timeout,
        locale=args.locale,
    )

    if result["status"] == "done":
        used = result.get("model_used", args.model)
        fallback_note = f" (fallback: {used})" if used != args.model else ""
        print(f"Done{fallback_note}.")
        print(f"  Blueprint: {result['blueprint_path']}")
        print(f"  Content:   {result['content_path']}")
    else:
        print(f"Error: {result['error']}")
        sys.exit(1)
