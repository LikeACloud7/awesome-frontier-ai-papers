"""Extract one PDF page in a short-lived process without collector credentials."""
import json
from pathlib import Path
import resource
import sys

from pypdf import PdfReader

resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
if sys.platform.startswith("linux"):
    resource.setrlimit(resource.RLIMIT_DATA, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))

reader = PdfReader(Path(sys.argv[1]))
text = (reader.pages[0].extract_text() or "") if reader.pages else ""
json.dump(text[:200000], sys.stdout)
