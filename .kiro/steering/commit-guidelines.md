---
inclusion: auto
description: Enforces Conventional Commits format for all commit messages
---

# Commit Message Guidelines

## CRITICAL: All commits MUST follow Conventional Commits format

When making changes to this repository, you MUST use the following commit format:

```
<type>(<scope>): <subject>

[optional body]

[optional footer]
```

## Types

- `feat`: New feature
- `fix`: Bug fix
- `perf`: Performance improvement
- `refactor`: Code refactoring
- `style`: UI/styling changes
- `docs`: Documentation only
- `test`: Adding/updating tests
- `chore`: Maintenance tasks
- `build`: Build system changes
- `ci`: CI/CD changes

## Scopes for meduseld (Backend)

- `panel` - Control panel features
- `api` - API endpoints
- `auth` - Authentication
- `logs` - Logging system
- `config` - Configuration
- `monitoring` - Server monitoring
- `proxy` - Jellyfin/SSH proxy
- `health` - Health checks
- `backup` - Backup functionality
- `server` - Game server control
- `release` - Version releases

## Examples

```bash
feat(panel): Add player count display
fix(auth): Resolve redirect loop on macOS browsers
style(footer): Update version to 0.4.0-alpha
refactor(routes): Clean up catch-all route logic
perf(monitoring): Reduce stats collection interval
docs(readme): Update deployment instructions
chore(deps): Update Flask to 3.0.0
```

## Rules

1. **Subject line**: Max 100 characters, MUST be sentence-case (first letter capitalized, e.g. "Add feature" not "add feature"), no period at end
2. **Scope**: Always include a scope from the list above (enforced by commitlint, warning if empty)
3. **Body**: Optional, use for detailed explanations
4. **Footer**: Optional, use for breaking changes or issue references

## Breaking Changes

Add `!` after the type for breaking changes:

```bash
feat(api)!: change authentication to Discord OAuth

BREAKING CHANGE: All existing auth tokens are now invalid
```

## Multi-line Commits

```bash
git commit -m "fix(logs): resolve system log access issues

- Change to absolute paths for production
- Add automatic log directory creation
- Improve error messages for permission issues

Fixes #42"
```

## Validation

Commits are automatically validated via git hooks. Invalid commits will be rejected with an error message.

## When Making Changes

Always commit your changes using this format after completing each logical unit of work. Do not wait for the user to ask — commit automatically when changes are done. The repository has commitlint configured to enforce these rules.
