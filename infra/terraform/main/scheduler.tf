locals {
  scheduler_service_account = "forseti-scheduler@${var.project_id}.iam.gserviceaccount.com"
}

resource "google_cloud_scheduler_job" "daily_refresh" {
  name             = "forseti-daily-refresh"
  region           = var.region
  description      = "Trigger Forseti daily market-data and RAG refresh after US market close"
  schedule         = "30 18 * * 1-5"
  time_zone        = "America/New_York"
  attempt_deadline = "320s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.daily_refresh.name}:run"

    oauth_token {
      service_account_email = local.scheduler_service_account
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}
