# Releasing `hermes-band`

> The PyPI distribution is **`hermes-band`**. The GitHub repository keeps its
> `hermes-band-platform` name, and the import package stays `hermes_band_platform`.
> `hermes-band-platform` on PyPI belongs to an unrelated third party — never
> publish there. See INT-1137.

Publishing follows `band-ai/band-sdk-python`, which ships `band-sdk` on every
run: **release-please** turns conventional-commit history into version bumps + a
changelog + a GitHub Release, and the Release then triggers the publishes.
**Diff against that repo first when releases break.**

Three workflows, one direction of causation:

| Workflow | Trigger | Does |
| -- | -- | -- |
| [`release.yml`](.github/workflows/release.yml) | push to `main` | release-please: release PR → tag → GitHub Release. Publishes nothing. |
| [`pypi-publish.yml`](.github/workflows/pypi-publish.yml) | `release: published` | build with uv → upload `hermes-band` to PyPI via OIDC |
| [`package.yml`](.github/workflows/package.yml) | `release: published` | attach the drop-in directory-plugin bundles to the Release |

Both publishes hang off the **durable GitHub Release**, not off a job inside
`release.yml`. So a failure after the tag can't strand a tagged release
unpublished, and either publish is re-runnable on its own without re-releasing.

## Flow

```
merge to `main`
   →  release-please opens a "release" PR  →  merge it
   →  release-please tags vX.Y.Z + creates the GitHub Release
   →  pypi-publish: uv build → PyPI (OIDC)
   →  package: directory-plugin .tar.gz/.zip onto the Release
```

- Conventional-commit PR titles drive the version bump (`feat:` → minor,
  `fix:` → patch, `feat!:`/`BREAKING CHANGE` → major). Enforced by
  [`pr-title.yml`](.github/workflows/pr-title.yml).
- `main` is the only long-lived branch. Work on feature branches and PR into it.
- Merging the release PR is the publish trigger — there is no manual upload step.
  `workflow_dispatch` on `release.yml` just re-runs release-please.
- Tags are plain `vX.Y.Z` (`include-component-in-tag: false`). `pypi-publish.yml`
  checks that format before checkout, then re-checks after checkout that the tag
  sits on `main` and matches `pyproject.toml`'s version — so changing the tag
  shape means updating both checks.

release-please keeps every version location in sync (configured in
[`release-please-config.json`](release-please-config.json)): `pyproject.toml`,
`hermes_band_platform/__init__.py` (`__version__`), `hermes_band_platform/plugin.yaml`,
and `flake.nix` (the last two via the `# x-release-please-version` annotations).
`.release-please-manifest.json` tracks the current released version, and
`release.yml` regenerates the root `plugin.yaml` on the release PR.

### First release: pin `1.0.0`

`pyproject.toml` and `.release-please-manifest.json` both already read `1.0.0`,
no tag has ever been cut, and a `feat(packaging)` commit already sits on
`main` — so left alone, release-please's first release PR computes **1.1.0**,
not `1.0.0`. To debut on `1.0.0`, a commit landing on `main` must carry a
`Release-As: 1.0.0` trailer in its commit body; release-please reads the
trailer from commits on `main`, not from a PR branch.

Both squash merge (`squash_merge_commit_message: COMMIT_MESSAGES`) and merge
commits are enabled on this repo. Under a squash merge, the trailer from *any*
commit on the branch survives into the squashed commit body that lands on
`main`; under a merge commit, it has to be in the merge commit's own body.

## One-time setup (required before the first publish)

1. **GitHub App access — two separate lists, both required.** `release.yml`
   mints a token with `actions/create-github-app-token@v3`, which needs
   `APP_CLIENT_ID` and `APP_PRIVATE_KEY`. Both already exist as **`band-ai` org
   secrets** for the **`band-release-please-public`** App — nothing new to mint.
   What has to be granted is:

   | List | Where | Controls |
   | -- | -- | -- |
   | Secret → Repository access | org Settings → Secrets and variables → Actions → each secret | whether this repo can **read** the two secrets |
   | App installation → Repository access | org Settings → GitHub Apps → `band-release-please-public` | whether the minted token can **act on** this repo |

   Scoping the secrets alone is not enough: the token mints fine and then
   release-please fails on the write instead of the read. `band-sdk-python` is on
   both lists; this repo had to be added to both.

   A secret that isn't scoped to this repo arrives as an **empty string** with no
   warning — the shape that cost this pipeline every run from 2026-06-18 to
   2026-07-26 — so `release.yml` preflights for it and fails naming the secret.

