terraform {
  required_version = ">= 1.7.0"

  required_providers {
    port = {
      source  = "port-labs/port-labs"
      version = "~> 2.30"
    }
  }
}

provider "port" {
  base_url = var.port_base_url
  # Read from PORT_CLIENT_ID and PORT_CLIENT_SECRET. Never commit credentials.
}

