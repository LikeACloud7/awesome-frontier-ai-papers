# Security Policy

## Reporting an issue

Please do not publish credentials, private data, or a working exploit in a public issue.

Use the repository's **Report a vulnerability** option if private vulnerability reporting is available: [security advisories](https://github.com/LikeACloud7/awesome-frontier-ai-papers/security/advisories). If it is unavailable, open an issue requesting a private reporting channel without disclosing the sensitive details.

Include the affected component, revision, reproduction steps, expected impact, and any proposed fix. Vulnerabilities in linked papers, publisher websites, or model repositories should also be reported to their maintainers.

## Security boundaries

- **Remote publications are untrusted input.** Collector requests permit public HTTP(S) destinations only. DNS results are checked, connections use validated IPs with hostname-verified TLS, redirects are checked individually, and credentials are removed when the origin changes. Ambient HTTP proxy settings are not used.
- **Resource use is bounded.** Downloads have byte limits, redirects have a hop limit, and PDF text extraction runs in a separate process with a wall-clock limit. Collector credentials are excluded from the child environment. CPU limits apply on POSIX systems; Linux CI also has data/address-space memory limits. macOS relies on the time/CPU limits rather than a hard memory cap. This process separation is not a general-purpose sandbox.
- **Publication jobs have separate permissions.** Collection and frontend builds have read-only repository access. A separate job has repository write permission, validates a data-only artifact, and regenerates Markdown using trusted code; it does not run the collector or install third-party Python/npm packages. The push token is explicitly passed only to its push step, although actions in that job retain access to the job token. Only the deployment job has Pages/OIDC permissions.
- **Generated output is escaped.** Paper titles cannot insert Markdown/HTML markup through the generator. Unsafe link schemes and local literal addresses are blocked, and lab file names are restricted to known safe identifiers. Browser links are checked independently.
- **Dependencies are reproducible.** Direct workflow action references use full commit SHAs, npm uses its lockfile and exact direct versions, and Python installation verifies pinned package hashes.
- **Secrets stay out of the repository.** Store optional API credentials in environment variables or GitHub Actions secrets. Local environment/key files are ignored, and collector diagnostics redact known credentials and common token parameters.

The website is a static export. It has no deployed Next.js application server, Server Actions, login system, or database. Local development tools and CI dependencies still require security updates.

## Keeping dependencies current

```bash
npm audit --package-lock-only
pip-audit -r requirements.txt
python -m unittest discover -s tests -v
```

After changing direct Python versions in `requirements.in`, regenerate and review the lockfile:

```bash
uv pip compile --python-version 3.10 --generate-hashes --no-header requirements.in -o requirements.txt
python -m pip install --require-hashes -r requirements.txt
```

Review dependency and workflow changes before merging. Repository branch protection, private vulnerability reporting, and GitHub secret-scanning settings are controlled by the repository owner; source files alone do not enable those protections.
