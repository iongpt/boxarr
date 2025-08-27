# GitHub Actions Setup Guide

This document explains how to configure the required secrets and settings for Boxarr's CI/CD pipeline.

## Required GitHub Secrets

To enable full CI/CD functionality, you need to configure the following secrets in your GitHub repository:

### Repository Secrets Path
Go to: **Settings** → **Secrets and variables** → **Actions** → **Repository secrets**

### Required Secrets

#### Docker Hub Publishing (Optional but Recommended)
- `DOCKERHUB_USERNAME`: Your Docker Hub username
- `DOCKERHUB_TOKEN`: Docker Hub access token (not your password)

To create a Docker Hub access token:
1. Log in to [Docker Hub](https://hub.docker.com/)
2. Go to **Account Settings** → **Security** → **Access Tokens**
3. Click **New Access Token**
4. Give it a descriptive name (e.g., "Boxarr GitHub Actions")
5. Select **Read, Write, Delete** permissions
6. Copy the generated token and add it as `DOCKERHUB_TOKEN` secret

### Automatic Secrets (No Setup Required)
- `GITHUB_TOKEN`: Automatically provided by GitHub Actions
- Used for GitHub Container Registry publishing and repository operations

## Workflow Overview

### CI Pipeline (`ci.yml`)
Triggers on:
- Pull requests to `main` or `develop` branches
- Direct pushes to `main` or `develop` branches

Includes:
- **Code Quality**: Black formatting, Flake8 linting, MyPy type checking, isort import sorting
- **Testing**: Unit tests across Python 3.10, 3.11, and 3.12
- **Security**: Bandit security scanning, Safety dependency vulnerability checks
- **Integration**: Basic integration tests with mock services
- **Docker**: Test Docker image build and health checks

### CD Pipeline (`cd.yml`)
Triggers on:
- Pushes to `main` branch
- Git tags starting with `v*`
- Published releases

Includes:
- **Multi-platform builds**: Linux AMD64 and ARM64
- **Registry publishing**: GitHub Container Registry (always) + Docker Hub (if configured)
- **Security scanning**: Trivy vulnerability scanner with SARIF uploads
- **Deployment testing**: Container health checks and endpoint testing
- **Release automation**: GitHub release creation for version tags
- **Documentation**: Automatic Docker Hub README updates

## Docker Registry Configuration

### GitHub Container Registry (GHCR)
- **Registry**: `ghcr.io`
- **Authentication**: Uses `GITHUB_TOKEN` (automatic)
- **Image path**: `ghcr.io/iongpt/boxarr`
- **Always enabled**: No additional setup required

### Docker Hub Registry
- **Registry**: `docker.io` (Docker Hub)
- **Authentication**: Uses `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`
- **Image path**: `{DOCKERHUB_USERNAME}/boxarr`
- **Optional**: Skipped if secrets not configured

## Image Tags and Versioning

The pipeline automatically creates the following tags:

### For Main Branch Pushes
- `latest` - Latest stable release
- `main-{sha}` - Commit-specific tag

### For Version Tags (e.g., `v1.2.3`)
- `v1.2.3` - Full version
- `1.2.3` - Version without 'v' prefix
- `1.2` - Major.minor version
- `1` - Major version only

### For Pull Requests
- `pr-{number}` - PR-specific builds (GHCR only)

## Branch Protection Rules (Recommended)

Configure branch protection for `main`:

1. Go to **Settings** → **Branches**
2. Add rule for `main` branch
3. Enable:
   - ✅ **Require a pull request before merging**
   - ✅ **Require status checks to pass before merging**
   - ✅ **Require branches to be up to date before merging**
   - ✅ **Required status checks**: Select all CI jobs

## Security Features

### Dependency Scanning
- **Safety**: Checks for known security vulnerabilities in Python dependencies
- **Bandit**: Static security analysis for Python code
- **Trivy**: Container vulnerability scanning

### Supply Chain Security
- **SBOM Generation**: Software Bill of Materials for all images
- **Provenance Attestation**: Build provenance attestation for transparency
- **SARIF Upload**: Security findings uploaded to GitHub Security tab

### Container Security
- **Multi-platform builds**: Supports AMD64 and ARM64 architectures
- **Minimal base images**: Python slim-based images for reduced attack surface
- **Non-root user**: Containers run with restricted permissions

## Troubleshooting

### Common Issues

#### Docker Hub Push Fails
```
Error: failed to solve: failed to push: denied: requested access to the resource is denied
```
**Solution**: Verify `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets are correct

#### GHCR Push Fails
```
Error: failed to push to ghcr.io: insufficient permissions
```
**Solution**: Ensure repository has `packages: write` permission (should be automatic)

#### Tests Fail on CI but Pass Locally
**Common causes**:
- Different Python versions
- Missing test dependencies
- Platform-specific issues
- Environment variable differences

**Solution**: Check the CI logs and ensure your local environment matches the CI matrix

#### Security Scans Fail
**Trivy scan fails**:
- Usually indicates actual vulnerabilities in dependencies
- Review the SARIF report in the Security tab
- Update vulnerable dependencies if possible

**Bandit scan fails**:
- Review security issues flagged by Bandit
- Use `# nosec` comment if false positive (sparingly)
- Fix genuine security issues

### Getting Help

1. Check the **Actions** tab for detailed workflow logs
2. Review the **Security** tab for vulnerability reports
3. Check the **Issues** tab for similar problems
4. Create a new issue with:
   - Workflow run URL
   - Error messages
   - Steps to reproduce

## Monitoring

### Key Metrics to Monitor
- **CI Success Rate**: Should be >95%
- **Build Times**: Watch for performance degradation
- **Security Findings**: Regular review of vulnerability reports
- **Image Sizes**: Monitor for bloat over time

### Notifications
- GitHub notifications for failed workflows
- Security alerts for new vulnerabilities
- Pull request status checks

## Best Practices

1. **Test Locally**: Run `pytest` and linting tools before pushing
2. **Small PRs**: Keep changes focused and reviewable  
3. **Security**: Address security findings promptly
4. **Dependencies**: Keep dependencies up to date
5. **Documentation**: Update docs when adding features
6. **Semantic Versioning**: Use proper version tags for releases