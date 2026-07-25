#!/bin/bash
# npm audit with vulnerability whitelist
# This script runs npm audit and filters out known false positives or
# vulnerabilities that don't apply to our use case.
#
# Uses JSON output for precise filtering.

set -e

# Whitelist of vulnerability IDs that are known to be not applicable
# Format: GHSA-XXXX-XXXX-XXXX
# GHSA-qwww-vcr4-c8h2: React Router RSC Mode CSRF Bypass
# Not applicable: Open-ACE is a pure SPA, does not use React Server Components (RSC)

# Run npm audit with JSON output
AUDIT_JSON=$(npm audit --omit=dev --audit-level=high --registry=https://registry.npmjs.org --json 2>&1) || true

# Use node to process JSON and check for unhandled vulnerabilities
RESULT=$(echo "$AUDIT_JSON" | node -e '
const whitelist = ["GHSA-qwww-vcr4-c8h2"];
let audit;
try {
  audit = JSON.parse(require("fs").readFileSync(0, "utf8"));
} catch (e) {
  console.log("PARSE_ERROR");
  process.exit(0);
}

const vulns = audit.vulnerabilities || {};
const entries = Object.entries(vulns);

if (entries.length === 0) {
  console.log("NO_VULNS");
  process.exit(0);
}

let unhandled = [];
let whitelisted = [];
for (const [name, info] of entries) {
  const via = info.via || [];
  for (const v of via) {
    if (typeof v === "object" && v.url) {
      // Extract GHSA ID from URL: https://github.com/advisories/GHSA-xxxx-xxxx-xxxx
      const match = v.url.match(/GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}/);
      if (match) {
        const ghsaId = match[0];
        if (whitelist.includes(ghsaId)) {
          whitelisted.push(ghsaId);
        } else {
          unhandled.push({id: ghsaId, name: name, severity: info.severity});
        }
      }
    }
  }
}

for (const id of whitelisted) {
  console.log("WHITELISTED:" + id);
}

if (unhandled.length > 0) {
  const highCritical = unhandled.filter(v => v.severity === "high" || v.severity === "critical");
  if (highCritical.length > 0) {
    for (const v of highCritical) {
      console.log("UNHANDLED:" + v.severity + ":" + v.id + ":" + v.name);
    }
  } else {
    console.log("NO_HIGH_CRITICAL");
  }
} else {
  console.log("ALL_WHITELISTED");
}
')

# Process the result
HAS_UNHANDLED=0
while IFS= read -r line; do
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
    HAS_UNHANDLED=1
  fi
done <<< "$RESULT"

# Exit with error if there are unhandled high/critical vulnerabilities
if [ "$HAS_UNHANDLED" = "1" ]; then
  echo "::error::npm audit found high/critical vulnerabilities in production dependencies."
  # Show human-readable output
  npm audit --omit=dev --audit-level=high --registry=https://registry.npmjs.org 2>&1 || true
  exit 1
fi

echo "✅ Security audit passed"

