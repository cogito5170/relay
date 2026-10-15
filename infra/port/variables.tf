variable "port_base_url" {
  description = "Port API endpoint. app.port.io uses api.port.io; app.us.port.io uses api.us.port.io."
  type        = string
  default     = "https://api.port.io"
}

variable "port_github_installation_id" {
  description = "Port GitHub Ocean integration identifier (the suffix in 'Github-ocean - <id>')."
  type        = string
  default     = "github-ops"
}

variable "github_org" {
  description = "GitHub organization or user that owns the relay repository."
  type        = string
  default     = "cogito5170"
}

variable "github_repo" {
  description = "Repository containing the Cloud Relay workflow."
  type        = string
  default     = "effiency"
}

variable "github_workflow" {
  description = "Workflow file registered in the Port GitHub integration."
  type        = string
  default     = "cloud-relay.yml"
}

