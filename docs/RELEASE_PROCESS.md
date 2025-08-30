# Release Process

This document describes the release process for Boxarr.

## Changelog Management

We use a **hybrid approach** combining manual changelog maintenance with automated release generation.

### During Development

1. **Update CHANGELOG.md**: When merging PRs, add entries to the `[Unreleased]` section
2. **Use Conventional Commits**: When merging, use meaningful messages:
   ```bash
   # Good examples:
   fix: Add auto-add override for historical weeks (#12)
   feat: Add scheduler debug UI (#11)
   fix: Dashboard not updating after adding movies (#10)
   
   # Avoid:
   Merge pull request #12 from branch-name
   ```

### Categories for CHANGELOG.md

- **Added**: New features
- **Changed**: Changes in existing functionality
- **Deprecated**: Soon-to-be removed features
- **Removed**: Removed features
- **Fixed**: Bug fixes
- **Security**: Security vulnerability fixes

### Release Steps

1. **Update CHANGELOG.md**:
   - Move entries from `[Unreleased]` to a new version section
   - Add the version number and date
   - Example: `## [1.0.2] - 2025-08-30`

2. **Commit the changelog**:
   ```bash
   git add CHANGELOG.md
   git commit -m "docs: Update CHANGELOG for v1.0.2"
   git push origin main
   ```

3. **Run the release script**:
   ```bash
   ./scripts/release v1.0.2
   ```
   
   This script will:
   - Update version in `pyproject.toml`
   - Update fallback version in `src/version.py`
   - Commit version changes
   - Create and push a git tag
   - Trigger the CD pipeline

4. **CD Pipeline automatically**:
   - Builds multi-architecture Docker images
   - Pushes to GitHub Container Registry
   - Creates GitHub Release using the CHANGELOG.md content
   - Adds Docker image information to the release

## Best Practices

### For Pull Requests

When creating PRs, use descriptive titles that can be used directly in merge commits:
- ✅ `fix: Add auto-add override for historical weeks`
- ✅ `feat: Implement scheduler debug UI`
- ❌ `Fix stuff`
- ❌ `Updates`

### For Merge Commits

When merging PRs via GitHub:
1. Choose "Squash and merge" for feature branches with many commits
2. Edit the commit message to be meaningful
3. Include the PR number for reference: `fix: Description (#12)`

### For Direct Commits

Use conventional commit format:
```
<type>: <description>

[optional body]
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `test`: Test additions or changes
- `chore`: Build process or auxiliary tool changes

## Version Numbering

We follow [Semantic Versioning](https://semver.org/):
- **MAJOR.MINOR.PATCH** (e.g., 1.0.2)
- **MAJOR**: Breaking changes
- **MINOR**: New features (backward compatible)
- **PATCH**: Bug fixes (backward compatible)

## Emergency Hotfix Process

For critical bugs in production:

1. Create hotfix branch from the release tag:
   ```bash
   git checkout -b hotfix/critical-bug v1.0.2
   ```

2. Fix the issue and update CHANGELOG.md

3. Merge to main and immediately release:
   ```bash
   git checkout main
   git merge hotfix/critical-bug
   ./scripts/release v1.0.3
   ```

## Changelog Template

When starting a new development cycle, add this to the top of CHANGELOG.md:

```markdown
## [Unreleased]

### Added
- 

### Changed
- 

### Fixed
- 

### Removed
- 
```

Remove empty sections when creating the release.