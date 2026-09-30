#!/usr/bin/env python3
"""Regenerate the notebook's embedded copies of repository files.

    python3 tools/sync_notebook.py           # rewrite card_testing_lab.ipynb in place
    python3 tools/sync_notebook.py --check   # exit 1 if the notebook is out of sync

The notebook embeds card_testing_lab.py, requirements.txt and card_testing_rules.spl as
string literals so it can run standalone in Colab. Editing those sources without running
this script leaves the notebook stale. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTEBOOK = ROOT / "card_testing_lab.ipynb"
EMBEDS = [("SOURCE", ROOT / "card_testing_lab.py"),
          ("REQUIREMENTS", ROOT / "requirements.txt"),
          ("RULES", ROOT / "splunk" / "card_testing_rules.spl")]


def sync(notebook: dict, variable: str, text: str) -> bool:
    """Update the `variable = '...'` line in whichever cell embeds it. True if changed."""
    for cell in notebook["cells"]:
        for i, line in enumerate(cell.get("source", [])):
            if line.startswith(f"{variable} = "):
                expected = f"{variable} = {text!r}\n"
                if line == expected:
                    return False
                cell["source"][i] = expected
                return True
    raise AssertionError(f"no notebook cell embeds {variable}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report drift without rewriting the notebook")
    args = parser.parse_args()
    notebook = json.loads(NOTEBOOK.read_text())
    changed = [variable for variable, source in EMBEDS if sync(notebook, variable, source.read_text())]
    if args.check:
        if changed:
            sys.exit(f"Notebook is out of sync: {', '.join(changed)}. Run: python3 tools/sync_notebook.py")
        print("Notebook embeds match the source files.")
        return
    if changed:
        NOTEBOOK.write_text(json.dumps(notebook, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
        print(f"Notebook updated: {', '.join(changed)}.")
    else:
        print("Notebook already in sync.")


if __name__ == "__main__":
    main()
