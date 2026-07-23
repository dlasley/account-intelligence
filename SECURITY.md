# Security Policy

## Reporting a Vulnerability

Please report security vulnerabilities **privately**. Do **not** open a public
issue, pull request, or discussion for a suspected vulnerability.

Use GitHub's private vulnerability reporting: open the repository's **Security**
tab and click **"Report a vulnerability."** This creates a private advisory
visible only to you and the maintainer.

Where possible, include:

- A description of the issue and its potential impact.
- Steps to reproduce, or a proof-of-concept.
- The affected files, endpoints, or components.
- Any suggested remediation.

This is a source-available project maintained by a single maintainer, so
responses are best-effort. You can expect an initial acknowledgment within a
reasonable window; please allow time for triage and a fix before any public
disclosure (coordinated disclosure).

## Scope

This repository contains the source for the account-intelligence platform,
published under the Business Source License 1.1 (see [LICENSE](LICENSE)).

**In scope** — vulnerabilities in the code in this repository, for example:
authentication and authorization logic, webhook signature verification,
credential handling, input parsing and normalization, and SQL / row-level
security policies.

**Separate** — the hosted production service and its infrastructure are operated
independently of this source tree. Infrastructure, DNS, and third-party service
configuration are not represented here. Findings that concern a live deployment
should still be reported privately via the channel above.

## Handling of Secrets

This repository is designed to contain **no secrets**. Configuration is supplied
at runtime through environment variables (see [.env.example](.env.example)); the
committed `.env.example` holds placeholders only. If you believe a credential has
been committed, report it privately via the channel above rather than opening a
public issue.
