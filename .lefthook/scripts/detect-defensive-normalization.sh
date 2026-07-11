#!/bin/bash
# Detect defensive string normalization patterns (code smell).
#
# Pattern: str(...).strip().lower() when the value should already be normalized.
# This typically indicates:
# 1. Redundant normalization (value normalized at source)
# 2. Lack of trust in type system / pydantic validation
#
# Whitelisted: Single-line functions with names starting with:
#   _sanitize, sanitize, is, _is, to, _to
#
# Usage: detect-defensive-normalization.sh [files...]

set -e

if [ $# -eq 0 ]; then
  echo "Usage: detect-defensive-normalization.sh [files...]"
  exit 0
fi

# Use Python to do the validation with context-awareness
files_json=$(python3 -c "import json, sys; print(json.dumps(sys.argv[1:]))" "$@")

python3 << PYEOF
import json
import sys
import re
import ast

# Allowed function name prefixes for whitelisted defensive normalization
ALLOWED_PREFIXES = ("_sanitize", "sanitize", "is", "_is", "to", "_to")

# Pattern to detect: str(...).strip().lower() in COMPARISONS only
PATTERN = re.compile(r'(if|elif)\s+.*str\([^)]*\)\s*\.\s*strip\s*\(\s*\)\s*\.\s*lower\s*\(\s*\)\s*(==|!=)')

files = json.loads('$files_json')
found_issues = False

for filepath in files:
    if not filepath.endswith('.py'):
        continue

    try:
        with open(filepath, 'r') as f:
            lines = f.readlines()
            content = ''.join(lines)

        # Parse the file to find function definitions and their ranges
        try:
            tree = ast.parse(content)
        except SyntaxError:
            # Skip files with syntax errors (may be incomplete)
            continue

        # Build a map of line numbers to function info (name, is_single_line)
        line_to_func = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                func_start = node.lineno
                func_end = node.end_lineno or node.lineno
                func_name = node.name

                # Check if function is exactly one line
                is_single_line = (func_end - func_start + 1) == 1

                # Map each line in the function to its metadata
                for line_num in range(func_start, func_end + 1):
                    line_to_func[line_num] = (func_name, is_single_line)

        # Find violations
        violations = []
        for line_num, line_text in enumerate(lines, start=1):
            if PATTERN.search(line_text):
                func_name, is_single_line = line_to_func.get(line_num, (None, False))

                # Check if this match is whitelisted
                is_whitelisted = (
                    func_name is not None
                    and is_single_line
                    and func_name.startswith(ALLOWED_PREFIXES)
                )

                if not is_whitelisted:
                    violations.append((line_num, line_text.rstrip()))

        # Report violations
        if violations:
            if found_issues == False:
                print("❌ Defensive string normalization detected (code smell):")
                print("   Pattern: str(...).strip().lower()")
                print("   These values should be normalized at source, not on access.")
                print()
                found_issues = True

            for line_num, line_text in violations:
                print(f"   {filepath}:{line_num}: {line_text}")

    except Exception as e:
        # Skip files we can't process (graceful degradation)
        continue

if found_issues:
    print()
    print("💡 Fix: Trust the type system. Normalize at construction (pydantic model),")
    print("   not at every access point. Use Literal[...] types to enforce values.")
    print("   Whitelisted in: _sanitize*(), sanitize*(), is*(), _is*(), to*(), _to*()")
    print()
    sys.exit(1)

sys.exit(0)
PYEOF
