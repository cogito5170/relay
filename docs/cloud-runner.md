# Cloud runner — phone-first Phase 1

This path works while every developer laptop is off. GitHub hosts the runner;
the repository stores the workflow; each run leaves a downloadable audit bundle.

## Run it from a phone

1. Open the repository in the GitHub mobile app or browser.
2. Open **Actions → Cloud Relay → Run workflow**.
3. Enter a lowercase `task_id`, choose an agent, and enter the instruction.
4. Open the completed run and download the `relay-...` artifact.

The artifact contains `request.json`, `run.json`, and `result.md`. Phase 1 is a
real remote execution test but deliberately does not call an AI model. It needs
no API key and cannot reuse Claude Code or Agy login sessions from a Mac.

## Security boundary

- Workflow token has `contents: read` only.
- Inputs are length/format checked and are never evaluated as shell commands.
- The job runs as a non-root user in a read-only container with Linux
  capabilities removed.
- No long-lived cloud credential is present.

## Phase 2 acceptance gate

Add one provider adapter only after choosing an official API and configuring one
repository/environment secret. The adapter must keep the same request validation,
write provider/model/request-id metadata to `run.json`, apply a timeout and cost
limit, and never place secret values in artifacts or logs.

For AWS deployment later, exchange GitHub's OIDC identity for a short-lived role;
do not store an AWS access key in repository secrets.
