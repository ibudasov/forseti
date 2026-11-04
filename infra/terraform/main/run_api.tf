locals {
  api_service_account = "forseti-api@${var.project_id}.iam.gserviceaccount.com"
  api_environment = merge({
    APP_ENV                   = "cloud"
    PIPELINE_MODE             = "agentic"
    SYNTHESIZER_CONTRACT      = "typed"
    ALLOW_PIPELINE_OVERRIDE   = "false"
    DEBUG_LLM_IO              = "false"
    RAG_FAIL_LOUD             = "true"
    GOOGLE_GENAI_USE_VERTEXAI = "TRUE"
    GOOGLE_CLOUD_PROJECT      = var.project_id
    VERTEX_AI_PROJECT         = var.project_id
    GOOGLE_CLOUD_LOCATION     = var.region
    VERTEX_AI_LOCATION        = var.region
    GEMINI_MODEL              = var.gemini_model
    EMBEDDING_MODEL           = var.embedding_model
    EDGAR_USER_AGENT          = var.edgar_user_agent
    DB_POOL_SIZE              = "3"
    DB_MAX_OVERFLOW           = "2"
  }, var.agent_modes)
}

resource "google_cloud_run_v2_service" "api" {
  name                = "forseti-api"
  location            = var.region
  deletion_protection = false

  template {
    service_account                  = local.api_service_account
    timeout                          = "300s"
    max_instance_request_concurrency = 4

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image = var.api_bootstrap_image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = local.api_environment
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

      startup_probe {
        initial_delay_seconds = 10
        timeout_seconds       = 5
        period_seconds        = 10
        failure_threshold     = 3
        http_get {
          path = "/livez"
          port = 8080
        }
      }

      liveness_probe {
        initial_delay_seconds = 0
        timeout_seconds       = 5
        period_seconds        = 30
        failure_threshold     = 3
        http_get {
          path = "/livez"
          port = 8080
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

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }
}
