# UMC registry

A small catalog for sandboxed `.umc` plugins. Git stores manifests; release assets store scripts. Forking this repository does not download the scripts.

Browse and submit at **https://neutro74.github.io/umc-registry/** once Pages is enabled.

## Share a plugin

1. Put a `.umc` file in a public GitHub release or public repository you own.
2. Open the sharing page, choose that file and paste its release-asset or raw-file URL.
3. Download the generated manifest, then use the page's GitHub link to propose it at `plugins/<id>/<version>.json`. GitHub will guide you through creating a fork and pull request if needed.

Submissions contain only four fields:

```json
{
  "id": "example",
  "version": "1.0.0",
  "sha256": "<64 lowercase hex characters>",
  "source": "https://github.com/YOUR_ACCOUNT/YOUR_REPO/releases/download/v1/example.umc"
}
```

The source account must match the PR author. A plugin ID keeps the same owner across versions. Published versions cannot be changed or deleted through automatic submissions. Dependencies must name cataloged versions (or other new manifests in the same PR).

Automation accepts PRs that add 1–16 valid manifests. It checks source size, hash, compiler identity, syntax, dependency cycles, version conflicts and permissions. It never executes submitted plugins and has no AI reviewer. PRs changing registry code, workflows or existing manifests are outside automatic acceptance.

After acceptance, publishing mirrors the verified scripts into registry releases and updates `index.tsv`, `catalog.json` and the Pages catalog. Clients verify hashes again during installation. Existing mirrored packages keep working if the author's source disappears.

## Repository setup

From a terminal with GitHub access:

```sh
bash setup.sh
```

The setup script creates the public repository (or pushes to an existing empty one), enables Pages with Actions and starts publication. It never force-pushes. It requires an authenticated `gh` CLI and permission to manage the repository.

Enable **Settings → Actions → General → Allow GitHub Actions to create and approve pull requests**. Permit workflow tokens to merge catalog PRs; required human reviews would block automatic acceptance. If an organization policy prevents token writes, adjust that policy or use a scoped GitHub App token.

Both workflows pin the UMC compiler commit. Update that pin deliberately when the language changes. New plugins are compiler-checked, not gameplay-tested. Source downloads are limited to public GitHub hosts and 256 KB; compiler subprocesses have time and memory limits and receive no token environment.

## Local tests

```sh
python3 -m unittest discover -s test
node test/site_test.js
```

Set `UMC_TEST_BINARY=/path/to/umc` to run actual compiler checks too. The tests use fake downloads and API responses, so they do not create releases, merge PRs or require network access.
