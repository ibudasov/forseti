output "artifact_registry_repo" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.forseti.repository_id}"
}

output "cloud_sql_connection_name" {
  value = google_sql_database_instance.forseti_pg.connection_name
}
