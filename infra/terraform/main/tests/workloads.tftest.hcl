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
      length(google_cloud_run_v2_job_iam_member.grants) == 1 &&
      length(google_storage_bucket_iam_member.grants) == 1
    )
    error_message = "Secret accessors, private API invokers, the trust-job executor and the artifacts writer must be the only resource grants."
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
    condition = toset([
      for grant in local.resource_grants :
      grant.member
      if grant.kind == "job" && grant.resource == "forseti-trust-check" && grant.role == "run.jobsExecutorWithOverrides"
    ]) == toset(["forseti-verifier"])
    error_message = "Only the verifier may execute the trust-check job, and only with overrides so CONFIRM_COST stays out of the job."
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
      google_cloud_run_v2_job.trust_check.name == "forseti-trust-check" &&
      google_cloud_run_v2_job.trust_check.template[0].template[0].max_retries == 0 &&
      startswith(google_cloud_run_v2_job.trust_check.template[0].template[0].service_account, "forseti-jobs@") &&
      !contains([for value in google_cloud_run_v2_job.trust_check.template[0].template[0].containers[0].env : value.name], "CONFIRM_COST") &&
      contains([for value in google_cloud_run_v2_job.trust_check.template[0].template[0].containers[0].env : value.name == "CRITIC_MODE" && value.value == "enforced"], true)
    )
    error_message = "The trust-check job must run as forseti-jobs with enforced agents and must not pre-approve its own cost."
  }
  assert {
    condition = (
      tolist([for volume in google_cloud_run_v2_job.trust_check.template[0].template[0].volumes : volume.name]) ==
      sort([for volume in google_cloud_run_v2_job.trust_check.template[0].template[0].volumes : volume.name]) &&
      [for mount in google_cloud_run_v2_job.trust_check.template[0].template[0].containers[0].volume_mounts : mount.name] ==
      [for volume in google_cloud_run_v2_job.trust_check.template[0].template[0].volumes : volume.name] &&
      { for mount in google_cloud_run_v2_job.trust_check.template[0].template[0].containers[0].volume_mounts : mount.name => mount.mount_path } ==
      { artifacts = "/gcs", cloudsql = "/cloudsql" } &&
      length(google_cloud_run_v2_job.trust_check.template[0].template[0].volumes[0].gcs) == 1 &&
      google_cloud_run_v2_job.trust_check.template[0].template[0].volumes[0].gcs[0].bucket == google_storage_bucket.artifacts.name &&
      length(google_cloud_run_v2_job.trust_check.template[0].template[0].volumes[1].cloud_sql_instance) == 1 &&
      google_storage_bucket.artifacts.name == "${var.project_id}-artifacts" &&
      google_storage_bucket.artifacts.uniform_bucket_level_access &&
      google_storage_bucket.artifacts.public_access_prevention == "enforced" &&
      !google_storage_bucket.artifacts.force_destroy &&
      google_storage_bucket.artifacts.soft_delete_policy[0].retention_duration_seconds == 604800 &&
      length(google_storage_bucket.artifacts.lifecycle_rule) == 1 &&
      one([for rule in google_storage_bucket.artifacts.lifecycle_rule : one(rule.condition).age]) == 180 &&
      one([for rule in google_storage_bucket.artifacts.lifecycle_rule : one(rule.action).type]) == "Delete" &&
      one(flatten([for rule in google_storage_bucket.artifacts.lifecycle_rule : one(rule.condition).matches_prefix])) == "reports/"
    )
    error_message = "Trust-check volumes must keep API-returned order and mount the private artifacts bucket."
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

run "daily_cost_threshold" {
  command = plan

  variables {
    daily_llm_cost_threshold_usd = 7.5
  }

  assert {
    condition = (
      google_logging_metric.analysis_cost_usd.metric_descriptor[0].unit == "{USD}" &&
      google_monitoring_alert_policy.daily_llm_cost.enabled &&
      length(google_monitoring_alert_policy.daily_llm_cost.notification_channels) == 1 &&
      trimspace(google_monitoring_alert_policy.daily_llm_cost.conditions[0].condition_monitoring_query_language[0].query) == trimspace(<<-EOT
        fetch cloud_run_revision
        | metric 'logging.googleapis.com/user/forseti/analysis_cost_usd'
        | group_by [], sliding(1d), [daily_cost_usd: sum(sum_from(value.analysis_cost_usd))]
        | every 1m
        | condition val() > 7.5
      EOT
      )
    )
    error_message = "The cost policy must render the API-validated rolling-total query with the configured threshold and notification channel."
  }
}
