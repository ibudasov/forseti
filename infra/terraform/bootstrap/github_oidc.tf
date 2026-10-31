locals {
  federated_accounts = { for name, account in local.accounts : name => account if account.workflow != null }
  workflow_conditions = {
    for name, account in local.federated_accounts : name => join(" && ", [
      "assertion.workflow_ref == '${var.github_repository}/.github/workflows/${account.workflow.file}@refs/heads/main'",
      "assertion.ref == 'refs/heads/main'",
      "assertion.sub == 'repo:${var.github_repository}:${account.workflow.environment == null ? "ref:refs/heads/main" : "environment:${account.workflow.environment}"}'",
      "assertion.event_name in ${jsonencode(account.workflow.events)}",
    ])
  }
  # A single mapped attribute combines workflow, branch, environment and event.
  pipeline_expression = "${join(" : ", [for name, condition in local.workflow_conditions : "(${condition}) ? '${name}'"])} : 'denied'"
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  attribute_condition                = "assertion.repository_id == '${var.github_repository_id}' && assertion.repository_owner_id == '${var.github_owner_id}'"
  attribute_mapping = {
    "google.subject"        = "assertion.sub"
    "attribute.workflow"    = "assertion.workflow_ref"
    "attribute.ref"         = "assertion.ref"
    "attribute.environment" = "'environment' in assertion ? assertion.environment : ''"
    "attribute.pipeline"    = local.pipeline_expression
  }
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "federation" {
  for_each           = local.federated_accounts
  service_account_id = google_service_account.accounts[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.pipeline/${each.key}"
}
