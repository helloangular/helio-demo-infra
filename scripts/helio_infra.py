#!/usr/bin/env python3
"""Calls Helio (Apex) to plan, apply and verify the stack described in helio/service.json.

Helio runs Terraform; this script only asks and waits. It holds no cloud
credentials. It signs in to Helio with accounts kept in files on the runner
(HELIO_CI_CREDENTIALS, HELIO_APPROVER_CREDENTIALS), never in the repository.
Standard library only.
"""
import datetime, json, os, pathlib, sys, time, urllib.error, urllib.request

URL = os.environ.get('HELIO_URL', '').rstrip('/')
SHA = os.environ.get('GITHUB_SHA', 'local')
RUN = os.environ.get('GITHUB_RUN_ID', 'local') + '-' + os.environ.get('GITHUB_RUN_ATTEMPT', '1')
OUT = pathlib.Path(os.environ.get('GITHUB_OUTPUT', '/dev/null'))


def fail(message):
    print('::error::' + message)
    sys.exit(1)


def call(method, path, token=None, body=None, headers=None):
    request = urllib.request.Request(URL + '/api/v1' + path, method=method,
                                     data=None if body is None else json.dumps(body).encode())
    request.add_header('Content-Type', 'application/json')
    if token:
        request.add_header('Authorization', 'Bearer ' + token)
    for k, v in (headers or {}).items():
        request.add_header(k, v)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b'{}')
        except ValueError:
            return e.code, {}


def credentials(variable):
    path = os.environ.get(variable)
    if not path or not pathlib.Path(path).is_file():
        fail(variable + ' must name a credentials file on the runner')
    return json.loads(pathlib.Path(path).read_text())


def login(variable):
    c = credentials(variable)
    status, body = call('POST', '/auth/login', body={'email': c['email'], 'password': c['password']})
    if status != 200 or 'token' not in body:
        fail('Helio sign-in failed for ' + variable + ' (HTTP ' + str(status) + ')')
    return body['token'], c


def output(key, value):
    with OUT.open('a') as f:
        f.write(key + '=' + str(value) + '\n')


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def deploy_status(token, deploy_id):
    status, body = call('GET', '/deploy/' + deploy_id + '/status', token)
    if status != 200:
        fail('Helio status unavailable (HTTP ' + str(status) + ')')
    return body


def wait(token, deploy_id, done, what, minutes):
    last = None
    for _ in range(minutes * 6):
        body = deploy_status(token, deploy_id)
        provision = body.get('provision') or {}
        state = (body.get('status'), provision.get('status'))
        if state != last:
            print(what + ': deploy=' + str(state[0]) + ' provision=' + str(state[1]))
            last = state
        if provision.get('status') == 'failed' or body.get('status') == 'failed':
            fail(what + ' failed: ' + str(provision.get('failure_reason') or body.get('failure_reason') or 'no reason given'))
        if done(body, provision):
            return body, provision
        time.sleep(10)
    fail('Timed out waiting for ' + what)


def action(actions):
    actions = list(actions or [])
    if actions == ['no-op']:
        return 'no_op'
    if 'delete' in actions and 'create' in actions:
        return 'replace'
    return actions[0] if actions else 'no_op'


def conformance(results):
    results = results or {}
    groups = [('helio.baseline', 'baseline_violations', 'Helio baseline rules'),
              ('helio.contract', 'contract_violations', 'Environment contract rules'),
              ('helio.production', 'production_violations', 'Production rules'),
              ('helio.policy', 'violations', 'Account policy')]
    rules = []
    for rule_id, key, label in groups:
        found = results.get(key)
        if found is None:
            rules.append({'id': rule_id, 'result': 'not_evaluable', 'message': label + ': not reported'})
        elif found:
            rules.append({'id': rule_id, 'result': 'fail',
                          'message': label + ': ' + '; '.join(str(v.get('message', v) if isinstance(v, dict) else v) for v in found)[:400]})
        else:
            rules.append({'id': rule_id, 'result': 'pass', 'message': label + ': no violations'})
    return {'passed': results.get('status') == 'passed' and not any(r['result'] == 'fail' for r in rules), 'rules': rules}


