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
      length(google_secret_manager_secret_iam_member.grants) == 6 &&
      length(google_cloud_run_v2_service_iam_member.grants) == 3 &&
      length(google_cloud_run_v2_job_iam_member.grants) == 0 &&
      length(google_storage_bucket_iam_member.grants) == 0
    )
    error_message = "Secret accessors and the declared private API invokers must be the only resource grants."
  }
  assert {
    condition = toset([
      for grant in local.resource_grants :
      grant.member
      if grant.kind == "service" && grant.resource == "forseti-api" && grant.role == "run.invoker"
    ]) == toset(["owner", "forseti-deployer", "forseti-verifier"])
    error_message = "Only the owner, deployer and verifier may invoke the private API."
  }
  assert {
    condition = (
      google_cloud_run_v2_service.api.name == "forseti-api" &&
      google_cloud_run_v2_service.api.location == "europe-west4" &&
      google_cloud_run_v2_service.api.template[0].max_instance_request_concurrency == 4 &&
      google_cloud_run_v2_service.api.template[0].timeout == "300s" &&
      google_cloud_run_v2_service.api.template[0].service_account == "forseti-api@forseti-505116.iam.gserviceaccount.com" &&
      google_cloud_run_v2_service.api.template[0].scaling[0].min_instance_count == 0 &&
      google_cloud_run_v2_service.api.template[0].scaling[0].max_instance_count == 2 &&
      google_cloud_run_v2_service.api.template[0].containers[0].resources[0].limits["cpu"] == "1" &&
      google_cloud_run_v2_service.api.template[0].containers[0].resources[0].limits["memory"] == "1Gi" &&
      google_cloud_run_v2_service.api.template[0].containers[0].resources[0].cpu_idle &&
      google_cloud_run_v2_service.api.template[0].containers[0].resources[0].startup_cpu_boost
    )
    error_message = "The API must use its dedicated identity, request-based 1-vCPU sizing and bounded scaling."
  }
  assert {
    condition = (
      contains([for value in google_cloud_run_v2_service.api.template[0].containers[0].env : value.name == "DATABASE_URL" && value.value_source[0].secret_key_ref[0].secret == "forseti-database-url"], true) &&
      contains([for value in google_cloud_run_v2_service.api.template[0].containers[0].env : value.name == "ALPHA_VANTAGE_API_KEY" && value.value_source[0].secret_key_ref[0].secret == "forseti-alpha-vantage-api-key"], true) &&
      contains([for value in google_cloud_run_v2_service.api.template[0].containers[0].env : value.name == "FUNDAMENTAL_AGENT_MODE" && value.value == "enforced"], true) &&
      contains([for value in google_cloud_run_v2_service.api.template[0].containers[0].env : value.name == "TECHNICAL_AGENT_MODE" && value.value == "enforced"], true) &&
      contains([for value in google_cloud_run_v2_service.api.template[0].containers[0].env : value.name == "CRITIC_MODE" && value.value == "enforced"], true) &&
      contains([for value in google_cloud_run_v2_service.api.template[0].containers[0].env : value.name == "SYNTHESIZER_CONTRACT" && value.value == "typed"], true) &&
      contains([for value in google_cloud_run_v2_service.api.template[0].containers[0].env : value.name == "ALLOW_PIPELINE_OVERRIDE" && value.value == "false"], true) &&
      contains([for value in google_cloud_run_v2_service.api.template[0].containers[0].env : value.name == "DEBUG_LLM_IO" && value.value == "false"], true)
    )
    error_message = "The API must use secret references and secure, enforced agent runtime settings."
  }
  assert {
    condition = (
      google_cloud_run_v2_service.api.template[0].containers[0].startup_probe[0].http_get[0].path == "/livez" &&
      google_cloud_run_v2_service.api.template[0].containers[0].liveness_probe[0].http_get[0].path == "/livez" &&
      length(google_cloud_run_v2_service.api.template[0].volumes[0].cloud_sql_instance[0].instances) == 1 &&
      google_cloud_run_v2_service.api.template[0].containers[0].volume_mounts[0].mount_path == "/cloudsql"
    )
    error_message = "The API probes must use /livez and Cloud SQL must be mounted at /cloudsql."
  }
  assert {
    condition = (
      google_cloud_run_v2_job.housekeeping.name == "forseti-housekeeping" &&
      google_cloud_run_v2_job.housekeeping.template[0].task_count == 1 &&
      google_cloud_run_v2_job.housekeeping.template[0].template[0].max_retries == 0 &&
      google_cloud_run_v2_job.housekeeping.template[0].template[0].timeout == "900s" &&
      startswith(google_cloud_run_v2_job.housekeeping.template[0].template[0].service_account, "forseti-housekeeping@")
    )
    error_message = "The housekeeping job must run as forseti-housekeeping with one task, no retries and a 900 s timeout."
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
