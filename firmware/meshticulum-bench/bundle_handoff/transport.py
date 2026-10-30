"""Pinned SSH stdin transport only; no native runtime or radio authority."""
import json,os,re,select,shlex,signal,subprocess,time

PROMPT=b'MTQ_SUDO> '

def rpc(row,source,request,*,password=None,seconds=60,event=None,control=None):
    assert row['ssh'][0]=='ssh' and 'StrictHostKeyChecking=yes' in row['ssh'] and 'BatchMode=yes' in row['ssh']
    argv=[*row['ssh'][:-1],'-o','ControlMaster=no','-o','ControlPath=none',
        '-o','ClearAllForwardings=yes','-o','ForwardAgent=no','-o','PermitLocalCommand=no',row['ssh'][-1]]
    command=['/usr/bin/timeout','--signal=TERM','--kill-after=3s',str(seconds-5)+'s',row['python'],'-I','-B','-u','-c',source]
    if row['sudo']:command=['sudo','-S','-p',PROMPT.decode(),'--',*command]
    argv.append(shlex.join(command));p=None;raw=bytearray();buffer=bytearray();value=None;ready=False;sent=False
    payload=json.dumps(request,separators=(',',':')).encode()+b'\n';pending=bytearray();deadline=time.monotonic()+seconds
    credential=password
    try:
        p=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True,bufsize=0)
        os.set_blocking(p.stdin.fileno(),False);os.set_blocking(p.stdout.fileno(),False)
        while p.poll() is None or select.select([p.stdout],[],[],0)[0]:
            assert time.monotonic()<deadline,'local_management_deadline'
            if control is not None and ready:
                for command in control():
                    encoded=json.dumps(command,separators=(',',':')).encode()+b'\n'
                    assert len(encoded)<4096 and len(pending)+len(encoded)<16384,'control_output_bound'
                    pending.extend(encoded)
            reading,writing,_=select.select([p.stdout],[p.stdin] if pending else [],[],.02)
            if reading:
                part=os.read(p.stdout.fileno(),65536)
                if part:raw.extend(part);buffer.extend(part)
                if not part and p.poll() is not None:break
                assert len(raw)<=4000000,'management_output_bound'
                if PROMPT in buffer:
                    assert password is not None and not ready,'unexpected_sudo_prompt'
                    buffer[:]=buffer.replace(PROMPT,b'',1);pending.extend(password.encode()+b'\n');password=None
                while b'\n' in buffer:
                    line,_,tail=buffer.partition(b'\n');buffer[:]=tail
                    if line==b'MTQ_READY':
                        assert not sent;pending.extend(payload);sent=True;ready=True
                    elif line.startswith(b'MTQ_RESULT '):value=json.loads(line[11:])
                    elif line.startswith(b'MTQ_EVENT ') and event is not None:event(json.loads(line[10:]))
            if writing and pending:
                count=os.write(p.stdin.fileno(),pending);del pending[:count]
        code=p.wait(timeout=2)
        if code!=0 or type(value) is not dict:
            # Keep only a bounded exception summary, never the source/input or
            # complete output. SSH stdin carries secret files in later stages.
            candidates=[line[:240] for line in bytes(raw).decode('utf-8','replace').splitlines()
                if re.fullmatch(r'[A-Za-z_.]*(?:Error|Exception)(?:Group)?(?:: [^\x00-\x1f]{0,1000})?',line)]
            diagnostic=candidates[-1] if candidates else 'no_structured_remote_result'
            if credential and credential in diagnostic:diagnostic='credential_redacted'
            raise RuntimeError('remote_management_failed rc='+str(code)+' '+diagnostic)
        return value
    finally:
        password=credential=None;payload=None;pending.clear();raw.clear();buffer.clear()
        if p is not None:
            if p.poll() is None:
                os.killpg(p.pid,signal.SIGTERM)
                try:p.wait(timeout=2)
                except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait(timeout=2)
            for f in (p.stdin,p.stdout):
                if f is not None:f.close()

