import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_papers as f
import publication_sources as sources
import update_company_papers as updater
import affiliation_discovery as affiliations
import repository_sources as repositories
from collection_status import collect_with_status, record_source_error


def paper(title="Research on language models", **overrides):
    return {"id": "test:1", "title": title, "url": "https://example.org/paper", "published": "2025-01-01",
            "authors": ["Researcher One"], "abstract": "A study of large language models.",
            "matched_orgs": ["OpenAI"], "companies": ["OpenAI"], "source": "official_publication_page", **overrides}


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.config = f.load_config()
        self.registry = f.get_company_registry(self.config)
        self.org = next(o for o in self.registry if o["name"] == "OpenAI")

    def test_ai_scope_does_not_include_unrelated_medical_research(self):
        self.assertFalse(f.is_frontier_ai_relevant_paper(paper(
            "A randomized clinical trial of metformin", abstract="We measure blood glucose outcomes among participants receiving medication."), self.config))
        self.assertTrue(f.is_frontier_ai_relevant_paper(paper("Learning Personalized Agents from Human Feedback",
            abstract="Modern AI agents adapt to changing user preferences through reinforcement learning."), self.config))

    def test_org_name_in_author_field_is_not_topic_evidence(self):
        self.assertFalse(f.is_frontier_ai_relevant_paper(paper("A randomized clinical trial", abstract="",
            authors=["Qwen Team"], author_affiliations=["Alibaba AI"]), self.config))
        self.assertFalse(f.is_frontier_ai_relevant_paper(paper("Neural activity during sleep", abstract="We record biological neural activity in a clinical cohort."), self.config))

    def test_dedicated_ai_catalogue_and_ai_venue_supply_topic_evidence(self):
        self.assertTrue(f.is_frontier_ai_relevant_paper(paper("HyperAgents", abstract="", ai_research_source="https://ai.meta.com/results/"), self.config))
        self.assertTrue(f.is_frontier_ai_relevant_paper(paper("Exclusive Self Attention", abstract="", matched_keywords=["ICLR 2026"]), self.config))
        self.assertFalse(f.is_frontier_ai_relevant_paper(paper("Clinical outcomes of medication", abstract="A clinical cohort study.", matched_keywords=["Nature Medicine"]), self.config))

    def test_topic_evidence_survives_display_truncation_and_merge(self):
        accepted = paper("Opaque title", abstract="Background. " * 100 + " We train large language models.")
        accepted["topic_evidence"] = f.ai_topic_evidence(accepted, self.config)
        displayed = updater.normalize_paper(accepted)
        self.assertTrue(f.is_frontier_ai_relevant_paper(displayed, self.config))
        merged = f.merge_paper_lists([paper("Opaque title", abstract="")], [displayed])
        self.assertEqual(merged[0]["topic_evidence"]["scope"], "ai")

    def test_sparse_listing_is_enriched_before_exclusion(self):
        sparse = paper("HyperAgents", abstract="")
        with patch.object(updater, "enrich_publication", return_value={**sparse, "abstract": "Self-improving AI agents perform autonomous research."}):
            pending = []
            self.assertEqual(len(updater.filter_new_papers([sparse], self.config, [], pending)), 1)
            self.assertEqual(pending, [])

    def test_metadata_failure_is_queued(self):
        with patch.object(updater, "enrich_publication", side_effect=TimeoutError("temporary outage")):
            pending = []
            accepted = updater.filter_new_papers([paper("HyperAgents", abstract="")], self.config, [], pending)
            self.assertEqual(accepted, [])
            self.assertEqual(pending[0]["paper"]["title"], "HyperAgents")

    def test_arxiv_identity_normalizes_versions_and_endpoints(self):
        for url in ["https://arxiv.org/pdf/2605.17295v2.pdf", "https://arxiv.org/html/2605.17295v1",
                    "https://huggingface.co/papers/2605.17295", "arxiv:2605.17295v3"]:
            self.assertEqual(f.get_arxiv_id(url), "2605.17295")
            self.assertEqual(f.get_paper_key({"url": url}), "arxiv:2605.17295")

    def test_merge_bridges_identities_without_losing_provenance(self):
        a = paper("A sufficiently long title for a paper", id="one", url="https://example.org/a", sources=["openalex", "arxiv"])
        b = paper("An alternate title for the same paper", id="two", url="https://example.org/b", matched_orgs=["Microsoft"])
        bridge = paper(a["title"], id="three", url=b["url"], source="huggingface_search")
        original = copy.deepcopy([a, b, bridge])
        result = f.merge_paper_lists([a, b, bridge])
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0]["matched_orgs"]), {"OpenAI", "Microsoft"})
        self.assertIn("arxiv", result[0]["sources"])
        self.assertEqual([a, b, bridge], original)
        self.assertEqual(len(f.merge_paper_lists(result, [b])), 1)

    def test_bad_institution_name_is_rejected(self):
        result = {"results": [{"id": "I999", "display_name": "Unrelated University", "country_code": "US"}]}
        with patch.object(f, "openalex_get", return_value=result):
            self.assertEqual(f.resolve_openalex_institutions(self.org, self.config, {}), [])

    def test_microsoft_empty_meta_does_not_crash(self):
        record = {"data": {"meta": [], "terms": [], "permalink": "https://microsoft.com/publication/test",
                           "post_title": "Mamba-based image compression", "post_content": "Neural compression."}}
        self.assertEqual(f.microsoft_post_to_paper(record, self.org, "endpoint")["title"], record["data"]["post_title"])

    def test_anthropic_full_hydration_catalogue_and_empty_date_window(self):
        records = [{"_type": "post", "title": f"Language model paper {n}", "slug": {"current": f"paper-{n}"},
                    "publishedOn": "2025-01-01T00:00:00Z", "subjects": []} for n in range(15)]
        html = "<script>self.__next_f.push(" + json.dumps([1, "x:" + json.dumps(records)]) + ")</script>"
        with patch.object(f, "fetch_text_url", return_value=html):
            args = (self.org, {"url": "https://www.anthropic.com/research"})
            self.assertEqual(len(sources.anthropic_publications(*args, "2024-01-01", "2026-09-05")), 15)
            self.assertEqual(sources.anthropic_publications(*args, "2026-01-01", "2026-09-05"), [])

    def test_hf_follows_next_page(self):
        first = '<h3><a href="/papers/2601.00001">First language model paper</a></h3><a href="?p=1">Next</a>'
        last = '<h3><a href="/papers/2601.00002">Second language model paper</a></h3><a class="pointer-events-none" href="">Next</a>'
        with patch.object(f, "fetch_text_url", side_effect=[first, last]) as fetch:
            result = sources.hf_org_publications(self.org, {"hf_orgs": ["openai"], "max_pages": 0}, "2024-01-01", "2026-09-05")
            self.assertEqual(len(result), 2)
            self.assertIn("p=1", fetch.call_args.args[0])

    def test_alignment_includes_research_notes(self):
        html = '<h2>Articles</h2><a class="note" href="2026/auditbench/"><h3>AuditBench</h3><div class="description">Language model auditing.</div></a>'
        with patch.object(f, "fetch_text_url", return_value=html):
            result = sources.alignment_publications(self.org, {"url":"https://alignment.anthropic.com/"}, "2024-01-01", "2026-09-05")
            self.assertEqual(result[0]["title"], "AuditBench")

    def test_deepmind_follows_next_page_and_retains_partial_results(self):
        first = '<li class="list-group__item"><a href="/research/publications/1/"><span class="list-group__date">1 January 2025</span><span class="list-group__description">First paper</span></a></li><a aria-label="Next page" href="/research/publications/page/2/">Next</a>'
        with patch.object(f, "fetch_text_url", side_effect=[first, TimeoutError("temporary outage")]):
            result, health = collect_with_status("Google/DeepMind", "official", lambda: sources.deepmind_publications(
                self.org, {"url": "https://deepmind.google/research/publications/"}, "2024-01-01", "2026-09-05"))
            self.assertEqual(len(result), 1)
            self.assertEqual(health["status"], "partial")

    def test_author_block_required_not_model_mentions(self):
        org = {"name": "Alibaba/Qwen", "affiliation_terms": ["Alibaba Group", "Qwen Team"]}
        page = '<div class="ltx_authors"><span class="ltx_affiliation">Unrelated University</span></div><div class="ltx_abstract">We evaluate Qwen Team models by Alibaba Group.</div>'
        with patch.object(affiliations, "http_get", return_value=Mock(status_code=200, text=page)):
            self.assertEqual(affiliations.affiliation_evidence("2605.17295", org), {})
        page = '<div class="ltx_authors"><span class="ltx_affiliation">Qwen Team, Alibaba Group</span></div>'
        with patch.object(affiliations, "http_get", return_value=Mock(status_code=200, text=page)):
            self.assertIn("Alibaba Group", affiliations.affiliation_evidence("2605.17295", org)["matched_affiliation_terms"])

    def test_hf_author_display_name_is_not_company_affiliation(self):
        self.assertFalse(f.match_huggingface_company_evidence({}, {"authors":[{"name":"OpenAI Fan"}], "summary":"We benchmark GPT."}, self.org))
        self.assertTrue(f.match_huggingface_company_evidence({"organization":{"name":"OpenAI"}}, {}, self.org))

    def test_one_source_failure_does_not_discard_other_labs(self):
        config = copy.deepcopy(self.config)
        config["company_tracking"]["arxiv_affiliations"]["enabled"] = False
        orgs = [dict(self.org), dict(self.org, name="Microsoft")]
        for org in orgs:
            org["official_publication_pages"] = [{"type": "rss", "url": org["name"]}]
        def fetch(org, *_):
            if org["name"] == "Microsoft":
                raise ValueError("malformed source")
            return [paper()]
        diagnostics = []
        with patch.object(updater, "fetch_official_publication_page", side_effect=fetch):
            result = updater.collect_fresh_papers_by_source(config, orgs, 30, diagnostics=diagnostics, sources={"official"})
        self.assertEqual(len(result), 1)
        self.assertEqual({s["status"] for s in diagnostics}, {"ok", "failed"})

    def test_backfill_preserves_existing_archive_and_has_no_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "company_papers.json"
            old = paper("Earlier language model paper", id="old", url="https://example.org/old", published="2023-01-01")
            destination.write_text(json.dumps({"papers": [old]}))
            with patch.object(updater, "collect_fresh_papers_by_source", return_value=[paper()]):
                result = updater.update_archive(since="2024-01-01", output=destination)
                self.assertEqual(len(result["papers"]), 2)
                before = destination.read_bytes()
                with self.assertRaises(ValueError):
                    updater.update_archive(max_papers=1, output=destination)
                self.assertEqual(destination.read_bytes(), before)

    def test_reconciliation_advances_persistent_lab_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "company_papers.json"
            destination.write_text('{"papers": []}')
            def collect(*args, **kwargs):
                kwargs["diagnostics"].append({"lab":args[1][0]["name"], "source":"official", "status":"ok",
                    "checked_at":"2026-09-05T00:00:00Z", "errors":[], "limits":[]})
                return [paper()]
            with patch.object(updater, "collect_fresh_papers_by_source", side_effect=collect):
                updater.update_archive(output=destination, reconcile=True)
                state = json.loads((Path(tmp)/"collection_state.json").read_text())
                self.assertEqual(state["next_lab"], 1)
                updater.update_archive(output=destination, reconcile=True)
                self.assertEqual(json.loads((Path(tmp)/"collection_state.json").read_text())["next_lab"], 2)

    def test_metadata_budget_preserves_unprocessed_candidates(self):
        config = copy.deepcopy(self.config)
        config["company_tracking"]["metadata_checks_per_run"] = 1
        inputs = [paper("Opaque study one", abstract=""), paper("Opaque study two", abstract="", id="test:2")]
        with patch.object(updater, "enrich_publication", side_effect=lambda p, _: {**p, "abstract":"AI agents learn user preferences."}) as fetch:
            pending = []
            accepted = updater.filter_new_papers(inputs, config, [], pending)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(pending[0]["paper"]["id"], "test:2")

    def test_failed_repository_does_not_starve_scan_queue(self):
        config = copy.deepcopy(self.config)
        config["company_tracking"]["official_reports"]["repositories_per_run"] = 1
        response = Mock(status_code=200, text="")
        def fetch(org, name):
            if name == "lab/broken":
                record_source_error("temporary repository error")
                return []
            return [paper()]
        with tempfile.TemporaryDirectory() as tmp, patch.object(f, "OUTPUT_DIR", Path(tmp)), \
             patch.object(repositories, "repository_inventory", return_value={"hf:lab/broken":"v1", "hf:lab/good":"v1"}), \
             patch.object(f, "fetch_huggingface_repo_reports", side_effect=fetch), \
             patch.object(repositories, "http_get", return_value=response):
            first, health = collect_with_status("OpenAI", "repos", lambda: repositories.collect_repository_reports(config,self.org,30))
            second, _ = collect_with_status("OpenAI", "repos", lambda: repositories.collect_repository_reports(config,self.org,30))
            self.assertEqual(first, [])
            self.assertEqual(health["status"], "failed")
            self.assertEqual(len(second), 1)

    def test_author_search_finishes_pagination_before_rotating_batch(self):
        config = copy.deepcopy(self.config)
        config["company_tracking"]["arxiv_affiliations"].update(authors_per_run=1, candidates_per_query=1, pdf_checks_per_run=1)
        seeds = [paper(authors=["Alice Researcher", "Bob Researcher"])]
        candidate = paper(id="arxiv:2609.00001", url="https://arxiv.org/abs/2609.00001")
        with tempfile.TemporaryDirectory() as tmp, patch.object(f,"OUTPUT_DIR",Path(tmp)), \
             patch.object(f,"fetch_arxiv_query",return_value=[candidate]) as fetch, \
             patch.object(affiliations,"affiliation_evidence",return_value={}):
            affiliations.fetch_affiliation_papers(config,self.org,seeds,30)
            first_author_query = fetch.call_args_list[1]
            affiliations.fetch_affiliation_papers(config,self.org,seeds,30)
            next_author_query = fetch.call_args_list[3]
            self.assertEqual(first_author_query.args[0],next_author_query.args[0])
            self.assertEqual(next_author_query.kwargs["start_offset"],1)

    def test_truncated_github_tree_is_walked_without_losing_nested_pdfs(self):
        responses = [
            {"default_branch":"main", "description":"Language model research", "topics":["llm"]},
            {"truncated":True, "sha":"root", "tree":[]},
            {"tree":[{"type":"tree", "path":"papers", "sha":"child"}]},
            {"tree":[{"type":"blob", "path":"technical_report.pdf"}]},
        ]
        response = Mock(status_code=200, text="# Language model research")
        with patch.object(f,"github_get_json",side_effect=responses), patch.object(f,"http_get",return_value=response):
            result = f.fetch_github_repo_reports(self.org,"openai/example")
            self.assertEqual(len(result),1)
            self.assertTrue(result[0]["url"].endswith("/papers/technical_report.pdf"))
            self.assertEqual(result[0]["published"], "")


if __name__ == "__main__":
    unittest.main()
