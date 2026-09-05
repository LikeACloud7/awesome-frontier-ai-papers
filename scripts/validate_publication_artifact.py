"""Validate data-only artifacts before a separate job can publish them.

Uses only the standard library and trusted repository code. Never imports a
collector, parses a PDF, evaluates input, or follows a URL.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import shutil

from security import safe_link, safe_slug

FILES = {"company_papers.json", "collection_health.json", "collection_pending.json", "collection_state.json"}
MAX_FILE_BYTES = 96 * 1024 * 1024
ROOT = Path(__file__).resolve().parent.parent


def load_bounded(path: Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"Invalid artifact file: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite JSON number")))


def validate(directory: Path, config_path: Path = ROOT / "config/frontier_labs.json"):
    directory = directory.resolve()
    entries = list(directory.iterdir())
    if {entry.name for entry in entries} != FILES:
        raise ValueError("Publication artifact must contain exactly the four expected JSON files")
    if any(entry.is_symlink() or not entry.is_file() for entry in entries):
        raise ValueError("Symlinks and directories are forbidden in publication artifacts")
    values = {name: load_bounded(directory / name) for name in FILES}
    config = json.loads(config_path.read_text())
    known = {}
    for group in config["company_tracking"]["groups"]:
        for org in group["organizations"]:
            known[org["name"]] = {"id":org["name"].lower().replace("/", "-").replace(".", "").replace(" ", "-"), "region":group["region"]}
    data = values["company_papers.json"]
    if not isinstance(data, dict) or not isinstance(data.get("papers"), list) or len(data["papers"]) > 200000:
        raise ValueError("Invalid paper dataset")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:+.-]+Z?", data.get("generated_at", "")):
        raise ValueError("Invalid generation timestamp")
    counts, identifiers = Counter(), set()
    for paper in data["papers"]:
        if not isinstance(paper, dict):
            raise ValueError("Invalid paper record")
        for key, limit in (("id",8192), ("title",4000), ("abstract",16000), ("source",200)):
            if not isinstance(paper.get(key, ""), str) or len(paper.get(key, "")) > limit:
                raise ValueError(f"Invalid paper field: {key}")
        if not paper.get("id") or paper["id"] in identifiers or not paper.get("title"):
            raise ValueError("Missing or duplicate paper identity")
        identifiers.add(paper["id"])
        urls = [paper.get("url", "")] + [u for u in [paper.get("paper_url", "")] if u] + paper.get("alternate_urls", [])
        if not all(safe_link(url) for url in urls):
            raise ValueError("Unsafe publication URL")
        labs = paper.get("companies")
        if not isinstance(labs, list) or not labs or len(set(labs)) != len(labs) or any(lab not in known for lab in labs):
            raise ValueError("Invalid paper lab attribution")
        counts.update(labs)
        for field in ("authors", "sources", "matched_keywords", "author_affiliations"):
            items = paper.get(field, [])
            if not isinstance(items, list) or len(items) > 2000 or any(not isinstance(s, str) or len(s) > 8192 for s in items):
                raise ValueError(f"Invalid list field: {field}")
        if not re.fullmatch(r"(?:\d{4}(?:-\d{2}(?:-\d{2})?)?)?", paper.get("published", "")):
            raise ValueError("Invalid publication date")
    companies = data.get("companies", [])
    if len(companies) != len(known) or {c["name"] for c in companies} != set(known):
        raise ValueError("Unexpected lab registry in artifact")
    for company in companies:
        expected = known[company["name"]]
        if safe_slug(company["id"]) != expected["id"] or company.get("region") != expected["region"]:
            raise ValueError("Invalid lab identifier or region")
        if company["paper_count"] != counts[company["name"]]:
            raise ValueError("Lab count does not match publications")
        if not re.fullmatch(r"(?:\d{4}(?:-\d{2}(?:-\d{2})?)?)?", company.get("latest_paper_date", "")):
            raise ValueError("Invalid lab date")
    totals = data.get("totals", {})
    if totals.get("papers") != len(identifiers) or totals.get("tracked_companies") != len(known) or totals.get("companies") != len(counts):
        raise ValueError("Archive totals do not match data")
    health = values["collection_health.json"]
    if data.get("collection") != health or health.get("status") not in {"ok", "partial"}:
        raise ValueError("Collection health does not match archive")
    for field in ("error_sources", "failed_sources", "partial_sources", "pending_metadata"):
        if type(health.get(field, 0)) is not int or health.get(field, 0) < 0:
            raise ValueError("Invalid collection health counter")
    if not isinstance(values["collection_pending.json"], list) or not isinstance(values["collection_state.json"], dict):
        raise ValueError("Invalid collection state")
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--copy-to", type=Path)
    args = parser.parse_args()
    validate(args.directory)
    if args.copy_to:
        args.copy_to.mkdir(parents=True, exist_ok=True)
        for name in sorted(FILES):
            target = args.copy_to / name
            if target.is_symlink():
                raise ValueError("Refusing a symlink destination")
            shutil.copyfile(args.directory / name, target)
    print("Publication artifact validated")


if __name__ == "__main__":
    main()
