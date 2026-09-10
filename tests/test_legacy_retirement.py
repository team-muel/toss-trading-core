"""Retirement is an explicitly reviewed maintenance action, never deployment."""
from pathlib import Path
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from asset_management.cli.legacy_retirement import (ALERTS, apply_plan, gcp_plan, identity, systemd_plan, main)

PROJECT='sample-archive-project'
INSTANCE='12345678'
METRIC='foundation_runner_ok_count'


def policy(instance=INSTANCE, **changes):
    result={'name':f'projects/{PROJECT}/alertPolicies/1234',
        'displayName':'Toss Foundation runner heartbeat missing','enabled':True,
        'conditions':[{'conditionAbsent': {'filter': f'metric.type="logging.googleapis.com/user/{METRIC}" AND resource.type="gce_instance" AND resource.labels.instance_id="{instance}"'}}]}
    return result|changes


def metric(instance=INSTANCE):
    return {'name':METRIC,'filter': f'resource.type="gce_instance" AND resource.labels.instance_id="{instance}" AND jsonPayload.event="foundation_runner_ok"'}


def test_gcp_exact_scope_deletes_policies_before_metrics():
    plan=gcp_plan(project=PROJECT,instance=INSTANCE,policies=[policy()],metrics=[metric()])
    assert len(plan['commands'])==2
    assert plan['commands'][0][:4]==['gcloud','monitoring','policies','delete']
    assert plan['commands'][1][:4]==['gcloud','logging','metrics','delete']
    assert all(f'--project={PROJECT}' in command for command in plan['commands'])


def test_plan_hash_revalidation_prevents_changed_inventory_execution():
    plan=gcp_plan(project=PROJECT,instance=INSTANCE,policies=[policy()],metrics=[metric()])
    runner=Mock()
    with pytest.raises(ValueError,match='plan changed'):
        apply_plan(plan,'wrong-digest',run=runner)
    runner.assert_not_called()
    apply_plan(plan,identity(plan),run=runner)
    assert [call.args[0] for call in runner.call_args_list]==plan['commands']


def test_other_instance_resources_are_retained():
    plan=gcp_plan(project=PROJECT,instance=INSTANCE,policies=[policy('999')],metrics=[metric('999')])
    assert plan['commands']==[]
    assert len(plan['retained_for_review'])==2


def test_shared_metric_and_unrelated_policy_are_never_deleted():
    another=policy(name=f'projects/{PROJECT}/alertPolicies/5678',displayName='Canonical monitoring')
    plan=gcp_plan(project=PROJECT,instance=INSTANCE,policies=[policy(),another],metrics=[metric()])
    assert len(plan['commands'])==1
    assert '5678' not in json.dumps(plan['commands'])
    assert plan['retained_for_review'][0]['name']==METRIC


def test_dynamic_policy_selector_prevents_metric_deletion():
    another=policy(name=f'projects/{PROJECT}/alertPolicies/999',displayName='Dynamic monitoring',
                   conditions=[{'conditionPrometheusQueryLanguage':{'query':'some regexp metric selector'}}])
    plan=gcp_plan(project=PROJECT,instance=INSTANCE,policies=[policy(),another],metrics=[metric()])
    assert len(plan['commands'])==1
    assert plan['retained_for_review']


def test_or_filter_cannot_escape_scoped_instance():
    source=policy()
    source['conditions'][0]['conditionAbsent']['filter'] += ' OR resource.labels.instance_id="other"'
    plan=gcp_plan(project=PROJECT,instance=INSTANCE,policies=[source],metrics=[metric()])
    assert not plan['commands']


def test_invalid_or_duplicate_cloud_inventory_fails_closed():
    for project,instance,policies,metrics in [
        ('bad','123',[policy()],[]),(PROJECT,'not-number',[policy()],[]),
        (PROJECT,INSTANCE,[policy(),policy()],[]),
        (PROJECT,INSTANCE,[],[metric(),metric()]),
        (PROJECT,INSTANCE,[policy(name='projects/other/alertPolicies/1')],[]),
    ]:
        with pytest.raises(ValueError):
            gcp_plan(project=project,instance=instance,policies=policies,metrics=metrics)


