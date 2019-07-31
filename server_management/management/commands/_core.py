from __future__ import print_function

import json
import os
import sys

from django.conf import settings
from django.core.management.base import BaseCommand
from fabric import Connection


class ServerManagementBaseCommand(BaseCommand):  # pylint: disable=abstract-method

    def add_arguments(self, parser):
        super(ServerManagementBaseCommand, self).add_arguments(parser)

        parser.add_argument(
            '--remote',
            dest='remote',
            default=None,
            help='remote host'
        )

        parser.add_argument(
            '--debug',
            dest='debug',
            action='store_true',
            default=False,
        )

        parser.add_argument(
            '--noinput',
            dest='noinput',
            action='store_true',
            default=False,
        )


# The complexity in this method comes from the number of different server
# configurations we want to allow for. The AWS flow seems to be the most complex
# of it all, it could potentially be moved into it's own method and called from
# this one.

def load_config(remote=None, config_user='deploy', debug=False):
    # Load the json file
    try:
        with open(os.path.join(settings.SITE_ROOT, 'server.json'), 'r', encoding='utf-8') as json_data:
            config = json.load(json_data)
    except Exception as e:
        print(e)
        raise Exception('Something is wrong with the server.json file, make sure it exists and is valid JSON.')

    remote_prompt = get_remote(remote, config)
    config['remote_name'] = remote_prompt

    host_string = config['remotes'][remote_prompt]['server']['ip']
    remote_user = config_user
    connect_kwargs = {}

    remote_user, key_filename = aws_get_info(config['remotes'][remote_prompt], remote_user)
    if key_filename:
        connect_kwargs['key_filename'] = key_filename

    connection = Connection(
        host=host_string,
        user=remote_user,
        connect_kwargs=connect_kwargs
    )

    # Make sure we can connect to the server
    trial_run = connection.run('whoami', hide=True)
    if trial_run.failed:
        print('Failed to connect to remote server')
        exit()

    # Return the server config, the chosen remote and the connection object.
    return config, connection


def aws_get_info(remote, remote_user=None):
    key_filename = None

    # If is_aws is explicitly declared, trust it.
    if 'is_aws' in remote:
        aws_check = remote['is_aws']
    # Try to guess it from the hostname.
    elif 'amazonaws.com' in remote['server']['ip']:
        aws_check = True
    # Dunno, ask the user.
    else:
        aws_check = confirm('Is this host on AWS?', default=False)

    if aws_check:
        if 'initial_user' in remote['server']:
            remote_user = remote['server']['initial_user']
        else:
            remote_user = 'ubuntu'

        if 'identity_file' in remote['server']:
            if remote['server']['identity_file']:
                key_filename = remote['server']['identity_file']
        else:
            key = prompt('Please enter the path to the AWS key pair: ')
            if key:
                key_filename = key
    else:
        if sys.argv[1] == 'deploy' and 'initial_user' in remote['server']:
            remote_user = remote['server']['initial_user']
        elif 'deploy_user' in remote['server']:
            remote_user = remote['server']['deploy_user']

    return remote_user, key_filename


def get_remote(remote, config):
    # Define current host from settings in server config
    # First check if there is a single remote or multiple.
    if 'remotes' not in config or not config['remotes']:
        raise Exception('No remotes specified in config.')

    # Prompt for a host selection.
    remote_keys = list(config['remotes'].keys())
    if len(remote_keys) == 1:
        remote_prompt = remote_keys[0]
    elif remote:
        remote_prompt = remote

        if remote_prompt not in remote_keys:
            raise Exception('Invalid remote name `{}`.'.format(remote))
    else:
        print('Available hosts: {}'.format(
            ', '.join(config['remotes'].keys())
        ))

        remote_prompt = prompt('Please enter a remote: ', default=remote_keys[0], validate=lambda x: remote_keys[remote_keys.index(x)])

    return remote_prompt


def prompt(text, default=None, validate=None):
    if default:
        text = text.strip()
        if text[-1] == ':' and len(text) > 1:
            text = text[0:-1]
        text = f'{text} [{default}]: '

    while 1:
        user_input = input(text)

        if not user_input and default is not None:
            return default

        if user_input and (validate is None or validate(user_input)):
            return user_input


def confirm(text, default=None):
    while 1:
        user_input = input(text)

        if user_input.lower() in ['y', 'yes']:
            return True

        if user_input.lower() in ['n', 'no']:
            return False

        if not user_input and default is not None:
            return default


def title_print(connection, title, state=''):
    def _wrap_with(code):
        def inner(text, bold=False):
            c = code
            if bold:
                c = "1;%s" % c
            return "\033[%sm%s\033[0m" % (c, text)
        return inner

    red = _wrap_with('31')
    green = _wrap_with('32')
    yellow = _wrap_with('33')

    if state == 'task':
        connection.fastprint('[{}] {} ... '.format(
            yellow('TASK'),
            title,
        ))
    elif state == 'succeeded':
        connection.fastprint('\r[{}] {} ... done'.format(
            green('TASK'),
            title,
        ), end='\n')
    elif state == 'failed':
        connection.fastprint('\r[{}] {} ... failed'.format(
            red('TASK'),
            title,
        ), end='\n')

        exit()


def check_request(task, result):
    if result.succeeded:
        title_print(task['title'], state='succeeded')
    elif result.failed:
        title_print(task['title'], state='failed')


def run_tasks(connection, tasks, user=None):
    # Loop tasks
    for task in tasks:
        title_print(task['title'], state='task')

        # Generic command
        if 'command' in task:
            if user:
                task_result = connection.sudo(task['command'], user=user)
            else:
                task_result = connection.run(task['command'])
        # Fabric API
        elif 'fabric_command' in task:
            task_result = getattr(connection, task['fabric_command'])(*task.get('fabric_args', []), **task.get('fabric_kwargs', {}))

        # Check result
        check_request(task, task_result)
