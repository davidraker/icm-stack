import sys
import time

from icmstack.processes import ManagedProcess, pid_alive
from icmstack.profile import Profile, dump_toml


def test_profile_roundtrip(tmp_path):
    p = Profile('one', root=tmp_path)
    p.answers.update({'instance_name': 'site1', 'ui_port': 8600, 'register': False, 'agents': ['driver', 'interop'], 'path': 'a "quoted" path'})
    p.mark_installed('broker'); p.mark_started('broker')
    q = Profile('one', root=tmp_path)
    assert q.answers == p.answers and q.is_installed('broker') and q.state['started'] == ['broker']
    assert oct(q.answers_file.stat().st_mode & 0o777) == '0o600'
    assert Profile.existing(tmp_path) == ['one']
    assert 'register = false' in dump_toml({'register': False, 'skip': None})


def test_managed_process_start_status_stop(tmp_path):
    profile = Profile('p', root=tmp_path)
    proc = ManagedProcess('sleeper', profile)
    assert not proc.running()
    pid = proc.start([sys.executable, '-c', 'import time, sys; print("up", flush=True); time.sleep(60)'])
    assert proc.running() and proc.pid() == pid
    time.sleep(0.5)
    assert 'up' in proc.tail()
    assert proc.stop(timeout=3) and not proc.running() and not pid_alive(pid)
    assert not proc.pidfile.exists()
    proc.pidfile.write_text('999999')                 # a stale pidfile reads as not running and is removed
    assert not proc.running() and not proc.pidfile.exists()


def test_follow_logs_prints_only_new_lines(tmp_path):
    """Following reads the component's logs() again and again and prints what was not in the last reading."""
    import threading
    from icmstack.processes import follow_logs
    log = tmp_path / 'x.log'; log.write_text('a\nb\n')

    class Comp:
        def logs(self, ctx, lines=40):
            return ''.join(log.read_text().splitlines(keepends=True)[-lines:])
    out, ticks = [], {'n': 0}
    def stop():
        ticks['n'] += 1
        if ticks['n'] == 2: log.write_text('a\nb\nc\nd\n')
        if ticks['n'] == 3: log.write_text('a\nb\nc\nd\ne\n')
        return ticks['n'] > 4
    follow_logs(Comp(), None, out.append, previous='a\nb\n', stop=stop, interval=0.01)
    assert out == ['c', 'd', 'e']
