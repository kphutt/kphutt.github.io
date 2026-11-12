# What the deploy workflow needs, as GitHub Actions repository variables. None of these
# is a secret: they name things, and only the WIF condition decides who may use them.
# infra/README.md sets them with `gh variable set`.

output "github_variables" {
  description = "Repository variables for .github/workflows/hugo.yml and rollback.yml."
  value = {
    GCP_PROJECT       = var.project_id
    GCP_REGION        = var.region
    CLOUD_RUN_SERVICE = google_cloud_run_v2_service.site.name
    ARTIFACT_REPO     = google_artifact_registry_repository.site.repository_id
    WIF_PROVIDER      = google_iam_workload_identity_pool_provider.github.name
    DEPLOYER_SA       = google_service_account.deployer.email
  }
}

# The same for the explorables repository's workflows (explorables-repo.tf). WIF_PROVIDER is
# empty until explorables_github_repository_id is set, and that repository's deploy job skips
# itself while it is. `python infra/seed.py explorables-vars` sets them there.
output "explorables_github_variables" {
  description = "Repository variables for the explorables repository's ci.yml and rollback.yml."
  value = {
    GCP_PROJECT       = var.project_id
    GCP_REGION        = var.region
    CLOUD_RUN_SERVICE = google_cloud_run_v2_service.explorables.name
    ARTIFACT_REPO     = google_artifact_registry_repository.explorables.repository_id
    WIF_PROVIDER      = local.explorables_repo_enabled ? google_iam_workload_identity_pool_provider.github_explorables[0].name : ""
    DEPLOYER_SA       = google_service_account.explorables_deployer.email
  }
}

output "service_url" {
  description = "The service's own *.run.app address."
  value       = google_cloud_run_v2_service.site.uri
}

output "explorables_service_url" {
  description = "The explorables service's own *.run.app address."
  value       = google_cloud_run_v2_service.explorables.uri
}
