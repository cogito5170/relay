output "ai_job_blueprint" {
  description = "Port blueprint identifier created by this stack."
  value       = port_blueprint.ai_job.identifier
}

output "run_ai_relay_action" {
  description = "Port self-service action identifier created by this stack."
  value       = port_action.run_ai_relay.identifier
}