def plan():
    token, _ = login('HELIO_CI_CREDENTIALS')
    service = json.loads(pathlib.Path('helio/service.json').read_text())
    status, accounts = call('GET', '/admin/cloud-accounts', token)
    account = [a for a in accounts.get('accounts', []) if a.get('auth_method') == 'assume_role']
    if len(account) != 1:
        fail('Expected exactly one provisioning cloud account in Helio')
    service['cloud_account_id'] = account[0]['id']
    # The repository names the image; Helio's account record supplies the registry.
    service['image'] = (account[0]['account_identifier'] + '.dkr.ecr.' + service['region'] + '.amazonaws.com/' + service['image'])
    status, body = call('POST', '/deploy/batch', token, {'deployments': [service]},
                        {'Idempotency-Key': 'provision:' + SHA[:12] + ':' + RUN})
    if status >= 300:
        fail('Helio refused the request (HTTP ' + str(status) + '): ' + json.dumps(body)[:600])
    deploy_id = body['deployments'][0].get('deploy_id')
    if not deploy_id:
        fail('Helio refused the request: ' + json.dumps(body['deployments'][0])[:600])
    print('Helio run ' + deploy_id)
    _, provision = wait(token, deploy_id, lambda b, p: p.get('status') in ('planned', 'active') and p.get('plan_sha256'), 'plan', 10)
    changes = sorted(((provision.get('evidence_summary') or {}).get('resource_changes') or []), key=lambda r: r['address'])
    resources = [{'address': r['address'], 'type': r['type'], 'action': action(r.get('actions'))}
                 for r in changes if r.get('mode', 'managed') == 'managed']
    counts = {k: sum(1 for r in resources if r['action'] == k) for k in ['create', 'update', 'replace', 'delete', 'no_op']}
    summary = {'schema': 'helio.infra-plan-summary/1', 'commit': SHA,
               'environment': service['environment'], 'plan_sha256': provision['plan_sha256'],
               'generated_at': now(),
               'apex': {'run_id': deploy_id, 'contract_id': provision.get('contract_id'),
                        'contract_version': provision.get('contract_version')},
               'counts': counts, 'resources': resources[:2000],
               'conformance': conformance(provision.get('policy_results'))}
    pathlib.Path('helio-plan-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('Plan ' + provision['plan_sha256'][:12] + ': ' + ', '.join(k + ' ' + str(v) for k, v in counts.items()))
    output('deploy_id', deploy_id)
    output('plan_sha256', provision['plan_sha256'])
    output('needs_apply', 'false' if provision.get('status') == 'active' else 'true')
    if not summary['conformance']['passed']:
        fail('The plan does not conform to policy; see helio-plan-summary.json')


def apply():
    deploy_id, expected = os.environ['HELIO_DEPLOY_ID'], os.environ['HELIO_PLAN_SHA256']
    token, _ = login('HELIO_CI_CREDENTIALS')
    provision = deploy_status(token, deploy_id).get('provision') or {}
    if provision.get('plan_sha256') != expected:
        fail('Refusing to apply: the plan in Helio is not the plan that was reviewed')
    if provision.get('status') == 'planned':
        approver, c = login('HELIO_APPROVER_CREDENTIALS')
        status, body = call('POST', '/deploy/' + deploy_id + '/approve-infra', approver,
                            {'decision': 'approved', 'password': c['password'],
                             'reason': 'Approved at the release gate, run ' + RUN + ', plan ' + expected[:12]})
        if status >= 300:
            fail('Helio refused the approval (HTTP ' + str(status) + '): ' + json.dumps(body)[:400])
        print('Approval recorded for plan ' + expected[:12])
    _, provision = wait(token, deploy_id, lambda b, p: p.get('status') == 'active', 'apply', 20)
    if provision.get('approved_plan_sha256') not in (None, expected):
        fail('Helio applied a different plan than the one reviewed')
    print('Applied plan ' + expected[:12])


def verify():
    deploy_id = os.environ['HELIO_DEPLOY_ID']
    token, _ = login('HELIO_CI_CREDENTIALS')
    body, provision = wait(token, deploy_id, lambda b, p: b.get('status') == 'complete' and p.get('status') == 'active', 'rollout', 15)
    outputs = {k: v.get('value') for k, v in (provision.get('outputs') or {}).items()}
    checks = {'provision_active': provision.get('status') == 'active',
              'service_rolled_out': body.get('status') == 'complete',
              'drift': provision.get('drift_status') or 'not_checked',
              'permission_boundary_set': bool(outputs.get('permission_boundary'))}
    summary = {'schema': 'helio.infra-verify/1', 'commit': SHA, 'verified_at': now(), 'run_id': deploy_id,
               'cluster': outputs.get('cluster'), 'service': outputs.get('service'), 'region': outputs.get('region'),
               'endpoint': outputs.get('alb_dns'), 'checks': checks}
    pathlib.Path('helio-infra-verify.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))
    if not (checks['provision_active'] and checks['service_rolled_out'] and checks['permission_boundary_set']):
        fail('Verification failed')


if __name__ == '__main__':
    if not URL:
        fail('Set the HELIO_URL repository variable')
    {'plan': plan, 'apply': apply, 'verify': verify}[sys.argv[1]]()
