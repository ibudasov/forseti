mock_provider "google" {}
mock_provider "github" {}
mock_provider "random" {}

override_data {
  target = data.github_user.owner
  values = {
    id = "3657892"
  }
}

variables {
  owner_email        = "owner@example.invalid"
  billing_account_id = "000000-000000-000000"
}

run "foundation_plan" {
  command = plan

  assert {
    condition = (
      google_storage_bucket.state.uniform_bucket_level_access &&
      google_storage_bucket.state.public_access_prevention == "enforced" &&
      google_storage_bucket.state.versioning[0].enabled &&
      !google_storage_bucket.state.force_destroy
    )
    error_message = "State storage must remain private, versioned and protected."
  }
  assert {
    condition = (
      length(google_service_account.accounts) == 8 &&
      length(google_service_account_iam_member.federation) == 4 &&
      !contains(keys(google_service_account_iam_member.federation), "forseti-api")
    )
    error_message = "Only CI identities, not runtime identities, may be federated."
  }
  assert {
    condition = alltrue([
      for grant in google_project_iam_member.storage_except_state :
      strcontains(grant.condition[0].expression, "!resource.name.startsWith('projects/_/buckets/forseti-505116-tfstate')")
    ])
    error_message = "Workload storage administration must exclude Terraform state."
  }
  assert {
    condition = (
      github_repository_environment.infra.reviewers[0].users != null &&
      github_repository_environment_deployment_policy.infra.branch_pattern == "main" &&
      github_repository_environment_deployment_policy.dev.branch_pattern == "main"
    )
    error_message = "Infrastructure requires owner approval and both environments must restrict deployments to main."
  }
}
