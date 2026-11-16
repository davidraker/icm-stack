import pytest

from icmstack.profile import Profile
from icmstack.prompts import Answers


@pytest.fixture
def profile(tmp_path):
    return Profile('t', root=tmp_path)


def scripted(replies):
    """An input function that returns the scripted replies in order and records the prompts it saw."""
    replies, seen = list(replies), []

    def fn(prompt):
        seen.append(prompt)
        if not replies:
            raise AssertionError(f'unexpected prompt {prompt!r}')
        return replies.pop(0)
    fn.seen = seen
    return fn


@pytest.fixture
def answers_factory(profile):
    def make(replies=(), cli=None, env=None, yes=False):
        fn = scripted(replies)
        a = Answers(profile, cli=cli or {}, env=env or {}, yes=yes, input_fn=fn, out=lambda s: None, interactive=True)
        a.input = fn
        return a
    return make


FAKE_DOCKER = r'''#!/bin/sh
# The test suite's docker: records every call and owns nothing. "info" fails (docker is not available to the tests),
# "ps" lists nothing, "inspect" finds nothing, and the mutating commands succeed without doing anything.
echo "$@" >> "$FAKE_DOCKER_LOG"
case "$1" in
  info|inspect|image|volume) exit 1 ;;
  ps) exit 0 ;;
  run) echo 0123456789ab ;;
esac
exit 0
'''


@pytest.fixture(autouse=True)
def fake_docker(tmp_path_factory, monkeypatch):
    """No test may reach the real docker daemon. Container names carry only the profile name, and the tests use the
    profile "default" under a temporary root, so a `down` in a test once removed the real default stack's broker and
    device containers. A recording `docker` goes first on PATH for every test; `fake_docker.log` lists the calls."""
    bin_dir = tmp_path_factory.mktemp('fakedocker')
    script = bin_dir / 'docker'
    script.write_text(FAKE_DOCKER)
    script.chmod(0o755)
    log = bin_dir / 'calls.log'
    log.touch()
    monkeypatch.setenv('FAKE_DOCKER_LOG', str(log))
    monkeypatch.setenv('PATH', f'{bin_dir}:{__import__("os").environ.get("PATH", "")}')
    return log
