# Security Policy

## Threat model

This is a personal data-analysis tool. The main concerns are:
1. **Leaking API keys** (Anthropic) or **email credentials** (Gmail App Password).
2. **Sending the digest to the wrong address.**
3. **Pulling untrusted code/data** from third-party APIs.

## Hardening already in place

- `.env` is git-ignored; only `.env.example` is tracked.
- `gitleaks` runs as a pre-commit hook AND in CI.
- `pip-audit` runs in CI to flag known vulnerabilities.
- `pre-commit` hooks include `detect-private-key` and large-file blocks.
- Gmail credentials and recipient address are env-only; no defaults in code.
- `DRY_RUN=true` by default in `.env.example` so first runs never send.
- All third-party data is read-only (yfinance has no auth).

## Recommendations for users

- Use a **Gmail App Password** dedicated to this project. Revoke it instantly at https://myaccount.google.com/apppasswords if it ever leaks.
- Never use your real Gmail password — App Passwords are scoped and revokable; the main password is not.
- Set a **monthly spending cap** on your Anthropic API account (console.anthropic.com → Plans & Billing → Usage Limits).
- Rotate the App Password every 90 days.
- Run on a machine with disk encryption; the `.env` file holds secrets.
- Never commit the `reports/` directory if it contains personally identifying info.

## What an attacker could do with leaked credentials

- **Anthropic key**: bill API usage to your account up to your spending cap. Mitigation: set the cap low.
- **Gmail App Password**: send emails AS your Gmail account. They cannot access your inbox via SMTP, but they could spoof emails from you. Revoke the App Password immediately if leaked — it does not affect your main Google login.

## Reporting

If you find a vulnerability, open a private issue or email the repo owner.
Do not include real credentials or tokens in any report.
