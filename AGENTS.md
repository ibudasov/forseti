# Agent Guidelines

Check relevant instructions in the folder `./.github/instructions/*.*`

## Environment Configuration

Docker Compose is the first-class runtime setup. Every environment variable
defined in `.env` or `.env.example` must be represented in the relevant
Compose service environment and be available inside the container. Use
Compose `env_file` / `--env-from-file` to load the file instead of duplicating
the variable-name list in YAML or Make targets. Keep test-only overrides
explicit in the relevant Makefile test target.
