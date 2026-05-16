#!/usr/bin/env python3
"""
Real Document Downloader for biologics-decision-engine benchmarks.

Downloads publicly available regulatory documents from FDA, EMA, NIST, and ICH
for use as real-world test cases.

Usage:
    python benchmarks/real_documents/download.py
    python benchmarks/real_documents/download.py --doc-id NISTMAB-CHAR-001
    python benchmarks/real_documents/download.py --list

Documents are cached locally. Re-running is safe — existing files are skipped.
"""

import argparse
import hashlib
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install pyyaml --break-system-packages")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests --break-system-packages")
    sys.exit(1)

MANIFEST_PATH = Path(__file__).parent / "MANIFEST.yaml"
DOWNLOAD_DIR  = Path(__file__).parent


def load_manifest():
    with open(MANIFEST_PATH) as f:
        return yaml.safe_load(f)


def download_document(doc, dest_dir: Path, force: bool = False) -> bool:
    dest = dest_dir / doc["filename"]

    if dest.exists() and not force:
        size_kb = dest.stat().st_size // 1024
        print(f"  SKIP  {doc['id']} — already downloaded ({size_kb} KB): {dest.name}")
        return True

    print(f"  GET   {doc['id']} — {doc['url']}")
    print(f"        → {dest.name}")

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; biologics-decision-engine/1.0; "
                "research use; https://github.com/)"
            )
        }
        resp = requests.get(doc["url"], headers=headers, timeout=120, stream=True)
        resp.raise_for_status()

        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    print(f"\r        {pct:5.1f}%  ({downloaded // 1024} KB)", end="", flush=True)

        print(f"\r        DONE  ({downloaded // 1024} KB)            ")

        # Verify it's actually a PDF
        with open(dest, "rb") as f:
            header = f.read(4)
        if header != b"%PDF":
            print(f"  WARN  {dest.name} does not appear to be a valid PDF (header: {header!r})")

        return True

    except requests.exceptions.RequestException as e:
        print(f"  FAIL  {doc['id']} — {e}")
        if dest.exists():
            dest.unlink()
        return False


def list_documents(manifest):
    print(f"\n{'ID':<30} {'Type':<25} {'File':<45} {'Status'}")
    print("-" * 120)
    docs = manifest.get("documents", [])
    dest_dir = DOWNLOAD_DIR
    for doc in docs:
        dest = dest_dir / doc["filename"]
        status = f"OK ({dest.stat().st_size // 1024} KB)" if dest.exists() else "NOT DOWNLOADED"
        print(f"{doc['id']:<30} {doc['document_type']:<25} {doc['filename']:<45} {status}")
    print(f"\nTotal: {len(docs)} documents\n")


def main():
    parser = argparse.ArgumentParser(description="Download real benchmark documents")
    parser.add_argument("--doc-id", help="Download only this document ID")
    parser.add_argument("--list", action="store_true", help="List all documents and status")
    parser.add_argument("--force", action="store_true", help="Re-download even if file exists")
    args = parser.parse_args()

    manifest = load_manifest()
    docs = manifest.get("documents", [])

    if args.list:
        list_documents(manifest)
        return

    if args.doc_id:
        docs = [d for d in docs if d["id"] == args.doc_id]
        if not docs:
            print(f"ERROR: No document with id '{args.doc_id}'")
            sys.exit(1)

    print(f"\nDownloading {len(docs)} document(s) to {DOWNLOAD_DIR}\n")
    success, failed = 0, 0
    for doc in docs:
        ok = download_document(doc, DOWNLOAD_DIR, force=args.force)
        if ok:
            success += 1
        else:
            failed += 1
        time.sleep(0.5)  # polite delay between requests

    print(f"\nDone: {success} succeeded, {failed} failed")
    if failed:
        print("Failed documents can be downloaded manually — see MANIFEST.yaml for URLs")
        sys.exit(1)


if __name__ == "__main__":
    main()
