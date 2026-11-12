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
    # The explorables deploy job skips itself until this is set.
    EXPLORABLES_SERVICE = google_cloud_run_v2_service.explorables.name
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
