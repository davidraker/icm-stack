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
