# Security Policy

## Threat model

This is a personal data-analysis tool. The main concerns are:
1. **Leaking API keys** (Anthropic, Twilio).
2. **Sending the digest to the wrong WhatsApp number.**
3. **Pulling untrusted code/data** from third-party APIs.

## Hardening already in place

- `.env` is git-ignored; only `.env.example` is tracked.
- `gitleaks` runs as a pre-commit hook AND in CI.
- `pip-audit` runs in CI to flag known vulnerabilities.
- `pre-commit` hooks include `detect-private-key` and large-file blocks.
- Twilio credentials and recipient number are env-only; no defaults in code.
- `DRY_RUN=true` by default in `.env.example` so first runs never send.
- All third-party data is read-only (yfinance has no auth).

## Recommendations for users

- Use a **read-only / minimum-scope** Anthropic API key dedicated to this project.
- Use Twilio's **WhatsApp sandbox** for testing, not your production sender.
- Rotate Twilio + Anthropic keys every 90 days.
- Run on a machine with disk encryption; the `.env` file holds secrets.
- Never commit the `reports/` directory if it contains personally identifying info.

## Reporting

If you find a vulnerability, open a private issue or email the repo owner.
Do not include real credentials or tokens in any report.
