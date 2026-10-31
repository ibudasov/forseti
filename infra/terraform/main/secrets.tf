ephemeral "random_password" "owner" {
  length  = 32
  special = false
}

ephemeral "random_password" "app" {
  length  = 32
  special = false
}

locals {
  cloud_sql_socket      = "/cloudsql/${google_sql_database_instance.forseti_pg.connection_name}"
  database_url_template = "postgresql://%s:%s@/${google_sql_database.forseti.name}?host=${local.cloud_sql_socket}"
}

resource "google_secret_manager_secret" "database_url" {
  secret_id = "forseti-database-url"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "owner_database_url" {
  secret_id = "forseti-owner-database-url"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret" "app_db_password" {
  secret_id = "forseti-app-db-password"
  replication {
    auto {}
  }
}

# The owner adds the value: see docs/database.md.
resource "google_secret_manager_secret" "alpha_vantage_api_key" {
  secret_id = "forseti-alpha-vantage-api-key"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "database_url" {
  secret                 = google_secret_manager_secret.database_url.id
  secret_data_wo         = format(local.database_url_template, "forseti_app", urlencode(ephemeral.random_password.app.result))
  secret_data_wo_version = var.credentials_version
}

resource "google_secret_manager_secret_version" "owner_database_url" {
  secret                 = google_secret_manager_secret.owner_database_url.id
  secret_data_wo         = format(local.database_url_template, "forseti_owner", urlencode(ephemeral.random_password.owner.result))
  secret_data_wo_version = var.credentials_version
}

resource "google_secret_manager_secret_version" "app_db_password" {
  secret                 = google_secret_manager_secret.app_db_password.id
  secret_data_wo         = ephemeral.random_password.app.result
  secret_data_wo_version = var.credentials_version
}
