"""CLI: `python -m doc_ingest parse INPUT... -o out.jsonl` / `inspect FILE`.

Erlaubt den Aufruf aus sprachfremden Programmen per Subprocess (SPEC §6/§7).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import IngestConfig, ingest_batch


def _cfg(args) -> IngestConfig:
    return IngestConfig(
        ocr=args.ocr,
        child_tokens=args.child_tokens,
        parent_tokens=args.parent_tokens,
        tokenizer=args.tokenizer,
        artifacts_path=args.artifacts_path,
        lang_detect=not args.no_lang,
        offline=not args.online,
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="doc-ingest")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("parse", help="Datei(en) -> JSONL (document/parent/child)")
    p.add_argument("inputs", nargs="+")
    p.add_argument("-o", "--output", default="-")
    p.add_argument("--ocr", default="off", choices=["auto", "force", "off"])
    p.add_argument("--child-tokens", type=int, default=256)
    p.add_argument("--parent-tokens", type=int, default=1024)
    p.add_argument("--tokenizer", default="BAAI/bge-m3")
    p.add_argument("--artifacts-path", default=None)
    p.add_argument("--no-lang", action="store_true")
    p.add_argument("--online", action="store_true",
                   help="Offline-Zwang aus (fuer Dev/Tokenizer-Download); Prod backt Modelle+Tokenizer")

    ins = sub.add_parser("inspect", help="Dry-Run: Format + geplanter Pfad")
    ins.add_argument("input")

    args = ap.parse_args(argv)

    if args.cmd == "inspect":
        pth = Path(args.input)
        print(json.dumps({
            "input": str(pth),
            "exists": pth.exists(),
            "suffix": pth.suffix.lower(),
        }, ensure_ascii=False))
        return 0

    results = ingest_batch(args.inputs, _cfg(args))
    out = sys.stdout if args.output == "-" else open(args.output, "w", encoding="utf-8")
    try:
        for r in results:
            for rec in r.iter_records():
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    finally:
        if out is not sys.stdout:
            out.close()
    # Report-Zusammenfassung auf stderr (nicht in den JSONL-Stream)
    for r in results:
        print(f"[report] {r.document.get('metadata', {}).get('source', r.document.get('source'))}: "
              f"{r.report}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
