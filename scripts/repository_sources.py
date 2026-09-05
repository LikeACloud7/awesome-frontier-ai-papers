"""Paginated official repository discovery with a persistent rotating scan queue."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

import fetch_papers as f
from collection_status import ACTIVE_SOURCE, http_get, record_source_error, record_source_limit


def repository_inventory(org: dict):
    settings = org.get("official_repositories", {})
    repositories = {}
    for owner in settings.get("huggingface_orgs", []):
        try:
            url = "https://huggingface.co/api/models"
            params = {"author": owner, "limit": 100, "sort": "lastModified", "direction": -1}
            visited = set()
            while url:
                response = f.huggingface_request("GET", url, params=params)
                signature = response.url
                if signature in visited:
                    raise ValueError(f"Hugging Face model inventory repeated: {owner}")
                visited.add(signature)
                for item in response.json():
                    repositories["hf:" + item["id"]] = item.get("lastModified", "")
                url = response.links.get("next", {}).get("url")
                params = None
        except Exception as error:
            record_source_error(error)
    for owner in settings.get("github_orgs", []):
        try:
            page = 1
            endpoint = f"https://api.github.com/orgs/{owner}/repos"
            while True:
                response = http_get(endpoint, headers=f.github_headers(), params={
                    "type": "public", "sort": "pushed", "direction": "desc", "per_page": 100, "page": page})
                if response.status_code == 404 and page == 1 and "/orgs/" in endpoint:
                    endpoint = f"https://api.github.com/users/{owner}/repos"
                    continue
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, list):
                    raise ValueError(f"Invalid GitHub repository inventory for {owner}")
                for item in data:
                    if not item.get("fork"):
                        repositories["gh:" + item["full_name"]] = item.get("pushed_at", "")
                if len(data) < 100:
                    break
                page += 1
        except Exception as error:
            record_source_error(error)
    for name in settings.get("huggingface", []):
        repositories.setdefault("hf:" + name, "explicit")
    for name in settings.get("github", []):
        repositories.setdefault("gh:" + name, "explicit")
    return repositories


def collect_repository_reports(config: dict, org: dict, days: int, since: str | None = None):
    settings = config.get("company_tracking", {}).get("official_reports", {})
    root = f.OUTPUT_DIR / "repository_scans"
    root.mkdir(parents=True, exist_ok=True)
    path = root / (hashlib.sha256(org["name"].encode()).hexdigest()[:16] + ".json")
    state = json.loads(path.read_text()) if path.exists() else {"records": {}, "candidates": {}}
    try:
        inventory = repository_inventory(org)
    except Exception as error:
        record_source_error(error)
        inventory = state.get("inventory", {})
    inventory = {**state.get("inventory", {}), **inventory}
    state["inventory"] = inventory
    records = state.setdefault("records", {})
    candidates = state.setdefault("candidates", {})
    pending = [key for key, changed in inventory.items()
               if key not in records or records[key].get("changed") != changed]
    # Rotate failures too, so one inaccessible repository cannot starve the rest.
    pending.sort(key=lambda key: records.get(key, {}).get("last_attempt", ""))
    budget = int(settings.get("repositories_per_run", 30))
    for key in pending[:budget] if budget else pending:
        name = key[3:]
        previous = records.get(key, {})
        try:
            status = ACTIVE_SOURCE.get()
            errors_before = len(status["errors"]) if status is not None else 0
            if key.startswith("hf:"):
                papers = f.fetch_huggingface_repo_reports(org, name)
                readme_url = f"https://huggingface.co/{name}/raw/main/README.md"
            else:
                papers = f.fetch_github_repo_reports(org, name)
                readme_url = f"https://raw.githubusercontent.com/{name}/HEAD/README.md"
            if status is not None and len(status["errors"]) > errors_before:
                raise RuntimeError(f"Repository scan incomplete: {name}; retained for retry")
            readme = http_get(readme_url, timeout=30)
            if readme.status_code not in {200, 404}:
                readme.raise_for_status()
            if readme.status_code == 200:
                for aid in re.findall(r"(?:arxiv\.org/(?:abs|pdf)/|huggingface\.co/papers/)(\d{4}\.\d{4,5})", readme.text):
                    candidates[aid] = {"source": readme_url}
            records[key] = {"changed": inventory[key], "papers": papers,
                "last_attempt": datetime.now().isoformat(timespec="seconds")}
        except Exception as error:
            record_source_error(error)
            records[key] = {**previous, "last_attempt": datetime.now().isoformat(timespec="seconds")}
    remaining = sum(key not in records or records[key].get("changed") != changed for key, changed in inventory.items())
    if remaining:
        record_source_limit(f"{remaining} official repositories remain in the persistent scan queue")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False))
    temporary.replace(path)
    papers = [f.official_report_to_paper(report, org) for report in org.get("official_reports", [])]
    for entry in records.values():
        papers.extend(entry.get("papers", []))
    # Cached records are intentionally returned regardless of repository creation
    # date; an old repository can publish a new paper years after creation.
    return papers
