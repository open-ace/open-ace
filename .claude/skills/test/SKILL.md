---
name: test
description: Run tests for the current project
---

# Steps
1. Detect project type by checking for test configuration files:
   - Python: `pytest.ini`, `setup.py`, `pyproject.toml`, `requirements.txt`
   - Node.js: `package.json` with test script
   - Go: `*_test.go` files
   - Rust: `Cargo.toml`
2. Run the appropriate test command:
   - Python: `pytest` or `python -m pytest`
   - Node.js: `npm test` or `yarn test`
   - Go: `go test ./...`
   - Rust: `cargo test`
3. Report test results (passed, failed, skipped)
4. If tests fail, analyze the output and suggest fixes

# Test Detection
Check for these files in order:
- `pytest.ini` → pytest
- `pyproject.toml` with `[tool.pytest]` → pytest
- `package.json` with `"test"` script → npm test
- `Makefile` with `test` target → make test
- `Cargo.toml` → cargo test
- `go.mod` → go test

# Example Output
```
Running tests with pytest...
=========================
12 passed, 2 failed in 3.45s

Failed tests:
- test_user_login: AssertionError on line 42
- test_api_response: ConnectionError

Suggested fixes:
1. Check user credentials in test_user_login
2. Verify API server is running for test_api_response
```