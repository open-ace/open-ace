#!/bin/bash
# npm audit with vulnerability whitelist
# This script runs npm audit and filters out known false positives or
# vulnerabilities that don't apply to our use case.

set -e

# Whitelist of vulnerability IDs that are known to be not applicable
# Format: GHSA-XXXX-XXXX-XXXX
WHITELIST=(
  # GHSA-qwww-vcr4-c8h2: React Router RSC Mode CSRF Bypass
  # Not applicable: Open-ACE is a pure SPA, does not use React Server Components (RSC)
  "GHSA-qwww-vcr4-c8h2"
)

# Run npm audit
AUDIT_OUTPUT=$(npm audit --omit=dev --audit-level=high --registry=https://registry.npmjs.org 2>&1) || true

# Check if there are vulnerabilities
if echo "$AUDIT_OUTPUT" | grep -q "vulnerabilities"; then
  # Check each whitelisted vulnerability
  for ghsa in "${WHITELIST[@]}"; do
    if echo "$AUDIT_OUTPUT" | grep -q "$ghsa"; then
      echo "::warning::Ignoring $ghsa - vulnerability does not apply to our use case"
      # Remove the vulnerability from output (simplified - just check if it's the only one)
      AUDIT_OUTPUT=$(echo "$AUDIT_OUTPUT" | grep -v "$ghsa" || true)
    fi
  done
  
  # Check if there are still unhandled vulnerabilities
  if echo "$AUDIT_OUTPUT" | grep -E "[0-9]+ (high|critical) severity"; then
    echo "::error::npm audit found high/critical vulnerabilities in production dependencies."
    echo "$AUDIT_OUTPUT"
    exit 1
  fi
fi

echo "✅ Security audit passed"
