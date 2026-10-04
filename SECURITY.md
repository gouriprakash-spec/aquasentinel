# SECURITY.md — Non-Negotiable Rules

These rules apply to every change in this repository, regardless of who or what is making the change.

## Secrets
- Never hardcode API keys, tokens, passwords, or secrets in any file — use `.env`.
- `.env` must be listed in `.gitignore` before the first commit.
- Never commit `.env`, never log secrets, never print them to the terminal.
- Never read `.env` unless explicitly authorized for that specific task.
- If a secret is ever detected in a diff, commit, or log — stop immediately and flag it. Do not proceed until it's resolved.
- This repo is public: no tokens, phone numbers, or personal contact details in any committed file.

## Scope of Execution
- Only run this project's tooling from inside the project directory — never from a home folder or drive root.
- Review diffs before accepting large or multi-file changes.

## Destructive Actions
- No deleting files, folders, or database tables without explicit confirmation.
- No force-pushing, hard resets, or history rewrites on shared branches without explicit confirmation.
- No installing, removing, or upgrading dependencies without explicit approval.

## Error Handling
- No silent error recovery — stop and surface every failure with the exact error message.
- Do not mask, swallow, retry-loop, or reroute around errors without surfacing them first.

## Third-Party Data
- Treat all external/user-supplied input as untrusted — validate at the boundary.
- Never paste sensitive or proprietary data into third-party tools (pastebins, external APIs) without confirming it's safe to share.
