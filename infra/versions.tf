# OpenTofu and provider pins.
#
# State lives in a versioned GCS bucket. The backend is only partly configured here: the
# bucket name is passed at `tofu init` time (infra/README.md), so `tofu init
# -backend=false` works for offline checks with no bucket and no GCP access at all.

terraform {
  required_version = ">= 1.13.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.9.0"
    }
    # google_billing_budget is only in the beta provider.
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 8.10.0"
    }
    # Zips the cost cutoff function's source (cost-cutoff.tf). Local only; no account.
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7.0"
    }
    # DNS: the zone's production records (dns.tf, imported) and the test name's (test-domain.tf).
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.28.0"
    }
  }

  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region

  # Some APIs (the billing budget among them) refuse user credentials that carry no quota
  # project. Charging quota to this project means no per-machine gcloud setup is needed.
  user_project_override = true
  billing_project       = var.project_id
}

provider "google-beta" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
}

# The API token comes from the CLOUDFLARE_API_TOKEN environment variable, never from a file
# in the repository or from a .tfvars file. infra/README.md lists the permissions it needs.
# dns.tf's production records are always planned, so every plan needs the token.
provider "cloudflare" {}
