# Release process

Smart Stock releases are tag-driven and guarded by `.github/workflows/release.yml`.

## Versioning

Stable releases use strict semantic version tags:

```text
vMAJOR.MINOR.PATCH
```

Examples:

```text
v1.0.0
v1.2.3
```

The release workflow intentionally rejects prerelease/build suffixes. Add prerelease support later only with an explicit distribution policy.

Use the version fields as follows:

- **MAJOR**: incompatible public/deployment/data-contract change,
- **MINOR**: backward-compatible capability,
- **PATCH**: backward-compatible fix/hardening.

## Release prerequisites

Before tagging:

1. the intended commit is already merged into `main`,
2. the current `main` CI quality gate is green,
3. production deployment changes and Flyway migrations were reviewed,
4. the release candidate passed local production smoke on the target Docker/runtime family,
5. backup/restore implications were reviewed,
6. the operator has recorded required PostgreSQL/Ollama production image digests separately from the application release.

Do not tag a feature branch. The workflow independently verifies that the tagged commit is contained in `origin/main`.

## Create a release

Update local `main` and create an annotated tag:

```bash
git switch main
git fetch origin main
git merge --ff-only origin/main
git tag -a v1.0.0 -m "Smart Stock v1.0.0"
git push origin v1.0.0
```

Pushing the tag starts the Release workflow.

## Release gate

Before publishing anything, the workflow re-verifies:

- strict `vX.Y.Z` tag format,
- tagged commit ancestry from `main`,
- Python unit tests,
- golden AI evaluation,
- production env/config contract,
- Java verification,
- fresh PostgreSQL Flyway migration + Hibernate validation,
- absence of production demo seed rows,
- frontend lint/tests/build.

This is deliberately redundant with PR CI. Tags are administrative actions outside the pull-request flow, so the release gate does not simply assume a previous CI run was the right one.

The release workflow has `contents: write` and `packages: write`; every reusable GitHub Action in that privileged workflow is therefore pinned to a full reviewed commit SHA rather than a movable `@vN` reference.

## Published application images

After the gate passes, the workflow builds the hardened production Dockerfiles. Each build is first pushed only under its source-commit tag:

```text
ghcr.io/<owner>/smart-stock-stock-service:sha-<full-git-commit>
ghcr.io/<owner>/smart-stock-llm-host:sha-<full-git-commit>
ghcr.io/<owner>/smart-stock-web-ui:sha-<full-git-commit>
```

Only after **all three** image builds have succeeded are the verified digests promoted to the release aliases:

```text
ghcr.io/<owner>/smart-stock-stock-service:vX.Y.Z
ghcr.io/<owner>/smart-stock-llm-host:vX.Y.Z
ghcr.io/<owner>/smart-stock-web-ui:vX.Y.Z
```

The workflow verifies those promoted aliases before creating the GitHub Release. It never publishes a `latest` application tag. Deployments should record and preferably use the returned registry digest rather than relying only on a tag.

PostgreSQL and Ollama are upstream runtime dependencies, not Smart Stock application images. Their production digests remain explicit deployment inputs in `.env.production`.

## GitHub Release manifest

The workflow creates a GitHub Release and attaches `release-manifest.txt`. It records:

- SemVer tag,
- exact Git commit,
- each application image name,
- each pushed application image digest.

Treat the manifest as the release-to-image mapping used by deployment and rollback records.

## Failed release

If verification fails, no application images are published. If one of the three commit-image builds/pushes fails after an earlier one succeeded, source-SHA artifacts may remain in GHCR, but version aliases are not promoted until all three builds have succeeded.

If a later version-promotion or GitHub Release step fails, inspect the registry and workflow state before retrying. Do not move/reuse a failed public version tag after consumers may have observed it. Fix the problem on `main`, choose the next appropriate patch/minor version when needed, and tag the corrected commit.

Avoid force-moving an existing release tag.

## Deployment after release

The Release workflow publishes artifacts; it does **not** deploy to a server automatically. Production deployment remains an explicit operator action:

1. select the reviewed release/tag and application digests,
2. validate `.env.production`,
3. take/verify the required database backup,
4. deploy using the production topology or your infrastructure equivalent,
5. run `scripts/production_smoke.py`,
6. inspect readiness, structured logs and metrics,
7. record the deployed tag, Git SHA and image digests.

This separation prevents a Git tag from becoming an implicit production write operation.

See `docs/production.md` for deployment, migration, backup and rollback details.
