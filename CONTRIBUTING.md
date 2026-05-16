# Contributing to Biologics Decision Engine

Thank you for your interest in contributing. This document covers how to add benchmark cases, create new modules, extend pipelines, and meet testing requirements.

## Development Setup

```bash
git clone <repo-url>
cd biologics-decision-engine
pip install -r requirements.txt
python3 -m pytest tests/ -v
```

All 212 tests should pass before you begin.

## How to Add Benchmark Cases

Benchmark cases live in `benchmarks/cases/` as JSON files. Each case has a prefix indicating its type:

| Prefix | Type | Required Fields |
|--------|------|-----------------|
| `COMP-` | Comparability | `case_id`, `description`, `attributes`, `expected_verdict` |
| `MEMO-` | Gap Memo | `case_id`, `description`, `sections` |
| `READY-` | Readiness | `case_id`, `description`, `submission_data` |
| `REG-` | Regulatory | `case_id`, `description`, `regulatory_data` |

### Steps

1. Create a new JSON file following the naming convention (e.g., `COMP-006.json`).
2. Include all required fields for the case type. Use an existing case as a template:
   ```bash
   cp benchmarks/cases/COMP-001.json benchmarks/cases/COMP-006.json
   # Edit with your data
   ```
3. For comparability cases, include `expected_verdict` and `expected_actions` so the benchmark runner can validate correctness.
4. Run the benchmark tests to confirm your case passes:
   ```bash
   python3 -m pytest tests/test_benchmark_cases.py -v
   ```
5. Verify JSON schema conformance -- the test suite checks required fields automatically.

## How to Add New Modules

Each module lives in `modules/<module_name>/` and follows a standard structure:

```
modules/your_module/
    __init__.py       # Module docstring + public API exports
    engine.py         # Core reasoning logic
    schemas.py        # Input/output dataclasses or Pydantic models (if needed)
```

### Steps

1. Create the module directory under `modules/`.
2. Write a docstring in `__init__.py` that explains what the module does in one sentence.
3. Implement the core logic in `engine.py`. Follow the pattern:
   - Accept structured input (dict or dataclass)
   - Return structured output with verdict/score + confidence + source trace
   - Keep all reasoning deterministic (no LLM calls at runtime)
4. Export the public API from `__init__.py`.
5. Add tests in `tests/test_<module_name>.py` (see Testing Requirements below).
6. Add the module to the inventory table in `README.md`.

### Design Principles

- **Deterministic**: Given the same input, produce the same output every time.
- **Traceable**: Every verdict must include the evidence that produced it.
- **Schema-first**: Define input/output schemas before writing logic.
- **No side effects**: Modules should not write files or make network calls.

## How to Extend the Comparability Pipeline

The comparability pipeline lives in `pipelines/comparability.py` and orchestrates multiple modules:

```
Input JSON --> data_harmonizer --> comparability_graph --> evidence_closure --> action_recommender --> Report
```

### Adding a New Stage

1. Identify where your stage fits in the pipeline flow.
2. Create or update the relevant module in `modules/`.
3. Edit `pipelines/comparability.py` to call your module at the right point.
4. Update `pipelines/schemas.py` if you change the pipeline input or output format.
5. Verify all existing benchmark cases still pass:
   ```bash
   python3 -m pytest tests/test_comparability_pipeline.py tests/test_benchmark_cases.py -v
   ```
6. Add a new benchmark case that exercises your stage if it changes verdicts or recommendations.

### Modifying Scoring Logic

If you change how CQA scores or verdicts are computed:
- Run the full benchmark suite and confirm 100% verdict accuracy is maintained.
- If a case legitimately needs a new expected verdict, update the case JSON and document why in the commit message.

## Testing Requirements

### Minimum Requirements for All PRs

- All existing tests pass: `python3 -m pytest tests/ -v`
- New modules must include at least one test file in `tests/`.
- New benchmark cases must pass schema validation and (for COMP cases) verdict matching.
- No hardcoded file paths, API keys, or PII in test data.

### What to Test

| Change Type | Required Tests |
|-------------|----------------|
| New module | Unit tests for core engine logic |
| Pipeline change | Integration test through `test_comparability_pipeline.py` or `test_gap_memo.py` |
| New benchmark case | Passes `test_benchmark_cases.py` automatically |
| Schema change | Validate backward compatibility or update all affected cases |

### Running Tests

```bash
# Full suite
python3 -m pytest tests/ -v

# Single module
python3 -m pytest tests/test_action_recommender.py -v

# Benchmarks only
python3 -m pytest tests/test_benchmark_cases.py -v

# With coverage (if pytest-cov installed)
python3 -m pytest tests/ --cov=modules --cov=pipelines -v
```

## Code Style

- Python 3.11+
- Type hints for all function signatures
- Docstrings for all public functions
- No hardcoded paths, API keys, or PII
- Imports sorted: stdlib, third-party, local

## Submitting Changes

1. Create a feature branch: `git checkout -b feature/your-feature`
2. Make your changes following the guidelines above.
3. Run the full test suite.
4. Commit with a descriptive message explaining what and why.
5. Submit a pull request with a summary of changes and test results.