def unit_runner(root, names):
    def run(argv):
        if argv[1] in ('list-unit-files','list-units'):
            return '\n'.join(f'{name} enabled' for name in names)
        if '--property=FragmentPath' in argv:
            name=argv[2]
            return str(root/name) if (root/name).exists() else ''
        if '--property=ActiveState' in argv:
            return 'inactive'
        return ''
    return Mock(side_effect=run)


def test_systemd_plan_is_read_only_and_apply_stops_exact_units(tmp_path):
    names=['toss-foundation.timer','toss-foundation.service','toss-research-automation@.service',
           'toss-research-automation@daily.service','canonical-os.service']
    for name in names:
        (tmp_path/name).write_text('[Unit]\nDescription=synthetic')
    runner=unit_runner(tmp_path,names)
    plan=systemd_plan(run=runner,root=tmp_path,host='test-host')
    assert all(call.args[0][1] in ('list-unit-files','list-units','show') for call in runner.call_args_list)
    assert plan['commands'][0][-1].endswith('.timer')
    assert all('@.service' not in command[-1] for command in plan['commands'])
    runner.reset_mock()
    apply_plan(plan,identity(plan),run=runner)
    assert (tmp_path/'canonical-os.service').is_file()
    assert all(not (tmp_path/n).exists() for n in names if n!='canonical-os.service')
    assert ['systemctl','daemon-reload'] in [call.args[0] for call in runner.call_args_list]
    assert all('enable' not in call.args[0] for call in runner.call_args_list)


def test_unknown_unit_is_not_glob_deleted(tmp_path):
    (tmp_path/'toss-research-unreviewed.service').write_text('unexpected')
    runner=unit_runner(tmp_path,[])
    with pytest.raises(ValueError,match='manual review'):
        systemd_plan(run=runner,root=tmp_path)
    assert (tmp_path/'toss-research-unreviewed.service').exists()


def test_unit_changed_after_plan_has_zero_effects(tmp_path):
    p=tmp_path/'toss-foundation.timer';p.write_text('old')
    runner=unit_runner(tmp_path,[p.name])
    plan=systemd_plan(run=runner,root=tmp_path)
    runner.reset_mock()
    p.write_text('changed')
    with pytest.raises(ValueError,match='unit changed before retirement'):
        apply_plan(plan,identity(plan),run=runner)
    assert p.read_text()=='changed'
    runner.assert_not_called()


def test_symlink_target_changed_after_plan_has_zero_effects(tmp_path):
    target=tmp_path/'retired-unit-target.service';target.write_text('old target bytes')
    link=tmp_path/'toss-foundation.timer';link.symlink_to(target.name)
    runner=unit_runner(tmp_path,[link.name])
    plan=systemd_plan(run=runner,root=tmp_path)
    recorded=plan['unit_files'][link.name]
    assert recorded['symlink']==target.name
    assert recorded['target']==str(target.resolve())
    assert 'target_sha256' in recorded
    runner.reset_mock()
    target.write_text('changed target bytes')
    with pytest.raises(ValueError,match='unit changed before retirement'):
        apply_plan(plan,identity(plan),run=runner)
    assert link.is_symlink()
    assert target.read_text()=='changed target bytes'
    runner.assert_not_called()


def test_cli_dry_run_never_executes_deletes(monkeypatch,capsys):
    from asset_management.cli import legacy_retirement as module
    def command(argv):
        assert 'delete' not in argv
        return json.dumps([policy()] if 'policies' in argv else [metric()])
    monkeypatch.setattr(module,'command',command)
    assert main(['gcp','--project',PROJECT,'--instance-id',INSTANCE])==0
    assert 'plan_sha256' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(['gcp','--project',PROJECT,'--instance-id',INSTANCE,'--apply'])


def test_command_uses_windows_gcloud_cmd_when_available(monkeypatch):
    from asset_management.cli import legacy_retirement as module
    seen = {}

    def fake_which(name):
        return 'C:/sdk/gcloud.cmd' if name == 'gcloud.cmd' else None

    def fake_run(argv, **kwargs):
        seen['argv'] = argv
        return SimpleNamespace(stdout='[]')

    monkeypatch.setattr(module.shutil, 'which', fake_which)
    monkeypatch.setattr(module.subprocess, 'run', fake_run)
    assert module.command(['gcloud', 'monitoring', 'policies', 'list']) == '[]'
    assert seen['argv'] == ['C:/sdk/gcloud.cmd', 'monitoring', 'policies', 'list']
