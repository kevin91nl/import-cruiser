# pydepend

**Analyze, validate, and visualize Python import dependencies.**

`pydepend` is a CLI tool for Python projects inspired by [dependency-cruiser](https://github.com/sverweij/dependency-cruiser). It parses Python `import` statements, builds a dependency graph, detects circular dependencies, validates the graph against configurable rules, and can export the results as JSON or DOT (Graphviz) format.

---

## Features

- 🔍 **Parse** all Python imports in a project directory
- 🔄 **Detect** circular dependencies automatically
- ✅ **Validate** dependencies against user-defined JSON rules
- 📊 **Export** dependency graphs as JSON or DOT/Graphviz
- 🖥️ **CLI** with three subcommands: `analyze`, `validate`, `export`

---

## Installation

### With pip

```bash
pip install pydepend
```

### From source (with [Poetry](https://python-poetry.org/))

```bash
git clone https://github.com/kevin91nl/pydepend.git
cd pydepend
poetry install
```

---

## CLI Usage

```
pydepend [OPTIONS] COMMAND [ARGS]...

Commands:
  analyze   Analyze imports and output results.
  export    Export the dependency graph.
  validate  Validate dependencies against rules.
```

### `analyze`

Scan a Python project and output a JSON or DOT dependency report.

```bash
# JSON report (default)
pydepend analyze ./myproject

# DOT format for Graphviz
pydepend analyze ./myproject --format dot

# Write to file
pydepend analyze ./myproject --output report.json
```

**JSON output structure:**

```json
{
  "summary": {
    "modules": 12,
    "dependencies": 18,
    "cycles": 0,
    "violations": 0
  },
  "modules": [...],
  "dependencies": [...],
  "cycles": [],
  "violations": []
}
```

### `validate`

Validate dependencies against rules defined in a JSON configuration file.

```bash
pydepend validate ./myproject --config pydepend.json

# Exit non-zero if there are any violations (useful in CI)
pydepend validate ./myproject --config pydepend.json --strict
```

### `export`

Export the dependency graph to DOT format (compatible with [Graphviz](https://graphviz.org/)).

```bash
pydepend export ./myproject --output graph.dot

# Render with Graphviz
dot -Tsvg graph.dot -o graph.svg
```

---

## Configuration

Create a `pydepend.json` file to define dependency rules:

```json
{
  "rules": [
    {
      "name": "no-circular",
      "severity": "error",
      "from": { "path": "myapp\\.ui" },
      "to":   { "path": "myapp\\.data" },
      "allow": false
    },
    {
      "name": "allow-utils",
      "severity": "warn",
      "from": {},
      "to": { "path": "myapp\\.utils" },
      "allow": true
    }
  ],
  "options": {
    "include_external": false
  }
}
```

### Rule schema

| Field      | Type    | Required | Description                                         |
|------------|---------|----------|-----------------------------------------------------|
| `name`     | string  | ✅        | Unique rule identifier                              |
| `severity` | string  | ✅        | `"error"`, `"warn"`, or `"info"`                    |
| `from`     | object  | ✅        | Source module pattern (see *Pattern object* below)  |
| `to`       | object  | ✅        | Target module pattern (see *Pattern object* below)  |
| `allow`    | boolean | ❌        | `true` (default) = allowed; `false` = forbidden     |

### Pattern object

| Field  | Type   | Description                                          |
|--------|--------|------------------------------------------------------|
| `path` | string | Regular expression matched against the module name   |

An empty pattern object `{}` matches **all** modules.

---

## Examples

### Detect circular dependencies

```bash
pydepend analyze ./myproject
# Check the "cycles" key in the JSON output
```

### Enforce layered architecture

```json
{
  "rules": [
    {
      "name": "no-data-to-ui",
      "severity": "error",
      "from": { "path": "\\.data" },
      "to":   { "path": "\\.ui" },
      "allow": false
    }
  ]
}
```

```bash
pydepend validate ./myproject --config pydepend.json --strict
```

### Visualize dependencies

```bash
pydepend export ./myproject --output deps.dot
dot -Tpng deps.dot -o deps.png
open deps.png
```

---

## Development

```bash
# Install dev dependencies
poetry install

# Run tests
poetry run pytest

# Run tests with coverage
poetry run pytest --cov
```

---

## License

MIT
