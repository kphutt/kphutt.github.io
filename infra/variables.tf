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

# The test name for the move (docs/design/cutover-runbook.md, step 4). Off by default, so the
# base infrastructure can be applied without a Cloudflare token.
variable "test_domain_enabled" {
  description = "Map test_hostname to the service and create its DNS record at Cloudflare. Needs CLOUDFLARE_API_TOKEN."
  type        = bool
  default     = false
}

# The apex and www serve the live site. They must never be managed from here, so the name is
# checked three times: this validation, a precondition on every DNS record (test-domain.tf)
# and scripts/check_infra.py.
variable "test_hostname" {
  description = "The test name. A subdomain of kphutt.com; never kphutt.com itself or www."
  type        = string
  default     = "next.kphutt.com"

  validation {
    condition = (
      can(regex("^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?[.])+kphutt[.]com$", var.test_hostname))
      && !contains(["kphutt.com", "www.kphutt.com"], var.test_hostname)
    )
    error_message = "test_hostname must be a lowercase subdomain of kphutt.com, and not kphutt.com or www.kphutt.com."
  }
}

variable "cloudflare_zone_id" {
  description = "The kphutt.com zone id, from the zone's Overview page in Cloudflare. Not a secret. Needed only when test_domain_enabled is true."
  type        = string
  default     = ""

  validation {
    condition     = var.cloudflare_zone_id == "" || can(regex("^[0-9a-f]{32}$", var.cloudflare_zone_id))
    error_message = "cloudflare_zone_id must be the 32-character hex zone id."
  }
}

variable "verify_test_hostname" {
  description = "Prove ownership of test_hostname to Google from code (a CNAME under it). Set false if ownership of kphutt.com is already verified for the account running tofu."
  type        = bool
  default     = true
}
