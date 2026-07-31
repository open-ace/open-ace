# CI Fix Required - Missing Git Tracked Files

## Summary

Found and fixed code issues related to `SecretHolder` integration, but CI will continue failing until git tracking issues are resolved by the orchestrator.

## Issues Found and Fixed

### 1. SecretHolder Serialization Bug (FIXED)

**Problem**: `serialize_provider_config()` in `app/modules/sso/manager.py` assumed `client_secret` is a string, but `deserialize_provider_config()` now wraps it in a `SecretHolder` object.

**Symptoms**: Test failure with `AttributeError: 'SecretHolder' object has no attribute 'encode'`

**Fix Applied**:
- Modified `serialize_provider_config()` to detect `SecretHolder` instances and call `.get()` to extract plaintext before encryption
- Updated test assertions to expect `SecretHolder` objects instead of raw strings

**Files Modified**:
1. `app/modules/sso/manager.py:90-106` - Handle SecretHolder in serialize_provider_config
2. `tests/routes/test_sso_provider_storage.py:26` - Import SecretHolder
3. `tests/routes/test_sso_provider_storage.py:124-129` - Update assertions to use SecretHolder.get()

**Verification**:
```bash
python -m pytest tests/unit/test_sso_manager_security.py tests/routes/test_sso_provider_storage.py tests/1826/test_sso_security_improvements.py -v
# Result: All 38 tests PASSED
```

## Issues Requiring Orchestrator Action

### 2. Missing Git Tracked File (CRITICAL)

**Problem**: `app/modules/sso/secret_holder.py` exists on disk but is NOT tracked in git.

**Evidence**:
```bash
$ git ls-files app/modules/sso/secret_holder.py
# No output (not tracked)

$ git show HEAD:app/modules/sso/secret_holder.py
fatal: path 'app/modules/sso/secret_holder.py' exists on disk, but not in 'HEAD'
```

**Impact**: CI test jobs fail with `ModuleNotFoundError: No module named 'app.modules.sso.secret_holder'`

**Required Action**:
```bash
git add app/modules/sso/secret_holder.py
git commit -m "fix: add missing secret_holder.py to git tracking"
git push
```

### 3. Invalid Git Submodules (CRITICAL)

**Problem**: Three `.worktrees/` directories were added as git submodules (mode 160000) but have no `.gitmodules` configuration.

**Evidence**:
```bash
$ git diff --name-status origin/main HEAD | grep worktrees
A	.worktrees/082fbaf2-d1b4-4075-17f-1d628c44b357
A	.worktrees/63f63269-4e12-42d6-86f3-c3c41b7eea42
A	.worktrees/f566fa56-38a3-4868-aaa2-f79e9655b2c4
```

**Impact**: schema-sync CI job fails during `actions/checkout` with:
```
fatal: No url found for submodule path '.worktrees/082fbaf2-d1b4-4075-917f-1d628c44b357' in .gitmodules
```

**Required Action**:
```bash
git rm --cached -r .worktrees/
# Note: .gitignore already contains .worktrees/, so they will be ignored
git commit -m "fix: remove invalid worktree submodules from git index"
git push
```

## Test Results After Code Fixes

### Passing Tests
- ✅ All SSO security tests (38 tests)
- ✅ All issue #2163 tests (59 tests)
- ✅ Module imports work correctly
- ✅ TenantResolver functionality verified

### Expected CI Behavior After Orchestrator Actions

Once the orchestrator performs the git operations above:

1. **test (3.12) job**: Will pass because `secret_holder.py` will be available
2. **schema-sync job**: Will pass because worktree submodules will be removed
3. **All other test jobs**: Should pass (no other code issues detected)

## Root Cause Analysis

### Why secret_holder.py was not tracked

The file was created during development but never added to git. Other modified files (manager.py, oauth2.py, provider.py) import it, causing CI to fail when it's not in the repository.

### Why worktrees were added as submodules

The `.worktrees/` directories contain git worktrees created by the auto-dev system. They were mistakenly added to the git index with submodule mode (160000) instead of being excluded via `.gitignore`.

## Files Modified in This Session

1. `app/modules/sso/manager.py` - Fixed serialize_provider_config to handle SecretHolder
2. `tests/routes/test_sso_provider_storage.py` - Updated test assertions

## Files Requiring Git Add (By Orchestrator)

1. `app/modules/sso/secret_holder.py` - New file, must be added

## Files Requiring Git Remove (By Orchestrator)

1. `.worktrees/082fbaf2-d1b4-4075-917f-1d628c44b357` - Invalid submodule
2. `.worktrees/63f63269-4e12-42d6-86f3-c3c41b7eea42` - Invalid submodule
3. `.worktrees/f566fa56-38a3-4868-aaa2-f79e9655b2c4` - Invalid submodule
