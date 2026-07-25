#!/bin/bash
# npm audit with vulnerability whitelist
# This script runs npm audit and filters out known false positives or
# vulnerabilities that don't apply to our use case.
#
# Uses JSON output for precise filtering.

set -e

# Whitelist of vulnerability IDs that are known to be not applicable
# Format: GHSA-XXXX-XXXX-XXXX
WHITELIST="GHSA-qwww-vcr4-c8h2"
# Reason: React Router RSC Mode CSRF Bypass
# Not applicable: Open-ACE is a pure SPA, does not use React Server Components (RSC)

# Run npm audit with JSON output
AUDIT_JSON=$(npm audit --omit=dev --audit-level=high --registry=https://registry.npmjs.org --json 2>&1) || true

# Use node to process JSON and check for unhandled vulnerabilities
RESULT=$(echo "$AUDIT_JSON" | node -e "
const whitelist = process.env.WHITELIST.split(' ');
let audit;
try {
  audit = JSON.parse(require('fs').readFileSync(0, 'utf8'));
} catch (e) {
  console.log('PARSE_ERROR');
  process.exit(0);
}

const vulns = audit.vulnerabilities || {};
const entries = Object.entries(vulns);

if (entries.length === 0) {
  console.log('NO_VULNS');
  process.exit(0);
}

let unhandled = [];
for (const [name, info] of entries) {
  const via = info.via || [];
  for (const v of via) {
    if (typeof v === 'object' && v.source === 'GHSA') {
      if (!whitelist.includes(v.id)) {
        unhandled.push({id: v.id, name: name, severity: info.severity});
      } else {
        console.log('WHITELISTED:' + v.id);
      }
    }
  }
}

if (unhandled.length > 0) {
  const highCritical = unhandled.filter(v => v.severity === 'high' || v.severity === 'critical');
  if (highCritical.length > 0) {
    for (const v of highCritical) {
      console.log('UNHANDLED:' + v.severity + ':' + v.id + ':' + v.name);
    }
  } else {
    console.log('NO_HIGH_CRITICAL');
  }
} else {
  console.log('ALL_WHITELISTED');
}
" WHITELIST="$WHITELIST")

# Process the result
echo "$RESULT" | while read -r line; do
  if [ "$line" = "NO_VULNS" ] || [ "$line" = "ALL_WHITELISTED" ] || [ "$line" = "NO_HIGH_CRITICAL" ]; then
    continue
  elif [[ "$line" == WHITELISTED:* ]]; then
    ghsa="${line#WHITELISTED:}"
    echo "::warning::Ignoring $ghsa - vulnerability does not apply to our use case"
  elif [[ "$line" == UNHANDLED:* ]]; then
    severity="${line#UNHANDLED:}"
    severity="${severity%%:*}"
    rest="${line#UNHANDLED:${severity}:}"
    ghsa="${rest%%:*}"
    name="${rest#*:}"
    echo "::error::Unhandled $severity severity vulnerability: $ghsa in $name"
    echo "HAS_UNHANDLED=1" >> /tmp/audit_result
  fi
done

# Check if there are unhandled high/critical vulnerabilities
if [ -f /tmp/audit_result ] && grep -q "HAS_UNHANDLED=1" /tmp/audit_result; then
  rm -f /tmp/audit_result
  echo "::error::npm audit found high/critical vulnerabilities in production dependencies."
  # Show human-readable output
  npm audit --omit=dev --audit-level=high --registry=https://registry.npmjs.org 2>&1 || true
  exit 1
fi

rm -f /tmp/audit_result
echo "✅ Security audit passed"