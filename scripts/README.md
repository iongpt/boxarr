# Boxarr Scripts

This directory contains utility scripts for development and CI/CD operations.

## Scripts

### `validate-ci.py`
**Purpose**: Validates that your development environment is properly configured for Boxarr's CI/CD pipeline.

**Usage**:
```bash
python scripts/validate-ci.py
```

**What it checks**:
- Python version compatibility (3.10+)
- Required development dependencies
- Code formatting (Black)
- Linting (Flake8)
- Type checking (MyPy)
- Import sorting (isort)
- Security scanning (Bandit)
- Unit tests (pytest)
- Git repository setup
- Docker build and container startup

**Exit codes**:
- `0`: All checks passed
- `1`: One or more checks failed

**Example output**:
```
🚀 Boxarr CI/CD Validation
========================================
✅ Python 3.11.5 (supported)
✅ All required packages installed

🔍 Checking code formatting...
✅ Code formatting is correct

🔍 Running Flake8 linting...
✅ No linting issues found

[... more checks ...]

========================================
📊 Results: 10 passed, 0 failed
🎉 All checks passed! Your CI/CD setup is ready.
```

## Development Workflow

Before submitting a PR, run the validation script to ensure your code meets CI requirements:

```bash
# Install dependencies
pip install -r requirements.txt

# Run validation
python scripts/validate-ci.py

# If formatting issues are found, fix them:
black src/ tests/
isort src/ tests/

# If tests fail, investigate and fix:
pytest tests/ -v
```

## Adding New Scripts

When adding new scripts to this directory:

1. **Make them executable**: `chmod +x script-name.py`
2. **Add a shebang**: `#!/usr/bin/env python3`
3. **Include documentation**: Add docstrings and comments
4. **Update this README**: Document what the script does
5. **Follow naming convention**: Use kebab-case for script names

## Script Categories

### Development Scripts
- `validate-ci.py` - CI/CD environment validation

### Future Scripts (Planned)
- `setup-dev.sh` - Development environment setup
- `generate-docs.py` - Documentation generation
- `update-dependencies.py` - Dependency update automation
- `benchmark.py` - Performance benchmarking