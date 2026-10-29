data "github_user" "owner" {
  username = split("/", var.github_repository)[0]
}

resource "github_repository_environment" "infra" {
  repository  = local.repository_name
  environment = "gcp-infra"
  reviewers {
    users = [data.github_user.owner.id]
  }
  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }
}

resource "github_repository_environment" "dev" {
  repository  = local.repository_name
  environment = "gcp-dev"
  deployment_branch_policy {
    protected_branches     = false
    custom_branch_policies = true
  }
}

resource "github_repository_environment_deployment_policy" "infra" {
  repository     = local.repository_name
  environment    = github_repository_environment.infra.environment
  branch_pattern = "main"
}

resource "github_repository_environment_deployment_policy" "dev" {
  repository     = local.repository_name
  environment    = github_repository_environment.dev.environment
  branch_pattern = "main"
}

locals {
  github_variables = {
    GCP_PROJECT_ID    = var.project_id
    GCP_REGION        = var.region
    GCP_WIF_PROVIDER  = google_iam_workload_identity_pool_provider.github.name
    GCP_TF_PLAN_SA    = local.account_emails["forseti-tf-plan"]
    GCP_TF_APPLY_SA   = local.account_emails["forseti-tf-apply"]
    GCP_DEPLOYER_SA   = local.account_emails["forseti-deployer"]
    GCP_VERIFIER_SA   = local.account_emails["forseti-verifier"]
    GCP_ARTIFACT_REPO = "${var.region}-docker.pkg.dev/${var.project_id}/forseti"
    TF_STATE_BUCKET   = local.state_bucket
  }
}

resource "github_actions_variable" "configuration" {
  for_each      = local.github_variables
  repository    = local.repository_name
  variable_name = each.key
  value         = each.value
}

resource "github_actions_secret" "owner_email" {
  repository  = local.repository_name
  secret_name = "OWNER_EMAIL"
  value       = var.owner_email
}
