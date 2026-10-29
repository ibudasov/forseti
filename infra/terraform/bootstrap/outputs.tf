output "wif_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "service_account_emails" {
  value = local.account_emails
}

output "artifact_registry_repo" {
  value = local.github_variables.GCP_ARTIFACT_REPO
}

output "state_bucket" {
  value = google_storage_bucket.state.name
}
