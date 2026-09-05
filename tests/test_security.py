import copy
import gzip
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pypdf import PdfWriter
import collection_status
import generate_markdown_index as markdown
import pdf_text
import safe_http
import security
import validate_publication_artifact as artifact
import fetch_papers


class SecurityTests(unittest.TestCase):
    def test_url_policy_rejects_local_private_and_active_schemes(self):
        values = ["javascript:alert(1)", "data:text/html,test", "file:///etc/passwd", "http://localhost/",
                  "http://127.0.0.1/", "http://169.254.169.254/", "http://10.0.0.1/", "http://[::1]/",
                  "http://[::ffff:127.0.0.1]/", "http://2130706433/", "http://0177.0.0.1/",
                  "https://user:password@example.com/", "https://example.com:8080/", "https://example.com/%0d%0aheader",
                  "https://example.com\\@127.0.0.1/", "https://localhost%2e/"]
        for value in values:
            with self.subTest(url=value), self.assertRaises(security.UnsafeURL):
                security.public_http_url(value)
        self.assertTrue(security.safe_link("https://arxiv.org/abs/2605.17295"))

    def test_dns_alias_and_mixed_answers_cannot_reach_private_addresses(self):
        answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
                  (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with patch.object(security.socket, "getaddrinfo", return_value=answer), self.assertRaises(security.UnsafeURL):
            security.public_addresses("https://public-looking.example/")

    def test_connections_pin_validated_ip_and_keep_tls_hostname(self):
        raw = Mock(status=200, headers={})
        raw.stream.return_value = iter([b"ok"])
        pool = Mock(); pool.urlopen.return_value = raw
        with patch.object(safe_http, "public_addresses", return_value=("example.com",443,["1.1.1.1"])) as resolve, \
             patch.object(safe_http.urllib3, "HTTPSConnectionPool", return_value=pool) as factory:
            result = safe_http.pinned_request("GET", "https://example.com/path")
        self.assertEqual(result.content, b"ok")
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(factory.call_args.args[0], "1.1.1.1")
        self.assertEqual(factory.call_args.kwargs["assert_hostname"], "example.com")
        self.assertEqual(factory.call_args.kwargs["server_hostname"], "example.com")
        self.assertEqual(pool.urlopen.call_args.kwargs["headers"]["Host"], "example.com")

    def test_redirect_to_internal_address_is_blocked_before_connection(self):
        raw = Mock(status=302, headers={"Location":"http://169.254.169.254/latest/meta-data/"})
        pool = Mock();pool.urlopen.return_value=raw
        with patch.object(safe_http,"public_addresses",return_value=("example.com",443,["1.1.1.1"])), \
             patch.object(safe_http.urllib3,"HTTPSConnectionPool",return_value=pool) as factory:
            with self.assertRaises(safe_http.RequestPolicyError):
                safe_http.pinned_request("GET","https://example.com/")
        self.assertEqual(factory.call_count,1)

    def test_redirect_drops_credentials_when_origin_changes(self):
        first=Mock(status=302,headers={"Location":"https://other.example/paper?api_key=test-secret"})
        last=Mock(status=200,headers={});last.stream.return_value=iter([b"ok"])
        pools=[Mock(),Mock()];pools[0].urlopen.return_value=first;pools[1].urlopen.return_value=last
        with patch.object(safe_http,"public_addresses",side_effect=[("example.com",443,["1.1.1.1"]),("other.example",443,["8.8.8.8"])]), \
             patch.object(safe_http.urllib3,"HTTPSConnectionPool",side_effect=pools):
            result=safe_http.pinned_request("GET","https://example.com/",headers={"Authorization":"Bearer test-secret","Cookie":"session=test"})
        headers=pools[1].urlopen.call_args.kwargs["headers"]
        self.assertNotIn("Authorization",headers);self.assertNotIn("Cookie",headers)
        self.assertNotIn("test-secret",result.url)

    def test_streaming_and_decompression_limits(self):
        raw=Mock(headers={});raw.stream.return_value=iter([b"1234",b"5678"])
        with self.assertRaises(safe_http.RequestPolicyError):safe_http.bounded_body(raw,6)
        raw=Mock(headers={"Content-Encoding":"gzip"});raw.stream.return_value=iter([gzip.compress(b"x"*100000)])
        with self.assertRaises(safe_http.RequestPolicyError):safe_http.bounded_body(raw,1000)
        raw=Mock(headers={"Content-Length":"99999999999999999999999"})
        with self.assertRaises(safe_http.RequestPolicyError):safe_http.bounded_body(raw,1000)
        raw.stream.assert_not_called()

    def test_policy_rejections_are_not_retried(self):
        with patch.object(collection_status,"pinned_request",side_effect=safe_http.RequestPolicyError("blocked")) as request:
            with self.assertRaises(safe_http.RequestPolicyError):collection_status.http_get("https://example.com/")
        self.assertEqual(request.call_count,1)

    def test_github_helper_cannot_send_credentials_to_another_host(self):
        with patch.object(fetch_papers,"http_get") as request, self.assertRaises(ValueError):
            fetch_papers.github_get_json("https://other.example/api")
        request.assert_not_called()

    def test_diagnostics_redact_environment_and_token_parameters(self):
        with patch.dict(collection_status.os.environ,{"OPENALEX_API_KEY":"test-only-api-secret"}):
            value=collection_status.safe_message("https://example.com/?API_KEY=test-only-api-secret&access_token=also-secret Authorization: Bearer credential")
        self.assertNotIn("test-only-api-secret",value);self.assertNotIn("also-secret",value);self.assertNotIn("credential",value)

    def test_markdown_cannot_inject_markup_or_an_active_link(self):
        title='Paper ](https://attacker.example/) <img src=x onerror=alert(1)> `code` | field'
        rendered=markdown.link(title,"https://arxiv.org/abs/2605.17295")
        self.assertIn(r"\]",rendered);self.assertIn("&lt;img",rendered);self.assertIn(r"\|",rendered)
        self.assertTrue(rendered.endswith("(<https://arxiv.org/abs/2605.17295>)"))
        self.assertEqual(markdown.link("Paper","javascript:alert(1)"),"Paper")
        with self.assertRaises(ValueError):markdown.lab_doc_path({"id":"../../escape"})

    def test_pdf_extraction_is_bounded_and_works_in_an_isolated_process(self):
        writer=PdfWriter();writer.add_blank_page(width=72,height=72);stream=io.BytesIO();writer.write(stream)
        self.assertEqual(pdf_text.first_page_text(stream.getvalue()),"")
        with self.assertRaises(ValueError):pdf_text.first_page_text(b"<html>Not a PDF</html>")

    def test_artifact_rejects_extra_files_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in artifact.FILES:(root/name).write_text("{}")
            (root/"workflow.yml").write_text("untrusted")
            with self.assertRaises(ValueError):artifact.validate(root)
            (root/"workflow.yml").unlink();(root/"company_papers.json").unlink()
            (root/"company_papers.json").symlink_to(root/"collection_health.json")
            with self.assertRaises(ValueError):artifact.validate(root)

    def test_readme_keeps_all_lab_sections_and_visible_recent_tables(self):
        data=json.loads((markdown.DATA_FILE).read_text())
        readme=markdown.build_readme(data)
        for company in data["companies"]:
            self.assertIn("### "+markdown.md_escape(company["name"]),readme)
            self.assertIn(markdown.lab_doc_path(company),readme)
        self.assertIn("## Latest Across Labs",readme)
        self.assertEqual(readme,markdown.build_readme(data))


if __name__ == "__main__":unittest.main()
