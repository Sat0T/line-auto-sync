"""Prepare, but never install/start, a bounded-state rootless daily worker."""
import argparse
import ast
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re

LABEL = 'com.sat0t.line-auto-sync.daily-collect'
SEND_LABEL = 'com.sat0t.line-auto-sync.daily-send'
STATE_ARTIFACTS = ('normal-delivery.sqlite', 'normal-delivery.sqlite-wal', 'normal-delivery.sqlite-shm',
                   'normal-delivery.sqlite-journal',
                   'named-cache.sqlite', 'named-cache.sqlite-wal', 'named-cache.sqlite-shm',
                   'named-cache.sqlite-journal',
                   'new.csv', 'lastid', 'sentid', 'sent_at', 'initial_scan_id')
RUNTIME_FILES = ('checkpoint.py', 'cli.py', 'delivery_state.py', 'legacy_names.py',
                 'normal_hold.py', 'presentation.py', 'recovery.py', 'resolver.py',
                 'runner.py', 'runtime_paths.py', 'sender.py', 'setup_normal.py',
                 'sync.py', 'transport.py')

ENTRY = r'''
import hashlib,json,os,pathlib,shutil,sys,time
PROFILE=pathlib.Path(PROFILE_PATH)
RUNTIME=pathlib.Path(RUNTIME_PATH)
def load():return json.loads((PROFILE/'config/config.json').read_text(encoding='utf-8'))
def binding(value):
    value=dict(value);value['enabled']=False
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
def inactive(value):return value.get('enabled') is not True or value.get('normal_line',{}).get('enabled') is not True
def activation():
    marker=PROFILE/'state/daily-activation.json'
    expected={'mode':'send','profile_binding':PROFILE_BINDING}
    if marker.is_symlink():raise RuntimeError('unsafe_daily_activation')
    if marker.exists():
        if json.loads(marker.read_text())!=expected:raise RuntimeError('daily_activation_binding_changed')
        return
    if any((PROFILE/'state'/name).exists() or (PROFILE/'state'/name).is_symlink() for name in STATE_ARTIFACTS):
        raise RuntimeError('old_queue_cannot_become_daily_send')
    with os.fdopen(os.open(str(marker),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as out:
        json.dump(expected,out);out.flush();os.fsync(out.fileno())
def main():
    config=load()
    if inactive(config):
        print('DAILY_COLLECT: disabled',flush=True);return 0
    if binding(config)!=PROFILE_BINDING:
        print('DAILY_COLLECT: profile_changed',flush=True);return 0
    for name,digest in RUNTIME_HASHES.items():
        path=RUNTIME/name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('runtime_version_changed')
    os.environ['LINE_AUTO_SYNC_BASE_DIR']=str(PROFILE)
    os.environ['LINE_AUTO_SYNC_CONFIG_DIR']=str(PROFILE/'config')
    os.environ['LINE_AUTO_SYNC_STATE_DIR']=str(PROFILE/'state')
    sys.path.insert(0,str(RUNTIME))
    import cli,runner
    if SEND_ENABLED:activation()
    def collect_with_capacity_check():
        import sync
        def check():
            total=0
            for name in STATE_ARTIFACTS:
                path=PROFILE/'state'/name
                if path.is_symlink():raise RuntimeError('unsafe_daily_state_file')
                if path.is_file():total+=path.stat().st_size
            free=shutil.disk_usage(PROFILE/'state').free
            paused=total>=MAX_STATE_BYTES or free<MIN_FREE_BYTES
            runner.write_marker('daily-health.json',{'phase':'capacity_paused' if paused else 'ready',
                'state_bytes':total,'max_state_bytes':MAX_STATE_BYTES,'free_bytes':free,
                'min_free_bytes':MIN_FREE_BYTES,'at':int(time.time())})
            if paused:runner.request_stop()
            return not paused
        if check():
            sync.collect()
            check()
    try:runner.start(cli.doctor,cli.queue_status,send=SEND_ENABLED,resume_pending=SEND_ENABLED,
                     collect_step=collect_with_capacity_check)
    except RuntimeError:
        current=load()
        if inactive(current) or binding(current)!=PROFILE_BINDING:return 0
        raise
    return 0
if __name__=='__main__':
    try:raise SystemExit(main())
    except KeyboardInterrupt:raise SystemExit(0)
    except Exception as error:
        print('DAILY_COLLECT: failed '+type(error).__name__,file=sys.stderr,flush=True)
        try:
            import syslog
            syslog.syslog(syslog.LOG_ERR,'LINE Auto Sync daily collect failed: '+type(error).__name__)
        except Exception:pass
        raise SystemExit(1)
'''