2. **`release` environment** — GitHub → Settings → Environments → `release`.
   Add a **deployment branch and tag policy**: branch `main` and tag `v*`.
   That policy is load-bearing, not cosmetic: it stops a doctored copy of
   `pypi-publish.yml` on a side branch from ever reaching the OIDC credential,
   and it's also what confines `release.yml` itself to `main` — a
   `workflow_dispatch` of that workflow from a side branch is blocked the same
   way, before the job starts. Keep it.

   Don't add required reviewers here expecting an upload-only gate:
   `release.yml`'s release job runs under this same environment, so reviewers
   would block every release-please run on `main`, not just the PyPI upload.
   An upload-only gate needs a second environment (e.g. `pypi`) that only
   `pypi-publish.yml`'s publish job references — and the PyPI trusted
   publisher's registered environment name updated to match.

3. **PyPI Trusted Publisher** — add a *pending publisher* for the project (it
   doesn't exist on PyPI yet). Add it **from inside the
   [`Band` PyPI organization](https://pypi.org/org/Band/)** — the org that
   already owns `band-sdk`, `band-mcp`, `band-client-rest`, `codeband`,
   `band-testing-python`, and `phoenix-channels-python-client` — so the project
   is owned by the org rather than one person's account:

   - PyPI project name: `hermes-band`
   - Owner: `band-ai` — the **GitHub** org. Trusted Publishing keys off GitHub,
     which is independent of the PyPI org the project lives in.
   - Repository: `hermes-band-platform` (the repo name, unchanged)
   - **Workflow name: `pypi-publish.yml`** — the file that actually uploads, not
     `release.yml`. A publisher registered against the wrong filename does not
     authorize the upload.
   - Environment name: `release`

   > Note the PyPI org slug is `Band` (case-sensitive; `band` redirects to it),
   > *not* `band-ai` — that 404s and is easy to mistake for "we have no org".

   > A pending publisher does **not** reserve the name: per
   > [PyPI's docs](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
   > it "does not create a project or reserve a project's name until it is
   > actually used to publish", and someone else registering the name first
   > invalidates it. The name is only ours once an upload lands.

4. **Branch protection** — protect `main` (require PR + passing CI).

Until step 3 is done the upload step fails by design — nothing leaks.

## Verify a published release

```bash
pip install hermes-band                 # also pulls in band-sdk
python -c "import hermes_band_platform; print(hermes_band_platform.__version__)"
```

## Recovery

A tagged release whose upload failed is republished without cutting a new
version: **Actions → pypi-publish → Run workflow**, passing the existing tag
(e.g. `v1.0.0`). `skip-existing: true` makes that idempotent, so a re-run after a
partial upload won't fail on files already on PyPI. `pypi-publish.yml` also holds
a workflow-level concurrency group, so a recovery dispatch for a version that's
still uploading queues behind the in-flight run instead of racing it — no need
to confirm the first run finished before dispatching.

Because the `release` event only fires workflows present **at the released tag**,
tags cut before `pypi-publish.yml` existed can only be published by dispatch.

## Notes / deviations from `band-sdk-python`

- **The secrets preflight in `release.yml` is ours**, kept deliberately: this
  repo burned 7 runs on an unscoped org secret arriving as an empty string, and
  six lines that name the missing secret beat rediscovering that.
- The build runs `uv build` only (no `uv sync`): the package's single runtime dep
  (`band-sdk`) isn't needed to *build* the wheel and need not resolve at build
  time. Consequently there is no `uv.lock` sync step on the release PR either.
- No TestPyPI stage (the SDK doesn't have one either). Ask if you want a
  `workflow_dispatch` dry-run-to-TestPyPI job added.
- No kit image, no cross-repo pin bump: `band-sdk-python`'s `publish-kit` and
  `bump-add-band` jobs have no counterpart here. `package.yml` is this repo's
  extra release artifact, and it already keys off the Release event.
- Actions are commit-pinned by SHA with a trailing version comment;
  [`dependabot.yml`](.github/dependabot.yml) moves those pins weekly.
