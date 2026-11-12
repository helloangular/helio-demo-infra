# Rendered by Helio from helio/service.json under the cloud account's policy.
# Helio plans and applies this stack itself, with a signed approval bound to the
# plan; this file shows what was requested. Change helio/service.json, not this.
#
# Fixed by policy for a development-class account (not editable by the requester):
#   - private subnets, no public IP on tasks
#   - load balancer internal unless public_endpoint (development class only, HTTP)
#   - permissions boundary on the task and execution roles
#   - image pulled only from this account's registry, by digest
#   - required tags and the reserved helio/ name prefix

module "service" {
  source = "helio/ecs-fargate-service"

  name           = "demo-api-sandbox-us-east-1"
  region         = "us-east-1"
  image          = "helio/demo-api-sandbox-us-east-1@sha256:c2721fbf059bc1b3ce78832472b87ff2cb13ee67ecc12cd3e37d8628d8fc208d"
  container_port = 8080

  assign_public_ip = false
  public_endpoint  = true
  launch_type      = "FARGATE"
  autoscale        = true
  min_capacity     = 1
  max_capacity     = 3
  enable_db        = false
}
