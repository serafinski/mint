#!/usr/bin/env python3
"""Validate generated Agent SKILL.md files.

The validator is used before generated skill content is accepted by the
runtime. It can also be run directly against a skill directory.

Example:
```python
python quick_validate.py data/eval/skills/my-skill
```
"""

import re
import sys
from pathlib import Path

import yaml


def validate_skill(skill_path):
    """Validate a directory containing a SKILL.md file.

    Args:
        skill_path: Path to a skill directory.

    Returns:
        Tuple of (is_valid, message) where is_valid is bool and message
            describes result.
    """
    skill_path = Path(skill_path)

    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md not found"

    content = skill_md.read_text()
    if not content.startswith("---"):
        return False, "No YAML frontmatter found"

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format"

    frontmatter_text = match.group(1)

    # Reject extra bare-line '---' delimiters beyond the two frontmatter
    # markers. YAML document splitters can treat later delimiters as document
    # boundaries and silently ignore body content after them.
    bare_delim_count = sum(
        1 for line in content.splitlines() if line.strip() == "---"
    )
    if bare_delim_count > 2:
        return False, (
            f"SKILL body contains {bare_delim_count - 2} extra bare-line "
            "'---' delimiter(s) beyond the frontmatter markers. Skill "
            "loaders may interpret body content after these as separate "
            "YAML documents and drop it silently. Use '***' or indented "
            "'---' for horizontal rules."
        )

    try:
        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            return False, "Frontmatter must be a YAML dictionary"
    except yaml.YAMLError as e:
        return False, f"Invalid YAML in frontmatter: {e}"

    ALLOWED_PROPERTIES = {
        "name",
        "description",
        "license",
        "compatibility",
        "allowed-tools",
        "metadata",
    }

    unexpected_keys = set(frontmatter.keys()) - ALLOWED_PROPERTIES
    if unexpected_keys:
        unexpected_str = ", ".join(sorted(unexpected_keys))
        allowed_str = ", ".join(sorted(ALLOWED_PROPERTIES))
        return False, (
            f"Unexpected key(s) in SKILL.md frontmatter: {unexpected_str}. "
            f"Allowed properties are: {allowed_str}"
        )

    if "name" not in frontmatter:
        return False, "Missing 'name' in frontmatter"
    if "description" not in frontmatter:
        return False, "Missing 'description' in frontmatter"

    name = frontmatter.get("name", "")
    if not isinstance(name, str):
        return False, f"Name must be a string, got {type(name).__name__}"
    name = name.strip()
    if name:
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
        if len(name) > 64:
            return (
                False,
                f"Name is too long ({len(name)} characters). Maximum is 64 characters.",
            )

    description = frontmatter.get("description", "")
    if not isinstance(description, str):
        return False, f"Description must be a string, got {type(description).__name__}"
    description = description.strip()
    if description:
        if "<" in description or ">" in description:
            return False, "Description cannot contain angle brackets (< or >)"
        if len(description) > 1024:
            return (
                False,
                (
                    f"Description is too long ({len(description)} characters). "
                    "Maximum is 1024 characters."
                ),
            )

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

    # Prose checks: hard-fail on patterns that are never legitimate in skill
    # bodies. Deliberately literal so words like 'placeholder' remain valid
    # when used in normal instructions.
    body_match = re.match(r"^---\n.*?\n---\n?(.*)$", content, re.DOTALL)
    body = body_match.group(1) if body_match else content
    if re.search(r"\blorem ipsum\b", body, re.IGNORECASE):
        return False, "Skill body contains lorem ipsum placeholder text"
    if "TODO: implement" in body:
        return False, "Skill body contains literal 'TODO: implement' marker"
    if "# placeholder return" in body:
        return False, "Skill body contains literal '# placeholder return' marker"

    # Body length warning (per Agent Skills spec: < 500 lines recommended).
    # Does not fail validation; appended to the success message.
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
