import pytest

from icmstack.prompts import Answers, PromptError, Question, add_question_options, cli_answers

Q = Question('instance_name', 'Instance name', default='site1')


def test_default_shown_then_recorded_answer_shown(answers_factory, profile):
    a = answers_factory(['demo1'])
    assert a.ask(Q) == 'demo1' and a.input.seen == ['Instance name [site1]: ']
    assert profile.answers['instance_name'] == 'demo1'
    b = answers_factory([''])                       # second run: the recorded answer is offered and Enter keeps it
    assert b.ask(Q) == 'demo1' and b.input.seen == ['Instance name [demo1]: ']


def test_cli_then_env_settle_without_asking(answers_factory, profile):
    profile.answers['instance_name'] = 'recorded'
    assert answers_factory(cli={'instance_name': 'fromcli'}).ask(Q) == 'fromcli'
    assert answers_factory(env={'ICMSTACK_INSTANCE_NAME': 'fromenv'}).ask(Q) == 'fromenv'
    a = answers_factory(cli={'instance_name': 'fromcli'}, env={'ICMSTACK_INSTANCE_NAME': 'fromenv'})
    assert a.ask(Q) == 'fromcli' and a.input.seen == []
    assert profile.answers['instance_name'] == 'fromcli'     # settled answers are recorded too


def test_yes_takes_recorded_or_default(answers_factory, profile):
    assert answers_factory(yes=True).ask(Q) == 'site1'
    profile.answers['instance_name'] = 'kept'
    assert answers_factory(yes=True).ask(Q) == 'kept'


def test_kinds_and_validation(answers_factory):
    port = Question('ui_port', 'UI port', default=8600, kind='int', validate=lambda v: None if 1 <= v <= 65535 else 'out of range')
    a = answers_factory(['abc', '70000', '8601'])
    assert a.ask(port) == 8601 and len(a.input.seen) == 3
    flag = Question('register', 'Register', default=True, kind='bool')
    assert answers_factory(['']).ask(flag) is True            # Enter keeps the default
    assert answers_factory(['n']).ask(flag) is False and answers_factory(['']).ask(flag) is False    # then the recorded answer is offered
    choice = Question('backend', 'Backend', default='modular', kind='choice', choices=('modular', 'fastlib'))
    a = answers_factory(['other', 'fastlib'])
    assert a.ask(choice) == 'fastlib'
    assert answers_factory(cli={'ui_port': '9000'}).ask(port) == 9000


def test_secret_never_recorded_and_not_a_plain_option(profile):
    pw = Question('web_password', 'Web admin password', kind='secret', secret=True)
    got = iter(['s3cret', 'nope', 's3cret', 's3cret'])
    a = Answers(profile, getpass_fn=lambda p: next(got), out=lambda s: None, interactive=True)
    assert a.ask(pw) == 's3cret' and 'web_password' not in profile.answers
    shown = []
    generated = Answers(profile, yes=True, interactive=True, out=shown.append).ask(pw)          # --yes: generated and shown once
    assert len(generated) >= 12 and generated in shown[0] and 'web_password' not in profile.answers
    assert Answers(profile, env={'ICMSTACK_WEB_PASSWORD': 'fromenv'}, interactive=True).ask(pw) == 'fromenv'
    import argparse
    p = argparse.ArgumentParser(allow_abbrev=False); add_question_options(p, [pw, Q])
    ns = p.parse_args(['--instance-name', 'x'])
    assert cli_answers(ns, [pw, Q]) == {'instance_name': 'x'}
    with pytest.raises(SystemExit):
        p.parse_args(['--web-password', 'x'])


def test_no_terminal_without_yes_is_an_error(profile):
    a = Answers(profile, interactive=False, out=lambda s: None)
    with pytest.raises(PromptError):
        a.ask(Q)
    assert Answers(profile, interactive=False, yes=True).ask(Q) == 'site1'


def test_a_question_is_asked_once_per_run(answers_factory, profile):
    a = answers_factory(['demo1'])
    assert a.ask(Q) == 'demo1' and a.ask(Q) == 'demo1' and len(a.input.seen) == 1
    pw = Question('web_password', 'Web admin password', kind='secret', secret=True)
    got = iter(['s3cret', 's3cret'])
    b = Answers(profile, getpass_fn=lambda p: next(got), out=lambda s: None, interactive=True)
    assert b.ask(pw) == 's3cret' and b.ask(pw) == 's3cret' and 'web_password' not in profile.answers
