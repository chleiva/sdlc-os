# Fixture Repo Conventions

This is a synthetic, minimal multi-package repository used only by D3's
own test suite (services/index-server/tests) to prove the index server
against real cross-package references. It is not a real project.

- Build/test: `pytest` from the repo root.
- Style: standard library only, no external dependencies.
- Packages: `pkg_a` (billing) and `pkg_b` (reporting), each an
  independent Python package with its own `pyproject.toml` marker.
- Boundary: `pkg_b` may import from `pkg_a`; `pkg_a` must never import
  from `pkg_b` (one-directional dependency, deliberately, so the
  cross-package find-references fixture has an unambiguous direction).
