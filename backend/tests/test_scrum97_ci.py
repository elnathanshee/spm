"""SCRUM-97: CI: run the frontend tests and build.

Done when (Jira):
  1. CI runs the frontend tests and the frontend build on every push and pull request to main.
  2. A failing frontend test or a broken build fails the run.
  3. The README says how to run the frontend tests locally.
"""

from pathlib import Path


def test_tc9701_ci_workflow_runs_frontend_tests_and_build():
    """TC-9701 (Done-when 1, 2): .github/workflows/ci.yml defines a frontend job
    that runs tests and build on pushes and PRs to main."""
    root = Path(__file__).resolve().parents[2]
    ci_path = root / ".github" / "workflows" / "ci.yml"
    assert ci_path.exists(), "ci.yml must exist"

    content = ci_path.read_text()

    # Triggers on push and pull_request to main
    assert "push:" in content and "branches: [main]" in content
    assert "pull_request:" in content

    # Has a dedicated frontend job
    assert "frontend:" in content

    # Uses actions/setup-node
    assert "actions/setup-node" in content

    # Installs dependencies, runs frontend tests and build
    assert "npm ci" in content or "npm install" in content
    assert "npm test" in content or "node --test" in content
    assert "npm run build" in content or "vite build" in content


def test_tc9702_readme_documents_frontend_testing():
    """TC-9702 (Done-when 3): README.md documents how to run frontend tests locally."""
    root = Path(__file__).resolve().parents[2]
    readme_path = root / "README.md"
    assert readme_path.exists(), "README.md must exist"

    content = readme_path.read_text()

    assert "npm test" in content
    assert "npm run build" in content
