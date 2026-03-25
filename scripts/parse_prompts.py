#!/usr/bin/env python3
"""Parse prompts/queue.md into a JSON array with priority and dependency metadata.

Usage:
    python scripts/parse_prompts.py prompts/queue.md
    python scripts/parse_prompts.py prompts/queue.md --filter PRIORITY
    python scripts/parse_prompts.py prompts/queue.md --split

Output (default): JSON array to stdout.
Output (--split): Four JSON arrays to stdout as a single JSON object with keys:
    priority_prompts, independent_prompts, conditional_prompts, low_priority_prompts
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional


PRIORITY_TAGS = {"PRIORITY", "INDEPENDENT", "IF-PREVIOUS-PASSED", "LOW-PRIORITY"}

# Regex for ## [TAG] Title headings
HEADING_RE = re.compile(r"^##\s+\[([A-Z\-]+)\]\s+(.+)$", re.MULTILINE)

# Regex to extract parent dependency from IF-PREVIOUS-PASSED prompts
# Matches: "Take <Name>" or "take <Name>" at the start of the content
DEPENDENCY_RE = re.compile(r"\bTake\s+([A-Za-z][A-Za-z0-9 _\-]+?)(?:\s*[,\.]|$|\s+and\b)", re.IGNORECASE)


def parse_queue_file(path: Path) -> list[dict]:
    """Parse a markdown queue file and return a list of prompt dicts."""
    if not path.exists():
        print(f"ERROR: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    text = path.read_text(encoding="utf-8")

    # Find all headings with positions
    heading_matches = list(HEADING_RE.finditer(text))

    if not heading_matches:
        return []

    prompts = []
    for i, match in enumerate(heading_matches):
        tag = match.group(1).strip().upper()
        title = match.group(2).strip()

        # Content is everything between this heading and the next heading (or EOF)
        content_start = match.end()
        if i + 1 < len(heading_matches):
            content_end = heading_matches[i + 1].start()
        else:
            content_end = len(text)

        content = text[content_start:content_end].strip()

        # Determine depends_on for conditional prompts
        depends_on: Optional[str] = None
        if tag == "IF-PREVIOUS-PASSED":
            dep_match = DEPENDENCY_RE.search(content)
            if dep_match:
                depends_on = dep_match.group(1).strip()

        if tag in PRIORITY_TAGS:
            prompts.append(
                {
                    "title": title,
                    "content": content,
                    "priority": tag,
                    "depends_on": depends_on,
                }
            )

    return prompts


def split_by_priority(prompts: list[dict]) -> dict[str, list[dict]]:
    """Split prompts into four lists by priority tag."""
    result: dict[str, list[dict]] = {
        "priority_prompts": [],
        "independent_prompts": [],
        "conditional_prompts": [],
        "low_priority_prompts": [],
    }
    for prompt in prompts:
        tag = prompt["priority"]
        if tag == "PRIORITY":
            result["priority_prompts"].append(prompt)
        elif tag == "INDEPENDENT":
            result["independent_prompts"].append(prompt)
        elif tag == "IF-PREVIOUS-PASSED":
            result["conditional_prompts"].append(prompt)
        elif tag == "LOW-PRIORITY":
            result["low_priority_prompts"].append(prompt)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse prompts/queue.md into JSON")
    parser.add_argument("queue_file", type=Path, help="Path to the queue markdown file")
    parser.add_argument(
        "--filter",
        choices=list(PRIORITY_TAGS),
        default=None,
        help="Only output prompts with this priority tag",
    )
    parser.add_argument(
        "--split",
        action="store_true",
        help="Output a JSON object with four separate arrays by priority",
    )
    args = parser.parse_args()

    prompts = parse_queue_file(args.queue_file)

    if args.split:
        output = split_by_priority(prompts)
        print(json.dumps(output, indent=2))
        return

    if args.filter:
        prompts = [p for p in prompts if p["priority"] == args.filter]

    print(json.dumps(prompts, indent=2))


if __name__ == "__main__":
    main()
