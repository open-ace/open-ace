#!/usr/bin/env bash
#
# Open ACE Release Script
# Usage: scripts/release.sh --version 1.1.0 [--dry-run]
#
# This script helps prepare and publish a new release:
# 1. Validate version format (SemVer)
# 2. Update pyproject.toml version
# 3. Update CHANGELOG.md [Unreleased] -> [version]
# 4. Create git commit and tag
# 5. Push tag to origin (unless --dry-run)
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Portable in-place sed: GNU sed uses `sed -i`, BSD sed (macOS default) needs
# `sed -i ''`. Probe the running sed rather than guessing by OS.
if sed --version 2>/dev/null | grep -q GNU; then
    SED_INPLACE=(sed -i)
else
    SED_INPLACE=(sed -i '')
fi

# Parse arguments
VERSION=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --version=*)
            VERSION="${1#--version=}"
            shift
            ;;
        --version)
            VERSION="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 --version 1.1.0 [--dry-run]"
            echo ""
            echo "Options:"
            echo "  --version   Target version number (e.g., 1.1.0)"
            echo "  --dry-run   Show what would be done without making changes"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

if [[ -z "$VERSION" ]]; then
    echo "Error: --version is required"
    echo "Usage: $0 --version 1.1.0 [--dry-run]"
    exit 1
fi

# Validate version format (SemVer: X.Y.Z)
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: Version must follow SemVer format (e.g., 1.1.0)"
    exit 1
fi

TAG="v${VERSION}"
RELEASE_DATE=$(date +%Y-%m-%d)

echo "=== Open ACE Release Preparation ==="
echo "Version: $VERSION"
echo "Tag: $TAG"
echo "Date: $RELEASE_DATE"
echo "Dry run: $DRY_RUN"
echo ""

# Check for uncommitted changes
if git diff-index --quiet HEAD --; then
    echo "✓ Working directory is clean"
else
    echo "Error: Working directory has uncommitted changes"
    echo "Please commit or stash changes before release"
    git status --short
    exit 1
fi

# Check current branch
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [[ "$CURRENT_BRANCH" != "main" ]]; then
    echo "Warning: Not on main branch (current: $CURRENT_BRANCH)"
    echo "Releases should typically be created from main"
fi

# Check if tag already exists
if git tag -l "$TAG" | grep -q "$TAG"; then
    echo "Error: Tag $TAG already exists"
    exit 1
fi

# Update pyproject.toml
echo ""
echo "Step 1: Updating pyproject.toml..."
if [[ "$DRY_RUN" == true ]]; then
    echo "  [dry-run] Would update version to $VERSION"
else
    "${SED_INPLACE[@]}" "s/^version = \".*\"/version = \"$VERSION\"/" "$PROJECT_ROOT/pyproject.toml"
    echo "  ✓ Updated version to $VERSION"
fi

# Update CHANGELOG.md
echo ""
echo "Step 2: Updating CHANGELOG.md..."
CHANGELOG="$PROJECT_ROOT/CHANGELOG.md"

if [[ "$DRY_RUN" == true ]]; then
    echo "  [dry-run] Would replace [Unreleased] with [$TAG] - $RELEASE_DATE"
else
    # Replace [Unreleased] header with new version header.
    # Use awk (not sed) for the multi-line insertion: BSD sed treats `\n` in an
    # s/// replacement as a literal 'n', which would corrupt the CHANGELOG.
    awk -v tag="$TAG" -v date="$RELEASE_DATE" '
        /^## \[Unreleased\]$/ && !done {
            print
            print ""
            print "## [" tag "] - " date
            done = 1
            next
        }
        { print }
    ' "$CHANGELOG" > "$CHANGELOG.tmp" && mv "$CHANGELOG.tmp" "$CHANGELOG"
    echo "  ✓ Added version header to CHANGELOG"

    # Update bottom links: add the new version link before the [Unreleased] link.
    # awk avoids the GNU-only `\n`-in-replacement sed behavior.
    awk -v tag="$TAG" '
        /^\[Unreleased\]: / && !done {
            print "[" tag "]: https://github.com/open-ace/open-ace/releases/tag/" tag
            print "[Unreleased]: https://github.com/open-ace/open-ace/compare/" tag "...HEAD"
            done = 1
            next
        }
        { print }
    ' "$CHANGELOG" > "$CHANGELOG.tmp" && mv "$CHANGELOG.tmp" "$CHANGELOG"
    echo "  ✓ Updated version links"
fi

# Generate changelog summary for release notes
echo ""
echo "Step 3: Generating release notes..."
echo "---"
echo "Release notes preview (from CHANGELOG.md):"
echo ""
# Extract the new version section from CHANGELOG
sed -n "/^## \[$TAG\]/,/^## \[v/p" "$CHANGELOG" | sed '$d'
echo "---"

# Git operations
echo ""
echo "Step 4: Git commit and tag..."
if [[ "$DRY_RUN" == true ]]; then
    echo "  [dry-run] Would commit: Release $TAG"
    echo "  [dry-run] Would create tag: $TAG"
else
    git add "$PROJECT_ROOT/pyproject.toml" "$PROJECT_ROOT/CHANGELOG.md"
    git commit -m "Release $TAG"
    echo "  ✓ Committed release changes"

    git tag -a "$TAG" -m "Release $TAG"
    echo "  ✓ Created tag $TAG"
fi

# Push
echo ""
echo "Step 5: Push to origin..."
if [[ "$DRY_RUN" == true ]]; then
    echo "  [dry-run] Would push tag $TAG to origin"
    echo ""
    echo "=== Dry run complete ==="
    echo "To execute the release, run without --dry-run"
else
    echo "Ready to push tag $TAG to origin."
    echo ""
    read -p "Push tag to origin? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        git push origin "$TAG"
        echo "  ✓ Pushed tag $TAG"
        echo ""
        echo "=== Release $TAG created ==="
        echo "Next steps:"
        echo "  1. Go to https://github.com/open-ace/open-ace/releases/new"
        echo "  2. Select tag $TAG"
        echo "  3. Copy release notes from CHANGELOG.md"
        echo "  4. Publish the release"
    else
        echo "  Skipped push. Tag $TAG created locally."
        echo "  Push manually with: git push origin $TAG"
    fi
fi