def normalized_binding(config):
    config = dict(config); config['enabled'] = False
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def overlaps(left, right):
    return left == right or left in right.parents or right in left.parents


def management_templates():
    source=Path(__file__).with_name('manage_ipad.py')
    spec=importlib.util.spec_from_file_location('daily_management_templates',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def root_sources(output, spec, entry_source):
    """Reuse bounded domain cleanup and narrowly scoped, validated sudo setup."""
    module=management_templates()
    tree=ast.parse(module.ROOT_ACTION)
    functions='\n\n'.join(ast.unparse(node) for node in tree.body if isinstance(node,ast.FunctionDef)
                            and node.name in ('secure_root_file','registered','stop_or_status'))
    root_path='/var/root/las-daily-send-control.py'
    raw_plist=plistlib.dumps(spec)
    source=('import hashlib,json,os,pathlib,plistlib,stat,subprocess,sys,time\n'
        +'LABEL='+repr(SEND_LABEL)+'\nlabel=LABEL\ndomains=("system","user/foreground")\n'
        +'SOURCE=pathlib.Path('+repr(str(output/'daily-send.plist'))+')\n'
        +'ENTRY=pathlib.Path('+repr(str(output/'daily-send.py'))+')\n'
        +'PLIST_SHA='+repr(hashlib.sha256(raw_plist).hexdigest())+'\n'
        +'ENTRY_SHA='+repr(hashlib.sha256(entry_source.encode()).hexdigest())+'\n'
        +'EXPECTED_SPEC='+repr(spec)+'\n'+functions+r'''
def verified_source():
    if SOURCE.is_symlink() or ENTRY.is_symlink():raise RuntimeError('daily_source_symlink')
    raw=SOURCE.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=PLIST_SHA or hashlib.sha256(ENTRY.read_bytes()).hexdigest()!=ENTRY_SHA:
        raise RuntimeError('daily_source_version_changed')
    if plistlib.loads(raw)!=EXPECTED_SPEC:raise RuntimeError('daily_job_spec_changed')
    return raw
def main():
    if os.getuid()!=0:raise RuntimeError('root_required')
    secure_root_file(pathlib.Path('/var/root/las-daily-send-control.py'),0o600)
    q=json.load(sys.stdin)
    if not isinstance(q,dict) or set(q)!={'action'}:raise RuntimeError('fixed_daily_action_only')
    action=q['action']
    target=pathlib.Path('/var/root')/(LABEL+'.plist')
    if action=='probe':return {'fixed_root_bridge':True,'mode':'daily_send','started':False}
    if action=='install':
        raw=verified_source()
        if target.exists() or target.is_symlink():
            if target.is_symlink() or target.read_bytes()!=raw:raise RuntimeError('root_daily_plist_conflict')
        else:
            with os.fdopen(os.open(str(target),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as out:
                out.write(raw);out.flush();os.fsync(out.fileno())
        secure_root_file(target,0o600)
        return {'installed':True,'started':False}
    if action=='start':
        raw=verified_source()
        secure_root_file(target,0o600)
        if target.is_symlink() or target.read_bytes()!=raw:raise RuntimeError('root_daily_plist_not_verified')
        if any(registered(domain) for domain in domains):raise RuntimeError('daily_job_already_registered')
        result=subprocess.run(['/var/jb/bin/launchctl','bootstrap','system',str(target)],
                              stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
        if result.returncode:raise RuntimeError('daily_start_unknown_reconcile_status')
        return {'start_command_accepted':True,'delivery_proven':False}
    if action in ('status','stop'):return stop_or_status(action)
    raise RuntimeError('unsupported_daily_action')
if __name__=='__main__':
    try:print(json.dumps(main()))
    except Exception as error:
        print(json.dumps({'ok':False,'error':type(error).__name__,'automatic_restart':False}))
        raise SystemExit(1)
''')
    compile(source,'<fixed-daily-root-controller>','exec')
    setup=module.ROOT_SETUP.replace('las-managed-control','las-daily-send-control')
    bootstrap=('BRIDGE_BASE64='+repr(base64.b64encode(source.encode()).decode())+'\n'
               +'BRIDGE_SHA256='+repr(hashlib.sha256(source.encode()).hexdigest())+'\n'+setup)
    compile(bootstrap,'<daily-root-bootstrap>','exec')
    return source,bootstrap


def prepare(profile, stage, output, *, send=False, confirm_delivery_binding=False,
            max_state_bytes=256*1024*1024, min_free_bytes=64*1024*1024):
    profile, stage, output = Path(profile).absolute(), Path(stage).absolute(), Path(output).absolute()
    if any(p.is_symlink() for p in (profile, stage, output)) or output.exists():
        raise ValueError('new_nonlinked_output_required')
    config_path = profile / 'config/config.json'
    if (config_path.is_symlink() or not config_path.is_file()
            or (profile / 'config').is_symlink() or (profile / 'state').is_symlink()
            or not (profile / 'state').is_dir()):
        raise ValueError('existing_stopped_profile_required')
    config = json.loads(config_path.read_text(encoding='utf-8'))
    chats = config.get('normal_line', {}).get('allowed_chat_pks')
    if (config.get('schema_version') != 1 or config.get('enabled') is not False
            or config.get('normal_line', {}).get('enabled') is not True
            or config.get('source_id') != 'MVP_IPAD_LIVE'
            or not isinstance(chats, list) or len(chats) != 1
            or type(chats[0]) is not int or not 0 < chats[0] <= 2**63 - 1):
        raise ValueError('disabled_single_authorized_chat_profile_required')
    if (type(max_state_bytes) is not int or max_state_bytes<1024*1024
            or type(min_free_bytes) is not int or min_free_bytes<1024*1024):
        raise ValueError('positive_capacity_budget_required')
    if send:
        if confirm_delivery_binding is not True:
            raise ValueError('confirm_actual_receiver_and_recipient_binding_before_send')
        from urllib.parse import urlsplit
        transport=config.get('transport',{});url=urlsplit(transport.get('endpoint',''))
        if (url.scheme!='https' or url.netloc!='script.google.com'
                or not re.fullmatch(r'/macros/s/[A-Za-z0-9_-]+/exec',url.path)
                or url.query or url.fragment or not isinstance(transport.get('token'),str)
                or not transport['token'].strip()):
            raise ValueError('existing_independent_gas_transport_required')
        activation=profile/'state/daily-activation.json'
        if activation.is_symlink():raise ValueError('unsafe_daily_activation')
        if activation.exists():
            if json.loads(activation.read_text())!={'mode':'send','profile_binding':normalized_binding(config)}:
                raise ValueError('previous_daily_profile_binding_mismatch')
        elif any((profile/'state'/name).exists() or (profile/'state'/name).is_symlink() for name in STATE_ARTIFACTS):
            raise ValueError('first_daily_send_requires_fresh_queue_not_old_state')
    marker = profile / 'state/worker-status.json'
    if marker.is_symlink() or (marker.is_file() and json.loads(marker.read_text()).get('state') == 'running'):
        raise ValueError('confirm_previous_worker_stopped')
    database = Path(config['line_db']).resolve(strict=True)
    if not database.is_file():
        raise ValueError('line_database_file_required')
    pointer = profile / 'state/dbpath'
    if (pointer.is_symlink() or not pointer.is_file()
            or Path(pointer.read_text().strip()).resolve(strict=True) != database):
        raise ValueError('profile_database_pointer_mismatch')
    runtime = stage / 'runtime'
    resolved = (profile.resolve(), stage.resolve(), output.resolve(), database.parent)
    if (any(overlaps(resolved[i], resolved[j]) for i, j in ((0, 1), (0, 2), (1, 2)))
            or any(overlaps(p, database.parent) for p in resolved[:3])):
        raise ValueError('separate_profile_runtime_output_and_line_directory_required')
    manifest_path = stage / 'manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file() or runtime.is_symlink():
        raise ValueError('verified_managed_stage_required')
    manifest = json.loads(manifest_path.read_text())
    hashes = {}
    for name in RUNTIME_FILES:
        path = runtime / name; digest = manifest.get('runtime/' + name)
        if (path.is_symlink() or not path.is_file() or not isinstance(digest, str)
                or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise ValueError('staged_runtime_version_mismatch')
        hashes[name] = digest
    source = ('PROFILE_PATH=' + repr(str(profile)) + '\nRUNTIME_PATH=' + repr(str(runtime))
              + '\nPROFILE_BINDING=' + repr(normalized_binding(config))
              + '\nRUNTIME_HASHES=' + repr(hashes)
              + '\nSEND_ENABLED='+repr(send)+'\nSTATE_ARTIFACTS='+repr(STATE_ARTIFACTS)
              + '\nMAX_STATE_BYTES='+repr(max_state_bytes)+'\nMIN_FREE_BYTES='+repr(min_free_bytes)+'\n'+ENTRY)
    compile(source, '<daily-collect-entry>', 'exec')
    mode='send' if send else 'collect_only';stem='daily-send' if send else 'daily-collect'
    label=SEND_LABEL if send else LABEL
    spec = {'Label': label, 'UserName': 'mobile', 'RunAtLoad': True,
            'KeepAlive': {'SuccessfulExit': False}, 'ThrottleInterval': 60,
            'Umask': 0o077, 'ExitTimeOut': 20,
            'EnvironmentVariables': {'HOME': '/var/mobile'},
            'ProgramArguments': ['/var/jb/usr/bin/python3', str(output / (stem+'.py'))]}
    generated=[(stem+'.py',source.encode()),(stem+'.plist',plistlib.dumps(spec))]
    if send:
        controller,bootstrap=root_sources(output,spec,source)
        generated.extend((('daily-send-control.py',controller.encode()),('daily-root-bootstrap.py',bootstrap.encode())))
    os.umask(0o077)
    output.mkdir(mode=0o700)
    for name, raw in generated:
        with (output / name).open('xb') as stream:stream.write(raw)
    plan = {'mode': mode, 'installed': False, 'started': False,
            'label': label, 'runtime_manifest_verified': True,
            'entry_sha256': hashlib.sha256(source.encode('utf-8')).hexdigest(),
            'profile_binding': normalized_binding(config), 'runtime_hashes': hashes,
            'restart_only_after_unsuccessful_exit': True, 'throttle_seconds': 60,
            'operator_confirmed_delivery_binding':bool(send and confirm_delivery_binding),
            'actual_delivery_verified':False,'max_state_bytes':max_state_bytes,'min_free_bytes':min_free_bytes,
            'new_persistent_stdout_stderr_files': False}
    with (output / 'plan.json').open('x', encoding='utf-8') as stream:json.dump(plan, stream, indent=2)
    return {'prepared': True, 'mode': mode, 'installed': False, 'started': False,
            'external_send': False, 'sending_configured':send,'runtime_manifest_verified': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, type=Path)
    parser.add_argument('--stage', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--send', action='store_true', help='Opt in to the explicitly approved daily profile; never starts it')
    parser.add_argument('--confirm-delivery-binding', action='store_true', help='Operator has checked actual GAS endpoint and owner recipient; not proof of delivery')
    parser.add_argument('--max-state-bytes',type=int,default=256*1024*1024)
    parser.add_argument('--min-free-bytes',type=int,default=64*1024*1024)
    args = parser.parse_args()
    print(json.dumps(prepare(args.profile,args.stage,args.output,send=args.send,
        confirm_delivery_binding=args.confirm_delivery_binding,max_state_bytes=args.max_state_bytes,min_free_bytes=args.min_free_bytes)))


if __name__ == '__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'prepared': False, 'reason': str(error) if isinstance(error, ValueError) else type(error).__name__,
                          'installed': False, 'started': False, 'preserve_existing_state': True}))
        raise SystemExit(1)
