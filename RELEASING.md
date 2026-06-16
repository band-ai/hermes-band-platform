# Releasing `hermes-band-platform`

Publishing uses the same model as `thenvoi-sdk-python`: **release-please** turns
conventional-commit history into version bumps + a changelog + a GitHub release,
and the release then builds with **uv** and publishes to **PyPI** via Trusted
Publishing (OIDC — no API tokens or secrets stored for the upload). The pipeline
is [`.github/workflows/release.yml`](.github/workflows/release.yml).

## Flow

```
work on `dev`  →  Promote Dev to Main (PR)  →  merge to `main`
   →  release-please opens a "release" PR  →  merge it
   →  release-please tags + creates the GitHub Release
   →  publish job: uv build → upload to PyPI (OIDC)
```

- Conventional-commit PR titles drive the version bump (`feat:` → minor,
  `fix:` → patch, `feat!:`/`BREAKING CHANGE` → major). Enforced by
  [`pr-title.yml`](.github/workflows/pr-title.yml).
- `dev` is the working branch; **Actions → Promote Dev to Main** opens the PR to
  `main` ([`promote-dev-to-main.yml`](.github/workflows/promote-dev-to-main.yml)).
- Merging the release PR is the publish trigger — there is no manual upload step.
  `workflow_dispatch` on `release.yml` just re-runs release-please.

release-please keeps every version location in sync (configured in
[`release-please-config.json`](release-please-config.json)): `pyproject.toml`,
`hermes_band_platform/__init__.py` (`__version__`), `hermes_band_platform/plugin.yaml`,
and `flake.nix` (the last two via the `# x-release-please-version` annotations).
`.release-please-manifest.json` tracks the current released version.

## One-time setup (required before the first publish)

1. **GitHub App token** — `release.yml` and `promote-dev-to-main.yml` mint a
   token via `.github/actions/GithubToken`, so the repo (or org) needs these
   secrets, same as `thenvoi-sdk-python`:
   - `APP_ID`
   - `INSTALLATION_ID`
   - `APP_PRIVATE_KEY`
   If they're org-level secrets shared across the `band-ai` org, this repo
   inherits them. Until they exist, the `release` job fails on push to `main`.
2. **`release` environment** — GitHub → Settings → Environments → create
   `release` (add required reviewers there if you want an approval gate before
   the PyPI upload).
3. **PyPI Trusted Publisher** — on PyPI, add a trusted publisher for the project
   (or a *pending publisher* before it exists):
   - Owner: `band-ai`
   - Repository: `hermes-band-platform`
   - Workflow name: `release.yml`
   - Environment name: `release`
4. **Branch model** — make `dev` the default working branch and protect `main`.

Until step 3 is done the upload step fails by design — nothing leaks.

## Verify a published release

```bash
pip install hermes-band-platform        # also pulls in band-sdk
python -c "import hermes_band_platform; print(hermes_band_platform.__version__)"
```

## Notes / deviations from `thenvoi-sdk-python`

- The publish job runs `uv build` only (no `uv sync` / git-HTTPS step): the
  package's single runtime dep (`band-sdk`) isn't needed to *build* the wheel, and
  `band-sdk` need not be resolvable at build time.
- No TestPyPI stage (the SDK doesn't have one either). Ask if you want a
  `workflow_dispatch` dry-run-to-TestPyPI job added.
- The plugin's own test CI (`ci.yml`) and the directory-bundle release
  (`package.yml`) are unchanged; the latter still attaches drop-in bundles to
  each GitHub Release that release-please creates.
