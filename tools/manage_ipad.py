"""Private SSH controller for one bounded LINE Auto Sync trial; never guesses credentials."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys

REMOTE = r'''
import base64, hashlib, json, os, pathlib, plistlib, re, shutil, subprocess, sys, time
if os.getuid() != 501:
    raise RuntimeError('mobile_login_required')
q=json.load(sys.stdin)
run=q.get('run','')
if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}',run):
    raise RuntimeError('invalid_run')
root=pathlib.Path('/var/mobile/las-managed')
stage=root/run
base_name=q.get('base','/var/mobile/las-lock-n1')
if not re.fullmatch(r'/var/mobile/las-lock-[a-z0-9-]{1,40}',base_name):
    raise RuntimeError('invalid_trial_base')
base=pathlib.Path(base_name)
for p in (root,stage,base):
    if p.is_symlink(): raise RuntimeError('symlink_refused')
os.umask(0o077)
def put_new(p,data):
    with p.open('xb') as f: f.write(data)
def emit(x): print(json.dumps(x))

def native_line_launch(os_version):
    if os_version!='16.7.14':return {'supported':False,'reason':'native_os_not_reviewed','foreground_verified':False}
    from datetime import datetime,timedelta,timezone
    def now():return datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='microseconds')
    tool=shutil.which('uiopen')
    if not tool:return {'supported':False,'reason':'existing_uiopen_missing','foreground_verified':False}
    help_result=subprocess.run([tool,'--help'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
    if help_result.returncode or b'--bundleid' not in help_result.stdout:
        return {'supported':False,'reason':'bundleid_contract_not_observed','foreground_verified':False}
    record={'event':'line_launch_request','started_jst':now(),'bundle':'jp.naver.line',
            'foreground_verified':False,'lock_state_verified':False}
    # A durable request record precedes screen-changing input. No automatic retry.
    path=base/'ui-actions.jsonl'
    def write(value):
        with os.fdopen(os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_APPEND|getattr(os,'O_NOFOLLOW',0),0o600),'a') as f:
            f.write(json.dumps(value)+'\n');f.flush();os.fsync(f.fileno())
    write(record)
    try:
        result=subprocess.run([tool,'--bundleid','jp.naver.line'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
        record.update(command_completed=result.returncode==0,returncode=result.returncode)
    except (OSError,subprocess.TimeoutExpired):
        record.update(command_completed=False,outcome='unknown_no_retry')
    record.update(event='line_launch_command_finished',finished_jst=now())
    write(record)
    return dict(record,supported=True)
def verify_stage():
    saved=json.loads((stage/'manifest.json').read_text())
    for name,digest in saved.items():
        p=stage/name
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('staged_version_changed')
def run_helper(action):
    args=['/var/jb/usr/bin/python3',str(stage/'tools/keep_lock_delivery.py'),action,'--base',str(base)]
    if action=='prepare': args+=['--baseline',q['baseline'],'--runtime',str(stage/'runtime')]
    r=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45 if action=='prepare' else 8)
    if r.returncode: raise RuntimeError('helper_'+action+'_failed')
    return r.stdout.decode('utf-8')
def descriptor():
    source=base/'worker.plist'
    spec=plistlib.loads(source.read_bytes())
    label=spec.get('Label','')
    if not re.fullmatch(r'com\.sat0t\.line-auto-sync\.lock-[a-z0-9-]{1,40}',label):
        raise RuntimeError('unexpected_job_label')
    if label!='com.sat0t.line-auto-sync.'+base.name.removeprefix('las-'):
        raise RuntimeError('job_label_base_mismatch')
    if spec.get('UserName')!='mobile' or spec.get('KeepAlive',False) is not False:
        raise RuntimeError('unsafe_job_specification')
    expected=['/var/jb/usr/bin/python3',str(base/'tools/keep_lock_delivery.py'),'supervise','--base',str(base)]
    if spec.get('ProgramArguments')!=expected:
        raise RuntimeError('unexpected_job_program')
    plan=json.loads((base/'plan.json').read_text())
    limits={'send_max':1,'retries':0,'observe_seconds':180,'hard_limit_seconds':240,
            'stop_upper_seconds':250,'protocol':'legacy-OK'}
    if any(plan.get(k)!=v for k,v in limits.items()): raise RuntimeError('unexpected_trial_limits')
    return source,label
def privileged(action):
    source,label=descriptor()
    request={'action':action,'source':str(source),'label':label,
             'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    r=subprocess.run(['sudo','-n','/var/jb/usr/bin/python3','/var/root/las-managed-control.py'],input=json.dumps(request).encode(),
                     stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
    if r.returncode: raise RuntimeError('noninteractive_root_or_job_control_unavailable')
    return json.loads(r.stdout)
action=q['action']
if action=='doctor':
    os_version=plistlib.loads(pathlib.Path('/System/Library/CoreServices/SystemVersion.plist').read_bytes()).get('ProductVersion')
    p=subprocess.run(['sudo','-n','/var/jb/usr/bin/python3','/var/root/las-managed-control.py'],
                     input=b'{"action":"probe"}',stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
    try: bridge=json.loads(p.stdout) if p.returncode==0 else {}
    except ValueError: bridge={}
    emit({'connected':True,'uid':os.getuid(),'python':list(sys.version_info[:3]),'os_version':os_version,
          'noninteractive_root_available':bridge.get('fixed_root_bridge') is True,
          'root_bridge_setup_required':bridge.get('fixed_root_bridge') is not True,
          'ui_tools':{n:shutil.which(n) is not None for n in ('uiopen','activator','notifyutil','lockdevice')},
          'screen_lock_verified':False,'unlock_automation':'unsupported_no_passcode_storage'})
elif action=='deploy':
    files=q['files']
    if not 1<=len(files)<=100: raise RuntimeError('invalid_deployment_size')
    for name,value in files.items():
        p=pathlib.PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts or not re.fullmatch(r'(tools|runtime)/[A-Za-z0-9_-]+\.py',name):
            raise RuntimeError('invalid_deployment_path')
        raw=base64.b64decode(value['data'],validate=True)
        if len(raw)>1000000 or hashlib.sha256(raw).hexdigest()!=value['sha256']:
            raise RuntimeError('deployment_checksum_mismatch')
    expected={k:v['sha256'] for k,v in files.items()}
    if stage.exists():
        saved=json.loads((stage/'manifest.json').read_text())
        if saved!=expected: raise RuntimeError('existing_stage_conflict')
        for name,digest in expected.items():
            if (stage/name).is_symlink() or hashlib.sha256((stage/name).read_bytes()).hexdigest()!=digest:
                raise RuntimeError('staged_version_changed')
        emit({'deployed':True,'reconciled':True,'files':len(expected)})
    else:
        root.mkdir(mode=0o700,exist_ok=True);stage.mkdir(mode=0o700)
        for name,value in files.items():
            target=stage/name;target.parent.mkdir(mode=0o700,exist_ok=True)
            put_new(target,base64.b64decode(value['data']))
        put_new(stage/'manifest.json',json.dumps(expected,sort_keys=True).encode())
        emit({'deployed':True,'reconciled':False,'files':len(expected)})
elif action=='prepare':
    if not re.fullmatch(r'/var/mobile/las-[a-z0-9-]+',q.get('baseline','')):
        raise RuntimeError('invalid_baseline')
    if not (stage/'manifest.json').is_file(): raise RuntimeError('deployment_required')
    verify_stage()
    marker=stage/'prepare-attempt.json'
    if marker.exists():
        if not (base/'worker.plist').is_file(): raise RuntimeError('previous_prepare_incomplete_requires_review')
    else:
        put_new(marker,json.dumps({'attempted_at':time.time()}).encode())
        run_helper('prepare')
    if json.loads((base/'config/config.json').read_text()).get('enabled') is not False:
        raise RuntimeError('prepared_profile_not_disabled')
    result=privileged('install')
    emit({'prepared':True,'root_plist_verified':result['installed'],'started':False})
elif action=='launch-line':
    verify_stage()
    if not base.is_dir():raise RuntimeError('prepare_profile_before_screen_action')
    version=plistlib.loads(pathlib.Path('/System/Library/CoreServices/SystemVersion.plist').read_bytes()).get('ProductVersion')
    emit(native_line_launch(version))
elif action=='ui-status':
    verify_stage()
    from datetime import datetime,timedelta,timezone
    namespace={'__name__':'private_lock_observer'}
    exec(compile((stage/'tools/observe_line_db.py').read_text(),'<private-lock-observer>','exec'),namespace)
    reader=namespace['LockSignal']()
    try: raw=reader.sample()
    finally: reader.close()
    emit({'device_jst':datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='microseconds'),
          'lock_signal_observed':raw,'lock_state_verified':False,
          'line_launch_automation':'launch-line_adapter_requires_existing_bundleid_tool',
          'lock_command_automation':'unsupported_until_native_tool_verified',
          'unlock_automation':'unsupported_no_passcode_storage'})
elif action=='start':
    verify_stage()
    marker=stage/'start-attempt.json'
    if marker.exists(): raise RuntimeError('start_already_attempted_reconcile_only')
    put_new(marker,json.dumps({'attempted_at':time.time()}).encode())
    result=privileged('start')
    emit({'start_command_accepted':result['started'],'run_completed':False,'delivery_proven':False})
elif action=='status':
    emit({'job':privileged('status'),'helper_status_private':run_helper('status'),
          'start_attempted':(stage/'start-attempt.json').exists()})
elif action=='post-unlock-once':
    if not privileged('status')['unregistered_in_checked_domains']:
        raise RuntimeError('unregister_and_confirm_stopped_before_post_unlock_read')
    emit({'post_unlock_read_private':run_helper('post-unlock-once'),'external_send':False})
elif action=='stop':
    helper_stop=False
    try:
        run_helper('stop'); helper_stop=True
    except Exception: pass
    result={'job':privileged('stop'),'helper_stop_command_accepted':helper_stop}
    try: result['helper_status_private']=run_helper('status')
    except Exception: result['helper_status_unavailable']=True
    emit(result)
elif action=='fetch':
    files={}
    for p in base.iterdir():
        if p.is_symlink() or not p.is_file(): continue
        if p.suffix=='.jsonl' or p.name in ('plan.json','payload-private.json','target-private.json',
                'http-result.json','finished.json','supervisor-started.json','worker-started.json',
                'network-started.json','http-lease.json','stop-request.json','post-unlock-claimed.json'):
            raw=p.read_bytes()
            if len(raw)>5000000: raise RuntimeError('log_too_large')
            files[p.name]={'data':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest()}
    emit({'files':files,'private':True})
else: raise RuntimeError('unsupported_action')
'''

ROOT_ACTION = r'''
import hashlib,json,os,pathlib,plistlib,re,stat,subprocess,sys,time
def secure_root_file(path,mode):
    if path.is_symlink(): raise RuntimeError('root_bridge_symlink_refused')
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=mode or info.st_nlink!=1:
        raise RuntimeError('root_bridge_ownership_or_mode_invalid')
secure_root_file(pathlib.Path('/var/root/las-managed-control.py'),0o600)
q=json.load(sys.stdin)
if os.getuid()!=0: raise RuntimeError('root_required')
if q.get('action')=='probe':
    print(json.dumps({'fixed_root_bridge':True,'protocol':1}))
    raise SystemExit(0)
source=pathlib.Path(q['source']);label=q['label']
if not re.fullmatch(r'/var/mobile/las-lock-[a-z0-9-]{1,40}/worker\.plist',str(source)) or source.is_symlink():
    raise RuntimeError('invalid_source')
if not re.fullmatch(r'com\.sat0t\.line-auto-sync\.lock-[a-z0-9-]{1,40}',label): raise RuntimeError('invalid_label')
if label!='com.sat0t.line-auto-sync.'+source.parent.name.removeprefix('las-'): raise RuntimeError('job_label_base_mismatch')
raw=source.read_bytes()
if hashlib.sha256(raw).hexdigest()!=q['sha256']: raise RuntimeError('job_version_changed')
spec=plistlib.loads(raw)
if spec.get('Label')!=label or spec.get('UserName')!='mobile' or spec.get('KeepAlive',False) is not False:
    raise RuntimeError('unsafe_job')
allowed={'Label','UserName','RunAtLoad','KeepAlive','Umask','ExitTimeOut','EnvironmentVariables','ProgramArguments'}
if (set(spec)!=allowed or spec.get('RunAtLoad') is not True or spec.get('Umask')!=0o077
        or type(spec.get('ExitTimeOut')) is not int or not 1<=spec['ExitTimeOut']<=20
        or spec.get('EnvironmentVariables')!={'HOME':'/var/mobile'}):
    raise RuntimeError('unexpected_privileged_job_fields')
base=source.parent
if spec.get('ProgramArguments')!=['/var/jb/usr/bin/python3',str(base/'tools/keep_lock_delivery.py'),'supervise','--base',str(base)]:
    raise RuntimeError('unexpected_job_program')
target=pathlib.Path('/var/root')/(label+'.plist')
def registered(domain,timeout=5):
    r=subprocess.run(['/var/jb/bin/launchctl','print',domain+'/'+label],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    if r.returncode==0:return True
    message=(r.stdout+r.stderr).lower()
    if b'could not find service' in message or b'could not find specified service' in message or b'no such process' in message:return False
    raise RuntimeError('job_query_inconclusive')
domains=('system','user/foreground')
def stop_or_status(action):
    # Keep the original envelope: two 15s bootouts and two 5s queries at most.
    # A failure in one domain must not prevent the remaining cleanup/queries.
    deadline=time.monotonic()+(40 if action=='stop' else 10)
    bootout={}
    if action=='stop':
        for domain in domains:
            timeout=min(15,max(0,deadline-time.monotonic()))
            if timeout<=0:
                bootout[domain]={'outcome':'budget_exhausted'}
                continue
            try:
                r=subprocess.run(['/var/jb/bin/launchctl','bootout',domain+'/'+label],
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=timeout)
                bootout[domain]={'outcome':'completed','returncode':r.returncode}
            except subprocess.TimeoutExpired:
                bootout[domain]={'outcome':'timeout'}
            except OSError:
                bootout[domain]={'outcome':'failed'}
    state={}; errors={}
    for domain in domains:
        timeout=min(5,max(0,deadline-time.monotonic()))
        if timeout<=0:
            state[domain]=None;errors[domain]='budget_exhausted'
            continue
        try:state[domain]=registered(domain,timeout)
        except subprocess.TimeoutExpired:
            state[domain]=None;errors[domain]='timeout'
        except (OSError,RuntimeError):
            state[domain]=None;errors[domain]='query_failed'
    return {'registered':state,'query_errors':errors,'bootout':bootout,
            'unregistered_in_checked_domains':all(state[domain] is False for domain in domains)}
if q['action']=='install':
    if target.exists() or target.is_symlink():
        if target.is_symlink() or target.read_bytes()!=raw: raise RuntimeError('root_plist_conflict')
    else:
        with os.fdopen(os.open(str(target),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as f:f.write(raw)
    print(json.dumps({'installed':True}))
elif q['action']=='start':
    if any(registered(d) for d in domains): raise RuntimeError('job_already_registered')
    if target.is_symlink() or target.read_bytes()!=raw: raise RuntimeError('root_plist_not_verified')
    r=subprocess.run(['/var/jb/bin/launchctl','bootstrap','system',str(target)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
    if r.returncode: raise RuntimeError('bootstrap_failed_reconcile_only')
    print(json.dumps({'started':True}))
elif q['action'] in ('status','stop'):
    print(json.dumps(stop_or_status(q['action'])))
else: raise RuntimeError('unsupported_root_action')
'''

ROOT_SETUP = r'''
import base64,hashlib,json,os,pathlib,re,stat,subprocess,sys,tempfile
BRIDGE=pathlib.Path('/var/root/las-managed-control.py')
INTERPRETER=pathlib.Path('/var/jb/usr/bin/python3')
SUPPORTED_SUDOERS=('/var/jb/etc/sudoers','/etc/sudoers')
RULE=b'mobile ALL=(root) NOPASSWD: /var/jb/usr/bin/python3 /var/root/las-managed-control.py\n'
def check(command):
    result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10,
                          env=dict(os.environ,LC_ALL='C',LANG='C'))
    if result.returncode:raise RuntimeError('root_setup_validation_failed')
    return result.stdout.decode('utf-8',errors='replace')
def write_new(path,data,mode):
    fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    with os.fdopen(fd,'wb') as stream:
        os.fchmod(stream.fileno(),mode)
        os.fchown(stream.fileno(),0,0)
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    info=path.stat()
    if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=mode or path.is_symlink():
        raise RuntimeError('root_setup_ownership_failed')
def install():
    if not hasattr(os,'getuid') or os.getuid()!=0:raise RuntimeError('one_time_owner_sudo_authentication_required')
    raw=base64.b64decode(BRIDGE_BASE64,validate=True)
    if hashlib.sha256(raw).hexdigest()!=BRIDGE_SHA256:raise RuntimeError('bridge_code_hash_mismatch')
    compile(raw,'<reviewed-root-bridge>','exec')
    visudo=next((p for p in ('/var/jb/usr/sbin/visudo','/usr/sbin/visudo') if pathlib.Path(p).is_file() and os.access(p,os.X_OK)),None)
    sudo=next((p for p in ('/var/jb/usr/bin/sudo','/usr/bin/sudo') if pathlib.Path(p).is_file() and os.access(p,os.X_OK)),None)
    if not visudo or not sudo:raise RuntimeError('visudo_and_sudo_required_before_any_install')
    version=check([sudo,'-V'])
    found=re.search(r'(?im)^\s*Sudoers(?: path| file)?\s*:\s*(/[^\r\n]+)\s*$',version)
    if not found or found.group(1).strip() not in SUPPORTED_SUDOERS:
        raise RuntimeError('active_sudoers_path_unconfirmed_no_install')
    config=pathlib.Path(found.group(1).strip())
    if config.is_symlink():raise RuntimeError('sudoers_file_symlink_requires_review')
    main_info=config.stat()
    if main_info.st_uid!=0 or stat.S_IMODE(main_info.st_mode)&0o022:
        raise RuntimeError('unsafe_sudoers_file_ownership')
    includes=re.findall(r'(?m)^\s*[@#]includedir\s+"?([^\s"#]+)"?\s*(?:#.*)?$',config.read_text())
    allowed=('/var/jb/etc/sudoers.d','/etc/sudoers.d')
    directories=[pathlib.Path(p) for p in includes if p in allowed]
    if len(directories)!=1:raise RuntimeError('existing_sudoers_include_directory_required_no_install')
    directory=directories[0]
    if not directory.is_dir() or directory.is_symlink():raise RuntimeError('unsafe_sudoers_include_directory')
    info=directory.stat()
    if info.st_uid!=0 or stat.S_IMODE(info.st_mode)&0o022:raise RuntimeError('unsafe_sudoers_directory_ownership')
    target=directory/'las-managed-control'
    if BRIDGE.exists() or BRIDGE.is_symlink() or target.exists() or target.is_symlink():
        raise RuntimeError('existing_root_setup_requires_review_no_overwrite')
    binary=INTERPRETER.resolve(strict=True).stat()
    if binary.st_uid!=0 or stat.S_IMODE(binary.st_mode)&0o022:raise RuntimeError('unsafe_interpreter_ownership')
    check([visudo,'-c','-f',str(config)])
    # A dot-prefixed candidate is ignored by sudo's includedir scanner. Link it
    # atomically only after validation; never expose a partial active rule.
    fd,name=tempfile.mkstemp(prefix='.las-managed-control-candidate-',dir=str(directory))
    candidate=pathlib.Path(name)
    candidate_inode=os.fstat(fd).st_ino
    rule_written=False
    try:
        with os.fdopen(fd,'wb') as stream:
            os.fchmod(stream.fileno(),0o440);os.fchown(stream.fileno(),0,0)
            stream.write(RULE);stream.flush();os.fsync(stream.fileno())
        check([visudo,'-c','-f',str(candidate)])
        write_new(BRIDGE,raw,0o600)
        os.link(str(candidate),str(target));rule_written=True
        candidate.unlink()
        check([visudo,'-c','-f',str(config)])
        result=subprocess.run([str(INTERPRETER),str(BRIDGE)],input=b'{"action":"probe"}',stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
        if result.returncode or json.loads(result.stdout).get('fixed_root_bridge') is not True:
            raise RuntimeError('installed_bridge_probe_failed')
    except Exception:
        # Roll back ONLY this newly created, unchanged rule. Preserve the bridge
        # and all pre-existing configuration for owner review; never broaden sudo.
        if (rule_written and not target.is_symlink() and target.read_bytes()==RULE
                and target.stat().st_uid==0 and target.stat().st_ino==candidate_inode):
            target.unlink()
        raise
    finally:
        if candidate.exists() and not candidate.is_symlink() and candidate.stat().st_ino==candidate_inode:
            candidate.unlink()
    print(json.dumps({'fixed_root_bridge_installed':True,'sudoers_syntax_valid':True,
                      'authorization_scope':'fixed_interpreter_and_bridge_path_only','doctor_required':True}))
if __name__=='__main__':
    try:install()
    except Exception as error:
        print(json.dumps({'root_setup_complete':False,'reason':str(error) if isinstance(error,RuntimeError) else type(error).__name__,
                          'automatic_retry':False}))
        raise SystemExit(1)
'''


class ManagementError(RuntimeError):
    pass


WINDOWS_PRIVATE_ACL = r'''
$ErrorActionPreference='Stop'
$item=Get-Item -LiteralPath $env:LAS_PRIVATE_KEY_ITEM
if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'reparse_refused'}
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
$acl=Get-Acl -LiteralPath $item.FullName
if($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid.Value){throw 'owner_mismatch'}
if($env:LAS_PRIVATE_KEY_DIRECTORY -eq '1'){
 $inherit=[Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit
}else{
 $inherit=[Security.AccessControl.InheritanceFlags]::None
}
# An OpenSSH-created key can grant its owner Read but not WRITE_OWNER.
# Preserve the already-verified owner and change only the Access section.
$acl.SetAccessRuleProtection($true,$false)
foreach($old in @($acl.GetAccessRules($true,$false,[Security.Principal.SecurityIdentifier]))){$acl.RemoveAccessRuleAll($old)}
$rule=[Security.AccessControl.FileSystemAccessRule]::new($sid,[Security.AccessControl.FileSystemRights]::FullControl,$inherit,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
$acl.AddAccessRule($rule)
# Persist only changed access rules. PowerShell Set-Acl can request SACL writes
# and SeSecurityPrivilege even though we did not change auditing or ownership.
if($item.PSObject.Methods['SetAccessControl']){$item.SetAccessControl($acl)}
else{[System.IO.FileSystemAclExtensions]::SetAccessControl($item,$acl)}
$verify=Get-Acl -LiteralPath $item.FullName
$owner=$verify.GetOwner([Security.Principal.SecurityIdentifier]).Value
$rules=@($verify.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
if($owner -ne $sid.Value -or -not $verify.AreAccessRulesProtected -or $rules.Count -ne 1){throw 'acl_not_owner_only'}
if($rules[0].IdentityReference.Value -ne $sid.Value -or $rules[0].AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or $rules[0].IsInherited){throw 'acl_rule_invalid'}
Write-Output 'PRIVATE_ACL_VERIFIED'
'''

WINDOWS_VERIFY_ACL = r'''
$ErrorActionPreference='Stop'
$item=Get-Item -LiteralPath $env:LAS_PRIVATE_KEY_ITEM
if(($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'reparse_refused'}
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
$verify=Get-Acl -LiteralPath $item.FullName
$owner=$verify.GetOwner([Security.Principal.SecurityIdentifier]).Value
$rules=@($verify.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
if($owner -ne $sid.Value -or -not $verify.AreAccessRulesProtected -or $rules.Count -ne 1){throw 'acl_not_owner_only'}
if($rules[0].IdentityReference.Value -ne $sid.Value -or $rules[0].AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or $rules[0].IsInherited){throw 'acl_rule_invalid'}
Write-Output 'PRIVATE_ACL_VERIFIED'
'''


def windows_acl(path, directory, script):
        environment = dict(os.environ, LAS_PRIVATE_KEY_ITEM=str(path.absolute()),
                           LAS_PRIVATE_KEY_DIRECTORY='1' if directory else '0')
        # Do not pass a PowerShell 7 module path into Windows PowerShell 5 (or
        # vice versa); that can prevent the Security module from loading.
        environment.pop('PSModulePath', None)
        shell = shutil.which('pwsh') or shutil.which('powershell')
        if not shell:
            raise ManagementError('private_acl_not_verified_no_key_generation')
        try:
            result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command', script],
                                    env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ManagementError('private_acl_not_verified_no_key_generation') from exc
        if result.returncode or result.stdout.strip() != b'PRIVATE_ACL_VERIFIED':
            raise ManagementError('private_acl_not_verified')


def protect_private_path(path, directory):
    path = Path(path)
    if path.is_symlink():
        raise ManagementError('private_key_symlink_refused')
    info = path.stat()
    if not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise ManagementError('private_key_hardlink_or_nonregular_file_refused')
    if directory and not stat.S_ISDIR(info.st_mode):
        raise ManagementError('private_key_directory_required')
    if os.name == 'nt':
        windows_acl(path, directory, WINDOWS_PRIVATE_ACL)
    else:
        if path.stat().st_uid != os.getuid():
            raise ManagementError('private_key_ownership_not_verified')
        os.chmod(path, 0o700 if directory else 0o600)
        info = path.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
            raise ManagementError('private_key_ownership_not_verified')


def verify_private_path(path, directory):
    path = Path(path)
    if path.is_symlink():
        raise ManagementError('private_key_symlink_refused')
    if os.name == 'nt':
        windows_acl(path, directory, WINDOWS_VERIFY_ACL)
    else:
        info = path.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
            raise ManagementError('private_key_acl_not_verified')
    if not directory and path.stat().st_nlink != 1:
        raise ManagementError('private_key_hardlink_refused')


def generate_key(output, execute=subprocess.run, which=shutil.which, protect=protect_private_path):
    """Generate only a dedicated local key, after owner-only storage is verified."""
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ManagementError('new_private_key_directory_required')
    keygen = which('ssh-keygen')
    if not keygen:
        raise ManagementError('existing_ssh_keygen_required')
    output.mkdir(mode=0o700)
    try:
        protect(output, True)  # Failure here must precede creation of any secret.
    except Exception as exc:
        raise ManagementError('private_directory_acl_not_verified_key_not_generated') from exc
    private = output / 'id_ed25519'
    public = output / 'id_ed25519.pub'
    try:
        result = execute([keygen, '-q', '-t', 'ed25519', '-f', str(private), '-N', '',
                          '-C', 'LINE-Auto-Sync dedicated management'],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ManagementError('key_generation_uncertain_private_directory_retained') from exc
    if result.returncode or not private.is_file() or not public.is_file():
        raise ManagementError('key_generation_failed_private_directory_retained')
    try:
        protect(private, False)
        protect(public, False)
    except Exception as exc:
        raise ManagementError('generated_key_candidate_retained_file_acl_not_verified') from exc
    return {'private_key_file': str(private.resolve()), 'public_key_file': str(public.resolve()),
            'key_registered_on_ipad': False, 'host_key_verified': False, 'authentication_ready': False}


def verify_local_key(output, execute=subprocess.run, which=shutil.which, verify=verify_private_path):
    """Read-only reconciliation of an existing dedicated candidate, never regeneration."""
    output = Path(output)
    private, public = output / 'id_ed25519', output / 'id_ed25519.pub'
    state = {'candidate_files_exist': private.is_file() and public.is_file(), 'acl_owner_only_verified': False,
             'pair_verified': False, 'key_registered_on_ipad': False,
             'host_key_verified': False, 'authentication_ready': False}
    if not state['candidate_files_exist'] or output.is_symlink():
        return state
    try:
        for path, directory in ((output, True), (private, False), (public, False)):
            verify(path, directory)
    except Exception:
        state['reason'] = 'existing_candidate_acl_not_verified_no_regeneration'
        return state
    state['acl_owner_only_verified'] = True
    keygen = which('ssh-keygen')
    if not keygen:
        state['reason'] = 'existing_ssh_keygen_required'
        return state
    try:
        result = execute([keygen, '-y', '-P', '', '-f', str(private)], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, timeout=10)
        lines = [line.split() for line in public.read_bytes().splitlines() if line.strip()]
        derived = result.stdout.split()
        state['pair_verified'] = bool(result.returncode == 0 and len(result.stdout) < 4096
            and len(lines) == 1 and len(lines[0]) >= 2 and len(derived) >= 2
            and lines[0][0] == b'ssh-ed25519' and lines[0][:2] == derived[:2])
    except (OSError, subprocess.TimeoutExpired):
        state['reason'] = 'key_pair_check_unavailable_candidate_retained'
    return state


def protect_existing_key(output, protect=protect_private_path):
    """Restrict ACLs of existing candidate files only; never replace key bytes."""
    output = Path(output)
    paths = ((output, True), (output / 'id_ed25519', False), (output / 'id_ed25519.pub', False))
    if not output.is_dir() or any(path.is_symlink() or (not directory and not path.is_file()) for path, directory in paths):
        raise ManagementError('existing_complete_candidate_required_no_generation')
    if any(path.stat().st_nlink != 1 for path, directory in paths if not directory):
        raise ManagementError('private_key_hardlink_refused_before_acl_change')
    for path, directory in paths:
        protect(path, directory)
    return {'existing_key_acl_restricted': True, 'key_regenerated': False, 'verify_local_key_required': True}


def root_setup_source():
    raw = ROOT_ACTION.encode('utf-8')
    source = 'BRIDGE_BASE64 = ' + repr(base64.b64encode(raw).decode()) + '\n'
    source += 'BRIDGE_SHA256 = ' + repr(hashlib.sha256(raw).hexdigest()) + '\n' + ROOT_SETUP
    compile(source, '<one-time-root-setup>', 'exec')
    return source


def export_root_setup(output):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ManagementError('new_private_setup_file_required')
    raw = root_setup_source().encode('utf-8')
    with output.open('xb') as stream:
        stream.write(raw)
    return {'root_setup_exported': True, 'code_sha256': hashlib.sha256(raw).hexdigest(),
            'applied': False, 'owner_sudo_authentication_required': True}


def load_config(path):
    cfg = json.loads(Path(path).read_text(encoding='utf-8'))
    destination = cfg.get('destination', '')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@:\[\]-]{0,200}', destination):
        raise ManagementError('invalid_private_destination')
    port = cfg.get('port', 22)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ManagementError('invalid_port')
    known = Path(cfg.get('known_hosts', '')).expanduser()
    if not known.is_file():
        raise ManagementError('existing_trusted_known_hosts_required')
    cfg['known_hosts'] = str(known.resolve())
    if cfg.get('identity_file') and not Path(cfg['identity_file']).expanduser().is_file():
        raise ManagementError('existing_identity_file_required')
    return cfg


def ssh_command(cfg):
    command = ['ssh', '-T', '-p', str(cfg.get('port', 22)), '-o', 'BatchMode=yes',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectionAttempts=1',
               '-o', 'ConnectTimeout=5', '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=1',
               '-o', 'UserKnownHostsFile=' + cfg['known_hosts']]
    if cfg.get('identity_file'):
        command += ['-o', 'IdentitiesOnly=yes', '-i', str(Path(cfg['identity_file']).expanduser())]
    return command + ['--', cfg['destination'], '/var/jb/usr/bin/python3 -c ' + shlex.quote(REMOTE)]


def remote(cfg, request, execute=subprocess.run):
    try:
        result = execute(ssh_command(cfg), input=json.dumps(request).encode(),
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=95)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise ManagementError('connection_or_remote_outcome_unknown_reconcile_only') from exc
    if result.returncode:
        raise ManagementError('connection_or_remote_action_failed_reconcile_only')
    try:
        return json.loads(result.stdout)
    except (ValueError, UnicodeError) as exc:
        raise ManagementError('unexpected_remote_response_reconcile_only') from exc


def save_journal(path, value):
    path = Path(path)
    if path.is_symlink():
        raise ManagementError('unsafe_private_journal')
    temporary = path.with_name(path.name + '.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def manifest(directory):
    directory = Path(directory).resolve(strict=True)
    files = {}
    for folder in ('tools', 'runtime'):
        if (directory / folder).is_symlink():
            raise ManagementError('deployment_symlink_refused')
        for path in sorted((directory / folder).glob('*.py')):
            if path.is_symlink():
                raise ManagementError('deployment_symlink_refused')
            raw = path.read_bytes()
            files[folder + '/' + path.name] = {'data': base64.b64encode(raw).decode(),
                                               'sha256': hashlib.sha256(raw).hexdigest()}
    if 'tools/keep_lock_delivery.py' not in files or not any(k.startswith('runtime/') for k in files):
        raise ManagementError('trial_helper_and_runtime_required')
    return files


def package(repository, output):
    """Create a fresh private staging kit from reviewed repository sources."""
    repository = Path(repository).resolve(strict=True)
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ManagementError('new_private_package_directory_required')
    sources = {'tools/' + name: repository / 'tools' / name for name in
               ('keep_lock_delivery.py', 'observe_line_db.py', 'keep_power_lock.py')}
    sources.update({'runtime/' + path.name: path for path in (repository / 'normal-only-rc1').glob('*.py')})
    if len(sources) <= 3 or any(not p.is_file() or p.is_symlink() for p in sources.values()):
        raise ManagementError('reviewed_helper_and_runtime_sources_required')
    raw = {name: path.read_bytes() for name, path in sources.items()}
    raw['tools/root_bootstrap.py'] = root_setup_source().encode('utf-8')
    output.mkdir(mode=0o700)
    for name, data in raw.items():
        target = output / name
        target.parent.mkdir(mode=0o700, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(data)
    manifest(output)
    return {'packaged_files': len(raw), 'started': False, 'private': True}


def control(cfg, action, run, journal, base='/var/mobile/las-lock-n1', bundle=None,
            baseline='/var/mobile/las-keep-lock-power-r3', output=None, transport=remote):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}', run):
        raise ManagementError('invalid_run')
    if not re.fullmatch(r'/var/mobile/las-lock-[a-z0-9-]{1,40}', base):
        raise ManagementError('invalid_trial_base')
    journal = Path(journal)
    if journal.is_symlink():
        raise ManagementError('unsafe_private_journal')
    binding = hashlib.sha256(json.dumps({'destination': cfg['destination'], 'port': cfg.get('port', 22),
                                         'run': run, 'base': base}, sort_keys=True).encode()).hexdigest()
    state = json.loads(journal.read_text()) if journal.exists() else {'binding': binding, 'events': []}
    if state.get('binding') != binding:
        raise ManagementError('journal_target_mismatch')
    request = {'action': action, 'run': run, 'base': base}
    if action == 'deploy':
        request['files'] = manifest(bundle)
    if action == 'prepare':
        request['baseline'] = baseline
    if action in ('start', 'post-unlock-once'):
        key = 'start_attempted' if action == 'start' else 'post_unlock_read_attempted'
        if state.get(key):
            raise ManagementError('start_already_attempted_use_status_not_start' if action == 'start'
                                  else 'post_unlock_read_already_attempted_review_existing_evidence')
        state[key] = True
        state['events'].append({'action': action, 'outcome': 'requested_remote_outcome_not_yet_known'})
        save_journal(journal, state)  # Durable BEFORE SSH; a lost response never causes an automatic restart.
    try:
        result = transport(cfg, request)
    except Exception:
        state['events'].append({'action': action, 'outcome': 'unknown_or_failed_reconcile_only'})
        save_journal(journal, state)
        raise
    if action == 'fetch':
        destination = Path(output)
        destination.mkdir(mode=0o700)
        for name, item in result.get('files', {}).items():
            if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name in ('.', '..'):
                raise ManagementError('invalid_download_name')
            raw = base64.b64decode(item['data'], validate=True)
            if hashlib.sha256(raw).hexdigest() != item['sha256']:
                raise ManagementError('download_checksum_mismatch')
            with (destination / name).open('xb') as stream:
                stream.write(raw)
        result = {'private_logs_saved': True, 'files': len(result.get('files', {}))}
    state['events'].append({'action': action, 'outcome': 'response_received', 'result': result})
    save_journal(journal, state)
    # Native helper output may contain target IDs: keep it in the private journal, not the console.
    visible = {k: v for k, v in result.items() if not k.endswith('_private')}
    return visible


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['package', 'generate-key', 'verify-local-key', 'protect-existing-key',
                                         'export-root-setup', 'doctor', 'deploy', 'prepare', 'ui-status', 'launch-line', 'start', 'status',
                                         'post-unlock-once', 'fetch', 'stop'])
    parser.add_argument('--config', type=Path)
    parser.add_argument('--journal', type=Path)
    parser.add_argument('--run')
    parser.add_argument('--repository', type=Path)
    parser.add_argument('--base', default='/var/mobile/las-lock-n1')
    parser.add_argument('--baseline', default='/var/mobile/las-keep-lock-power-r3')
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.action in ('verify-local-key', 'protect-existing-key'):
        if args.output is None:
            parser.error('--output required')
        print(json.dumps(verify_local_key(args.output) if args.action == 'verify-local-key' else protect_existing_key(args.output)))
        return
    if args.action == 'generate-key':
        if args.output is None:
            parser.error('--output required')
        print(json.dumps(generate_key(args.output)))
        return
    if args.action == 'export-root-setup':
        if args.output is None:
            parser.error('--output required')
        print(json.dumps(export_root_setup(args.output)))
        return
    if args.action == 'package':
        if args.repository is None or args.output is None:
            parser.error('--repository and --output required')
        print(json.dumps(package(args.repository, args.output)))
        return
    if args.config is None or args.journal is None or args.run is None:
        parser.error('--config, --journal and --run required')
    if args.action == 'deploy' and args.bundle is None:
        parser.error('--bundle required')
    if args.action == 'fetch' and args.output is None:
        parser.error('--output required')
    result = control(load_config(args.config), args.action, args.run, args.journal, args.base,
                     args.bundle, args.baseline, args.output)
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'error': str(error) if isinstance(error, ManagementError) else type(error).__name__,
                          'automatic_restart': False}), file=sys.stderr)
        raise SystemExit(1)
