# Legacy issue test quarantine

`tests/issues/<number>/` is retained only for the historical suites listed in
`legacy-directories.txt`. It is not the destination for new tests.

The directory mixes unit, integration, browser, performance, privileged-host,
and manual verification scripts. Treating them as one suite made their runtime
contract unclear and caused the entire tree to be excluded from normal CI.

For a new bug fix, put the test in exactly one canonical directory according to
what it needs to run:

- `tests/unit/`: in-process and fast; no network, real database, subprocess, or server.
- `tests/integration/`: crosses a database, filesystem, subprocess, or component boundary.
- `tests/e2e/`: needs a running Open ACE server, browser, or remote service.
- `tests/performance/`: asserts timing or resource behavior.

Security is expressed with `pytest.mark.security` in whichever runtime layer
owns the test; it is not a separate directory.

Record provenance without copying the file:

```python
import pytest

pytestmark = [pytest.mark.security, pytest.mark.regression, pytest.mark.issue(2429)]
```

Run a migrated regression with `pytest --issue=2429`. Legacy tests are marked
automatically from their directory and can be selected with
`pytest tests/issues --issue=517`.

When migrating an existing suite, move each test to its canonical layer, make
its dependencies deterministic, verify it is exercised by that layer's CI
lane, and then remove the old file. Never keep a second copy here.
