variable "project_id" {
  type    = string
  default = "forseti-505116"
}

variable "region" {
  type    = string
  default = "europe-west4"
}

variable "owner_email" {
  type      = string
  sensitive = true
}
