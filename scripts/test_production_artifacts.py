import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_DOCKERFILES = (
    ROOT / "stock-service" / "Dockerfile.prod",
    ROOT / "llm-host" / "Dockerfile.prod",
    ROOT / "web-ui" / "Dockerfile.prod",
)
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
FULL_SHA_ACTION = re.compile(r"^\s*uses:\s*[^\s@]+@([0-9a-f]{40})(?:\s+#.*)?$")


class ProductionArtifactContractTest(unittest.TestCase):
    def test_production_dockerfile_bases_are_digest_pinned(self):
        for path in PRODUCTION_DOCKERFILES:
            with self.subTest(path=path.name):
                from_lines = [
                    line.strip()
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.lstrip().startswith("FROM ")
                ]
                self.assertTrue(from_lines, f"{path} has no FROM instruction")
                for line in from_lines:
                    image = line.split()[1]
                    self.assertRegex(
                        image,
                        r"@sha256:[0-9a-f]{64}$",
                        f"Production base image is mutable: {line}",
                    )

    def test_privileged_release_actions_are_full_sha_pinned(self):
        uses_lines = [
            line
            for line in RELEASE_WORKFLOW.read_text(encoding="utf-8").splitlines()
            if line.lstrip().startswith("uses:")
        ]
        self.assertTrue(uses_lines, "Release workflow has no action references")
        for line in uses_lines:
            self.assertIsNotNone(
                FULL_SHA_ACTION.match(line),
                f"Privileged release action must be pinned to a full commit SHA: {line.strip()}",
            )

    def test_release_workflow_never_publishes_latest_application_tag(self):
        workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        self.assertNotRegex(workflow, r"smart-stock-(?:stock-service|llm-host|web-ui):latest\b")
        self.assertIn("sha-${{ github.sha }}", workflow)
        self.assertIn("${{ github.ref_name }}", workflow)


if __name__ == "__main__":
    unittest.main()
