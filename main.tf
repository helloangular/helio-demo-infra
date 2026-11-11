# Rendered by Helio from helio/service.json under the cloud account's policy.
# Helio plans and applies this stack itself, with a signed approval bound to the
# plan; this file shows what was requested. Change helio/service.json, not this.
#
# Fixed by policy for a development-class account (not editable by the requester):
#   - private subnets, no public IP on tasks, internal load balancer
#   - permissions boundary on the task and execution roles
#   - image pulled only from this account's registry, by digest
#   - required tags and the reserved helio/ name prefix

module "service" {
  source = "helio/ecs-fargate-service"

  name           = "demo-api-sandbox-us-east-1"
  region         = "us-east-1"
  image          = "helio-test-images@sha256:91fc9c3924b93f76adc9cd36a0bb93e53885ff8e97df10233caa45bb432789a5"
  container_port = 80

  assign_public_ip = false
  launch_type      = "FARGATE"
  autoscale        = false
  enable_db        = false
}
