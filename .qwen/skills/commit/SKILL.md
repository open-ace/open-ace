---
name: commit
description: Stage all changes, generate commit message, and commit
---

# Steps
1. Run `git status` to see changed files
2. Run `git diff` to review changes
3. Generate a descriptive commit message based on the changes
4. Run `git add -A` to stage all changes
5. Run `git commit -m "<message>"` with the generated message
6. Show the commit hash and summary

# Commit Message Format
- Use conventional commit format: `type: description`
- Types: feat, fix, docs, style, refactor, test, chore
- Keep the first line under 72 characters
- Add body if needed to explain the "why"

# Example
```
feat: add user authentication module

- Add login/logout API endpoints
- Implement session management
- Add password hashing with bcrypt
```