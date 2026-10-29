mock_provider "google" {}
mock_provider "random" {}

variables {
  owner_email = "owner@example.invalid"
}

run "workload_plan" {
  command = plan

  assert {
    condition = (
      google_artifact_registry_repository.forseti.format == "DOCKER" &&
      !google_artifact_registry_repository.forseti.cleanup_policy_dry_run &&
      length(google_artifact_registry_repository.forseti.cleanup_policies) == 2
    )
    error_message = "The Docker registry must enforce both cleanup policies."
  }
  assert {
    condition = (
      length(google_secret_manager_secret_iam_member.grants) == 0 &&
      length(google_cloud_run_v2_service_iam_member.grants) == 0 &&
      length(google_cloud_run_v2_job_iam_member.grants) == 0 &&
      length(google_storage_bucket_iam_member.grants) == 0
    )
    error_message = "Do not grant access to resources that later milestones have not created yet."
  }
}
