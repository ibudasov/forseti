# Scheduled daily refresh job: ingests structured market data and RAG documents
# after US market close, archives raw inputs to the artifacts bucket, and writes
# daily + latest ingest reports.
locals {
  daily_refresh_environment = merge(local.api_environment, {
    OTEL_SERVICE_NAME         = "forseti-daily-refresh"
    RAW_DOCUMENT_DIR          = "/mnt/artifacts/raw"
    REPORT_DIR                = "/mnt/artifacts/reports"
    INGEST_REPORT_PATH        = "/mnt/artifacts/reports/ingest/coverage-latest.json"
    EARNINGS_FALLBACK_ENABLED = "true"
  })
}

resource "google_cloud_run_v2_job" "daily_refresh" {
  name                = "forseti-daily-refresh"
  location            = var.region
  deletion_protection = false

  template {
    task_count = 1

    template {
      service_account       = local.jobs_service_account
      execution_environment = "EXECUTION_ENVIRONMENT_GEN2"
      max_retries           = 1
      timeout               = "3600s"

      containers {
        image   = var.housekeeping_bootstrap_image
        command = ["python", "-m", "app.jobs.daily_refresh"]

        resources {
          limits = {
            cpu    = "1"
            memory = "2Gi"
          }
        }

        dynamic "env" {
          for_each = local.daily_refresh_environment
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
          mount_path = "/mnt/artifacts"
        }
        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }

      volumes {
        name = "artifacts"
        gcs {
          bucket    = google_storage_bucket.artifacts.name
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
