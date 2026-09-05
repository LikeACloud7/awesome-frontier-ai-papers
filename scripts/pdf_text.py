"""Bounded PDF extraction separate from the network collector process."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from safe_http import MAX_PDF_BYTES


def first_page_text(data: bytes) -> str:
    if len(data) > MAX_PDF_BYTES or b"%PDF-" not in data[:1024]:
        raise ValueError("Invalid or oversized PDF response")
    with tempfile.TemporaryDirectory(prefix="frontier-pdf-") as folder:
        path = Path(folder) / "paper.pdf"
        path.write_bytes(data)
        try:
            result = subprocess.run([sys.executable, "-I", str(Path(__file__).with_name("pdf_worker.py")), str(path)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20, check=True,
                env={"PATH": os.defpath, "LANG": "C.UTF-8"})
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as error:
            raise ValueError("PDF extraction failed or exceeded its resource budget") from error
        text = json.loads(result.stdout)
        if not isinstance(text, str) or len(text) > 200000:
            raise ValueError("Invalid PDF extraction result")
        return text
