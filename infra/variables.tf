# Inputs, filled in infra/terraform.tfvars (git-ignored); the shape is
# in terraform.tfvars.example. Nothing account-specific has a default.

variable "project_id" {
  description = "The GCP project that hosts only this site."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a valid GCP project id."
  }
}

variable "region" {
  description = "Cloud Run region. us-west1 and us-central1 both support domain mapping."
  type        = string

  validation {
    condition     = contains(["us-west1", "us-central1"], var.region)
    error_message = "region must be us-west1 or us-central1, where Cloud Run domain mapping is available."
  }
}

variable "github_repository" {
  description = "owner/name of the only repository allowed to deploy, e.g. kphutt/kphutt.github.io."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must be owner/name."
  }
}

variable "github_repository_id" {
  description = "The repository's numeric GitHub id (gh api repos/OWNER/NAME --jq .id). Names can be reused after a rename or deletion; ids cannot."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_id))
    error_message = "github_repository_id must be the numeric repository id."
  }
}

variable "billing_account" {
  description = "Billing account id (XXXXXX-XXXXXX-XXXXXX) the budget alert watches."
  type        = string
}

variable "alert_email" {
  description = "Where budget alerts are sent."
  type        = string
}

variable "monthly_budget_usd" {
  description = "Budget alert threshold. The site should cost nothing; any spend is a signal."
  type        = number
  default     = 1
}

variable "service_name" {
  description = "Cloud Run service name. It appears in the default *.run.app URL."
  type        = string
  default     = "site"
}

variable "max_instances" {
  description = "Upper bound on Cloud Run instances. Caps the cost of a traffic flood."
  type        = number
  default     = 3
}

variable "request_log_retention_days" {
  description = "How long the dedicated bucket keeps Cloud Run request logs. 30 days is the free retention."
  type        = number
  default     = 30
}
