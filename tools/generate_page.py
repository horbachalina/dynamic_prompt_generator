"""
generate_page.py — Single-page orchestrator for the two-prompt LLM pipeline.

Loads config, assembles prompts, calls OpenAI twice, extracts blueprint,
saves outputs to .tmp/output/{url-slug}/.

Usage (CLI):
    python tools/generate_page.py --keyword "Annotate Quitclaim Deed" \
        --url "https://www.pdffiller.com/en/document-management/quitclaim-deed-annotate"

Importable:
    from generate_page import generate_single_page
    result = generate_single_page(keyword="...", url="...")
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


def _default_base_dir():
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(tools_dir), ".tmp")



def _call_llm_with_retry(
    client: OpenAI,
    prompt: str,
    model: str = "gpt-4o",
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> str:
    """Call OpenAI with exponential backoff retry on transient errors."""
    delays = [5, 15, 45]
    last_exc = None

    for attempt, delay in enumerate(delays + [None]):
        try:
            response = client.chat.completions.create(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content
        except (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APIStatusError,
        ) as e:
            last_exc = e
            if delay is None:
                break
            print(f"  API error (attempt {attempt + 1}/3): {e}. Retrying in {delay}s...")
            time.sleep(delay)

    raise last_exc


def generate_single_page(
    keyword: str,
    url: str,
    cluster: str = "group_annotate",
    base_dir: str = None,
    output_dir: str = None,
) -> dict:
    """
    Run the full two-prompt pipeline for a single page.

    Returns:
        {
            "status": "done" | "error",
            "url_slug": str,
            "blueprint_path": str | None,
            "content_path": str | None,
            "error": str | None
        }
    """
    url_slug = None

    try:
        # --- Setup ---
        if base_dir is None:
            base_dir = _default_base_dir()
        if output_dir is None:
            output_dir = os.path.join(base_dir, "output")

        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        load_dotenv(env_path)
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set in .env")
        client = OpenAI(api_key=api_key)

        # --- Load config ---
        config = load_config(cluster=cluster, keyword=keyword, url=url, base_dir=base_dir)
        url_slug = config["url_slug"]
        slug_output_dir = os.path.join(output_dir, url_slug)
        os.makedirs(slug_output_dir, exist_ok=True)

        # --- Read and clean prompt templates ---
        prompt1_path = os.path.join(base_dir, "prompt_1.md")
        prompt2_path = os.path.join(base_dir, "prompt_2.md")

        with open(prompt1_path, "r", encoding="utf-8") as f:
            prompt1_template = f.read()
        with open(prompt2_path, "r", encoding="utf-8") as f:
            prompt2_template = f.read()

        # --- Assemble Prompt 1 ---
        prompt1 = prompt1_template
        prompt1 = prompt1.replace("{{GLOBAL_CONFIG}}", config["GLOBAL_CONFIG"])
        prompt1 = prompt1.replace("{{CLUSTER_CONFIG}}", config["CLUSTER_CONFIG"])
        prompt1 = prompt1.replace("{{SECTION_MENU}}", config["SECTION_MENU"])
        prompt1 = prompt1.replace("{{PAGE_CONFIG}}", config["PAGE_CONFIG"])

        # --- Call LLM: Prompt 1 ---
        print(f"  Running Prompt 1 (Strategist)...")
        p1_response = _call_llm_with_retry(
            client, prompt1, model="gpt-4o", temperature=0.7, max_tokens=4096
        )

        # --- Extract blueprint ---
        match = re.search(r"<blueprint>(.*?)</blueprint>", p1_response, re.DOTALL)
        if not match:
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

        # --- Save blueprint ---
        blueprint_path = os.path.join(slug_output_dir, "blueprint.md")
        with open(blueprint_path, "w", encoding="utf-8") as f:
            f.write(blueprint)

        # --- Assemble Prompt 2 ---
        prompt2 = prompt2_template
        prompt2 = prompt2.replace("{{BLUEPRINT}}", blueprint)
        prompt2 = prompt2.replace("{{SHARED_RULES}}", config["SHARED_RULES"])

        # --- Call LLM: Prompt 2 ---
        print(f"  Running Prompt 2 (Writer)...")
        p2_response = _call_llm_with_retry(
            client, prompt2, model="gpt-4o", temperature=0.7, max_tokens=8192
        )

        # --- Strip markdown code fence if LLM wrapped output in ```html ... ``` ---
        p2_response = p2_response.strip()
        p2_response = re.sub(r"^```(?:html)?\s*\n?", "", p2_response)
        p2_response = re.sub(r"\n?```\s*$", "", p2_response).strip()

        # --- Validate HTML output ---
        if "<h2>" not in p2_response:
            return {
                "status": "error",
                "url_slug": url_slug,
                "blueprint_path": blueprint_path,
                "content_path": None,
                "error": "Prompt 2 response contains no <h2> tag — likely malformed output",
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

    except Exception as e:
        return {
            "status": "error",
            "url_slug": url_slug,
            "blueprint_path": None,
            "content_path": None,
            "error": str(e),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a single SEO content page.")
    parser.add_argument("--keyword", required=True, help="Target keyword")
    parser.add_argument("--url", required=True, help="Target page URL")
    parser.add_argument("--cluster", default="group_annotate", help="Cluster name")
    args = parser.parse_args()

    print(f"Generating page for: {args.keyword}")
    result = generate_single_page(args.keyword, args.url, args.cluster)

    if result["status"] == "done":
        print(f"Done.")
        print(f"  Blueprint: {result['blueprint_path']}")
        print(f"  Content:   {result['content_path']}")
    else:
        print(f"Error: {result['error']}")
        sys.exit(1)
