# Private job: no URL, no ingress, no scheduler. Only forseti-deployer (deploy
# pipeline) and the owner can run it. It is the only workload that reads the
# owner database URL.
locals {
  housekeeping_service_account = "forseti-housekeeping@${var.project_id}.iam.gserviceaccount.com"
}

resource "google_cloud_run_v2_job" "housekeeping" {
  name                = "forseti-housekeeping"
  location            = var.region
  deletion_protection = false

  template {
    task_count = 1

    template {
      service_account = local.housekeeping_service_account
      max_retries     = 0
      timeout         = "900s"

      containers {
        image   = var.housekeeping_bootstrap_image
        command = ["python", "-m", "app.jobs.housekeeping"]

        env {
          name  = "APP_ENV"
          value = "cloud"
        }
        env {
          name  = "APP_DB_ROLE"
          value = "forseti_app"
        }
        env {
          name = "HOUSEKEEPING_DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.owner_database_url.secret_id
              version = "latest"
            }
          }
        }
        env {
          name = "APP_DB_PASSWORD"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.app_db_password.secret_id
              version = "latest"
            }
          }
        }

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
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
