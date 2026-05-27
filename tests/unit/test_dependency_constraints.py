from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_STOP_MARKERS = {"# Testing", "# Code quality (development)"}


def _read_root_production_requirements() -> list[str]:
    requirements: list[str] = []
    for raw_line in (PROJECT_ROOT / "requirements.txt").read_text().splitlines():
        line = raw_line.strip()
        if line in PRODUCTION_STOP_MARKERS:
            break
        if line and not line.startswith("#"):
            requirements.append(line)
    return requirements


def _read_pyproject_dependencies() -> list[str]:
    lines = (PROJECT_ROOT / "pyproject.toml").read_text().splitlines()
    dependencies: list[str] = []
    in_dependencies = False
    for raw_line in lines:
        line = raw_line.strip()
        if line == "dependencies = [":
            in_dependencies = True
            continue
        if in_dependencies and line == "]":
            break
        if in_dependencies and line:
            # Remove trailing comma
            dep = line.rstrip(",")
            # Handle TOML double-quoted string: remove outer quotes and unescape
            if dep.startswith('"') and dep.endswith('"'):
                dep = dep[1:-1]  # Remove outer quotes
                dep = dep.replace('\\"', '"')  # Unescape internal quotes
            dependencies.append(dep)
    return dependencies


def _read_requirements(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_root_production_dependencies_are_synced_with_pyproject():
    assert _read_root_production_requirements() == _read_pyproject_dependencies()


def test_production_dependencies_have_upper_bounds():
    requirement_files = [
        PROJECT_ROOT / "requirements.txt",
        PROJECT_ROOT / "remote-agent" / "requirements.txt",
    ]

    unbounded = []
    for requirement_file in requirement_files:
        requirements = (
            _read_root_production_requirements()
            if requirement_file.name == "requirements.txt"
            and requirement_file.parent == PROJECT_ROOT
            else _read_requirements(requirement_file)
        )
        unbounded.extend(
            f"{requirement_file.relative_to(PROJECT_ROOT)}:{requirement}"
            for requirement in requirements
            if "<" not in requirement
        )

    assert unbounded == []
