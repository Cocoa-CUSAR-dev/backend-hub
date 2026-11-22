"""Starting the app must not import the heavy libraries only some routes need
(docs-and-plan#326).

Vercel imports `src.main` on every cold start, before it can answer the first
request. litellm + openai alone took ~7 s of a 20 s cold start; Sentry's
auto-enabled integrations imported openai, huggingface_hub and boto3 again.
Each of these is now imported on first use (src/llm/client.py,
src/reports/xlsx.py, src/r2/client.py) or not at all (src/main.py's
integrations list). This test fails if one creeps back into start-up.
"""

import subprocess
import sys

# Not botocore: Sentry's always-on StdlibIntegration imports botocore.awsrequest
# whenever it's installed (~0.1 s), and it's harmless next to the rest.
LAZY = ["litellm", "openai", "huggingface_hub", "openpyxl", "boto3"]


def test_importing_the_app_loads_none_of_the_lazy_libraries() -> None:
    # A fresh interpreter: this test process has imported them already.
    # The env vars tests/conftest.py set are inherited.
    script = f"import sys, src.main; print(','.join(m for m in {LAZY!r} if m in sys.modules))"
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=True
    )
    loaded = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    assert loaded == "", f"imported at start-up: {loaded}"
