# On-demand trust check: the live linear/agentic pre-flight against Cloud SQL with
# the production agent modes. It refuses to run unless CONFIRM_COST=yes is passed
# at execution time (`/verify full` does this), so it can never spend model
# budget by accident. Reports go to the artifacts bucket through a GCS volume.
# forseti-jobs has storage.objectUser on that bucket; the verifier may pass
# CONFIRM_COST only because it holds run.jobsExecutorWithOverrides on this job.
locals {
  jobs_service_account = "forseti-jobs@${var.project_id}.iam.gserviceaccount.com"
  trust_environment = merge(local.api_environment, {
    OTEL_SERVICE_NAME = "forseti-trust-check"
    TRUST_REPORT_DIR  = "/gcs/reports/trust"
  })
}

resource "google_cloud_run_v2_job" "trust_check" {
  name                = "forseti-trust-check"
  location            = var.region
  deletion_protection = false

  template {
    task_count = 1

    template {
      service_account = local.jobs_service_account
      max_retries     = 0
      timeout         = "1800s"

      containers {
        image   = var.housekeeping_bootstrap_image
        command = ["python", "-m", "scripts.cloud_trust_check"]

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }

        dynamic "env" {
          for_each = local.trust_environment
          content {
            name  = env.key
            value = env.value
          }
        }

        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_url.secret_id
              version = "latest"
            }
          }
        }
        env {
          name = "ALPHA_VANTAGE_API_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.alpha_vantage_api_key.secret_id
              version = "latest"
            }
          }
        }

        # Match the API-returned name order for both mounts and volumes.
        volume_mounts {
          name       = "artifacts"
          mount_path = "/gcs"
        }
        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }

      volumes {
        name = "artifacts"
        gcs {
          bucket    = "${var.project_id}-artifacts"
          read_only = false
        }
      }
      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.forseti_pg.connection_name]
        }
      }
    }
  }

  # CD owns the image (same image as the API); Terraform only creates the job.
  lifecycle {
    ignore_changes = [
      client,
      client_version,
      template[0].template[0].containers[0].image,
    ]
  }
}
