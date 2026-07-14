from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_docker_build_context_is_allowlisted_without_secret_files():
    rules = [
        line.strip()
        for line in (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert rules[0] == "*"
    assert "!onleiharr/**" not in rules
    assert not any(
        rule.lstrip("!").rstrip("/")
        in {"onleiharr.toml", "apprise.yml", "session.json", ".env", ".git/config"}
        for rule in rules
        if rule.startswith("!")
    )


def test_docker_runtime_stage_only_copies_built_wheels():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    runtime_stage = dockerfile.split("FROM python:alpine", maxsplit=2)[-1]

    assert "COPY ." not in runtime_stage
    assert "COPY --from=builder /wheels" in runtime_stage
    assert 'CMD ["-c", "/config/onleiharr.toml"]' in runtime_stage
