resource "google_sql_database_instance" "forseti_pg" {
  name                = "forseti-pg"
  region              = var.region
  database_version    = "POSTGRES_15"
  deletion_protection = true

  settings {
    edition                     = "ENTERPRISE"
    tier                        = "db-f1-micro"
    availability_type           = "ZONAL"
    disk_type                   = "PD_SSD"
    disk_size                   = 10
    disk_autoresize             = true
    disk_autoresize_limit       = 50
    deletion_protection_enabled = true

    backup_configuration {
      enabled                        = true
      start_time                     = "02:00"
      point_in_time_recovery_enabled = false
      backup_retention_settings {
        retained_backups = 7
        retention_unit   = "COUNT"
      }
    }

    maintenance_window {
      day  = 7
      hour = 3
    }

    # Public IP without authorized networks: reachable only through Cloud SQL
    # connectors and the Auth Proxy, which authorize through IAM.
    ip_configuration {
      ipv4_enabled = true
      ssl_mode     = "ENCRYPTED_ONLY"
    }
  }
}

resource "google_sql_database" "forseti" {
  name     = "forseti"
  instance = google_sql_database_instance.forseti_pg.name
}

# Users created through the Cloud SQL API join cloudsqlsuperuser, which
# CREATE EXTENSION vector needs. This is the only API-created user; the
# forseti_app runtime role is created by the housekeeping job (GCP-3b).
resource "google_sql_user" "forseti_owner" {
  name                = "forseti_owner"
  instance            = google_sql_database_instance.forseti_pg.name
  type                = "BUILT_IN"
  password_wo         = ephemeral.random_password.owner.result
  password_wo_version = var.credentials_version
}
