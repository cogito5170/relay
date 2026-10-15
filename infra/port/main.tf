resource "port_blueprint" "ai_job" {
  identifier  = "aiJob"
  title       = "AI Job"
  icon        = "AI"
  description = "An auditable request handled by the AI Workload Control Plane."

  properties = {
    string_props = {
      agent = {
        title = "Agent"
        enum  = ["claude", "codex", "agy"]
      }
      status = {
        title = "Status"
        enum  = ["queued", "running", "succeeded", "failed"]
      }
      mode = {
        title   = "Mode"
        default = "advisory"
        enum    = ["advisory"]
      }
      run_url = {
        title  = "Run URL"
        format = "url"
      }
    }
  }
}

resource "port_action" "run_ai_relay" {
  identifier               = "runAiRelay"
  title                    = "Run AI Relay"
  icon                     = "AI"
  description              = "Submit a bounded advisory job to the relay control plane."
  publish                  = true
  required_approval        = "false"
  allow_anyone_to_view_runs = false

  self_service_trigger = {
    operation            = "CREATE"
    blueprint_identifier = port_blueprint.ai_job.identifier
    order_properties     = ["task_id", "agent", "instruction"]

    user_properties = {
      string_props = {
        task_id = {
          title       = "Task ID"
          description = "1-64 lowercase letters, digits, dot, underscore, or hyphen."
          required    = true
          pattern     = "^[a-z0-9][a-z0-9._-]{0,63}$"
        }
        agent = {
          title    = "Agent"
          required = true
          default  = "claude"
          enum     = ["claude", "codex", "agy"]
        }
        instruction = {
          title       = "Instruction"
          description = "Do not enter credentials, authentication codes, or personal data."
          required    = true
          min_length  = 1
          max_length  = 4000
          format      = "markdown"
        }
      }
    }
  }

  github_method = {
    org                    = var.github_org
    repo                   = var.github_repo
    workflow               = var.github_workflow
    report_workflow_status = "true"
    workflow_inputs = jsonencode({
      task_id     = "{{ .inputs.task_id }}"
      agent       = "{{ .inputs.agent }}"
      instruction = "{{ .inputs.instruction }}"
    })
  }
}

