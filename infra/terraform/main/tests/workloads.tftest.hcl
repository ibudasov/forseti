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
      length(google_secret_manager_secret_iam_member.grants) == 4 &&
      length(google_cloud_run_v2_service_iam_member.grants) == 0 &&
      length(google_cloud_run_v2_job_iam_member.grants) == 0 &&
      length(google_storage_bucket_iam_member.grants) == 0
    )
    error_message = "Only the declared per-secret accessors may exist; later milestones add the rest."
  }
  assert {
    condition = (
      google_sql_database_instance.forseti_pg.database_version == "POSTGRES_15" &&
      google_sql_database_instance.forseti_pg.deletion_protection &&
      google_sql_database_instance.forseti_pg.settings[0].tier == "db-f1-micro" &&
      google_sql_database_instance.forseti_pg.settings[0].availability_type == "ZONAL" &&
      google_sql_database_instance.forseti_pg.settings[0].deletion_protection_enabled &&
      !google_sql_database_instance.forseti_pg.settings[0].backup_configuration[0].point_in_time_recovery_enabled &&
      google_sql_database_instance.forseti_pg.settings[0].ip_configuration[0].ssl_mode == "ENCRYPTED_ONLY" &&
      length(google_sql_database_instance.forseti_pg.settings[0].ip_configuration[0].authorized_networks) == 0
    )
    error_message = "Cloud SQL must be the cheapest zonal tier, encrypted-only, with no authorized networks."
  }
}
