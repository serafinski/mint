#!/usr/bin/env python3
"""Quick validation script for skills - minimal version.

For deepagents CLI, skills are located at:
~/.deepagents/<agent>/skills/<skill-name>/

Example:
```python
python quick_validate.py ~/.deepagents/agent/skills/my-skill
```
"""

import re
import sys
from pathlib import Path

import yaml


def validate_skill(skill_path):
    """Basic validation of a skill.

    Returns:
        Tuple of (is_valid, message) where is_valid is bool and message
            describes result.
    """
    skill_path = Path(skill_path)

    # Check SKILL.md exists
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md not found"

    # Read and validate frontmatter
    content = skill_md.read_text()
    if not content.startswith("---"):
        return False, "No YAML frontmatter found"

    # Extract frontmatter
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format"

    frontmatter_text = match.group(1)

    # ext8 #75 (2026-04-20): reject bodies containing bare-line '---'
    # delimiters beyond the 2 frontmatter markers. DeepAgents' skill loader
    # (or any downstream tool using ``yaml.safe_load_all``) splits on
    # ``\n---\n`` → body content after the 3rd delimiter can be silently
    # dropped. Count bare-line '---' occurrences; expect exactly 2 (open
    # + close of frontmatter).
    bare_delim_count = sum(
        1 for line in content.splitlines() if line.strip() == "---"
    )
    if bare_delim_count > 2:
        return False, (
            f"SKILL body contains {bare_delim_count - 2} extra bare-line "
            "'---' delimiter(s) beyond the frontmatter markers. DeepAgents "
            "may interpret body content after these as separate YAML "
            "documents and drop it silently. Use '***' or "
            "indented '---' for horizontal rules."
        )

    # Parse YAML frontmatter
    try:
        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            return False, "Frontmatter must be a YAML dictionary"
    except yaml.YAMLError as e:
        return False, f"Invalid YAML in frontmatter: {e}"

    # Define allowed properties
    ALLOWED_PROPERTIES = {
        "name",
        "description",
        "license",
        "compatibility",
        "allowed-tools",
        "metadata",
    }

    # Check for unexpected properties (excluding nested keys under metadata)
    unexpected_keys = set(frontmatter.keys()) - ALLOWED_PROPERTIES
    if unexpected_keys:
        unexpected_str = ", ".join(sorted(unexpected_keys))
        allowed_str = ", ".join(sorted(ALLOWED_PROPERTIES))
        return False, (
            f"Unexpected key(s) in SKILL.md frontmatter: {unexpected_str}. "
            f"Allowed properties are: {allowed_str}"
        )

    # Check required fields
    if "name" not in frontmatter:
        return False, "Missing 'name' in frontmatter"
    if "description" not in frontmatter:
        return False, "Missing 'description' in frontmatter"

    # Extract name for validation
    name = frontmatter.get("name", "")
    if not isinstance(name, str):
        return False, f"Name must be a string, got {type(name).__name__}"
    name = name.strip()
    if name:
        # Check naming convention (hyphen-case: lowercase with hyphens)
        if not re.match(r"^[a-z0-9-]+$", name):
            return (
                False,
                (
                    f"Name '{name}' should be hyphen-case "
                    "(lowercase letters, digits, and hyphens only)"
                ),
            )
        if name.startswith("-") or name.endswith("-") or "--" in name:
            return (
                False,
                (
                    f"Name '{name}' cannot start/end with hyphen "
                    "or contain consecutive hyphens"
                ),
            )
        # Check name length (max 64 characters per spec)
        if len(name) > 64:
            return (
                False,
                f"Name is too long ({len(name)} characters). Maximum is 64 characters.",
            )

    # Extract and validate description
    description = frontmatter.get("description", "")
    if not isinstance(description, str):
        return False, f"Description must be a string, got {type(description).__name__}"
    description = description.strip()
    if description:
        # Check for angle brackets
        if "<" in description or ">" in description:
            return False, "Description cannot contain angle brackets (< or >)"
        # Check description length (max 1024 characters per spec)
        if len(description) > 1024:
            return (
                False,
                (
                    f"Description is too long ({len(description)} characters). "
                    "Maximum is 1024 characters."
                ),
            )

    # Extract and validate compatibility (max 500 characters per spec)
    compatibility = frontmatter.get("compatibility", "")
    if isinstance(compatibility, str):
        compatibility = compatibility.strip()
        if len(compatibility) > 500:
            return (
                False,
                (
                    f"Compatibility is too long ({len(compatibility)} characters). "
                    "Maximum is 500 characters."
                ),
            )

    # Tier 0 prose checks: hard-fail on patterns that are never legitimate
    # in skill bodies. Deliberately literal — broad patterns like 'placeholder'
    # would re-introduce the T04 false positive.
    body_match = re.match(r"^---\n.*?\n---\n?(.*)$", content, re.DOTALL)
    body = body_match.group(1) if body_match else content
    if re.search(r"\blorem ipsum\b", body, re.IGNORECASE):
        return False, "Skill body contains lorem ipsum placeholder text"
    if "TODO: implement" in body:
        return False, "Skill body contains literal 'TODO: implement' marker"
    if "# placeholder return" in body:
        return False, "Skill body contains literal '# placeholder return' marker"

    # Body length warning (per Agent Skills spec: < 500 lines recommended).
    # Does not fail validation — appended to the success message.
    warnings: list[str] = []
    body_lines = body.count("\n") + (1 if body and not body.endswith("\n") else 0)
    if body_lines > 500:
        warnings.append(
            f"Body is {body_lines} lines (spec recommends < 500)"
        )

    if warnings:
        return True, "Skill is valid! Warning: " + "; ".join(warnings)
    return True, "Skill is valid!"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python quick_validate.py <skill_directory>")
        sys.exit(1)

    valid, message = validate_skill(sys.argv[1])
    print(message)
    sys.exit(0 if valid else 1)
