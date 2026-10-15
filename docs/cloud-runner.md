# Cloud runner — phone-first Phase 2

This path works while every developer laptop is off. GitHub hosts the runner;
the repository stores the workflow; each run leaves a downloadable audit bundle.

The current live adapter supports the `codex` route through the OpenAI Responses
API. This is an API-backed advisory role, not a remote reuse of a local Codex CLI
login. Claude and Agy stay unavailable in the Port form until their own official
remote adapters are implemented.

## One-time GitHub configuration

In the repository, open **Settings → Secrets and variables → Actions**:

1. Add repository secret `OPENAI_API_KEY`.
2. Add repository variable `OPENAI_MODEL` with a model available to that API key.
3. Never put either value in Terraform, Port inputs, source files, or artifacts.

## Run it from a phone

1. Open the repository in the GitHub mobile app or browser.
2. Open **Actions → Cloud Relay → Run workflow**.
3. Enter a lowercase `task_id`, choose `codex`, and enter the instruction.
4. Open the completed run and download the `relay-...` artifact.

The artifact contains `request.json`, `run.json`, and `result.md`. `run.json`
records the provider request ID, model and token usage, but never the API key.
The request is advisory and tool-free: it cannot inspect the repository or edit
files. Do not place credentials, authentication codes, or personal data in the
instruction because the instruction is retained in the audit bundle.

## Security boundary

- Workflow token has `contents: read` only.
- Inputs are length/format checked and are never evaluated as shell commands.
- The job runs as a non-root user in a read-only container with Linux
  capabilities removed.
- The OpenAI key is injected from GitHub Actions secrets only for the live step.
- The request has a 90-second provider timeout and a 1,200-output-token ceiling.

## Next acceptance gate

After a successful single-provider run, add durable Port status updates and then
consider separate official adapters for Claude and Agy. Do not map those names to
the OpenAI adapter, because that would make the audit record misleading.

For AWS deployment later, exchange GitHub's OIDC identity for a short-lived role;
do not store an AWS access key in repository secrets.
