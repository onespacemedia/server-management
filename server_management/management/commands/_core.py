from __future__ import print_function

import json
import sys

import fabric
from django.conf import settings
from django.core.management.base import BaseCommand
from fabric.api import settings as fabric_settings
from fabric.api import fastprint, hide, prompt, run, sudo
from fabric.colors import green, red, yellow
from fabric.contrib.console import confirm


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

def load_config(env, remote=None, config_user='deploy', debug=False):  # pylint: disable=too-complex,too-many-branches,too-many-statements
    env['sudo_prefix'] += '-H '

    remote_prompt, config = get_remote(remote)

    remote = config['remotes'][remote_prompt]
    env.host_string = remote['server']['ip']
    config['remote_name'] = remote_prompt

    env.user = config_user
    env.disable_known_hosts = True
    env.reject_unknown_hosts = False

    # If is_aws is explicitly declared, trust it.
    if 'is_aws' in remote:
        aws_check = remote['is_aws']
    # Try to guess it from the hostname.
    elif 'amazonaws.com' in env.host_string:
        aws_check = True
    # Dunno, ask the user.
    else:
        aws_check = confirm('Is this host on AWS?', default=False)

    if aws_check:
        if 'initial_user' in remote['server']:
            env.user = remote['server']['initial_user']
        else:
            env.user = 'ubuntu'

        if 'identity_file' in remote['server']:
            if remote['server']['identity_file']:
                env.key_filename = remote['server']['identity_file']
        else:
            key = prompt('Please enter the path to the AWS key pair: ')
            if key:
                env.key_filename = key
    else:
        if sys.argv[1] == 'deploy' and 'initial_user' in remote['server']:
            env.user = remote['server']['initial_user']
        elif 'deploy_user' in remote['server']:
            env.user = remote['server']['deploy_user']

    # Make sure we can connect to the server
    with hide('output', 'running', 'warnings'):
        with fabric_settings(warn_only=True):
            if not run('whoami'):
                print('Failed to connect to remote server')
                exit()

    if not debug:
        # Change the output to be less verbose.
        fabric.state.output['stdout'] = False
        fabric.state.output['running'] = False

    # Return the server config
    return config, remote


def get_remote(remote):
    # Load the json file
    try:
        with open(f'{settings.SITE_ROOT}/server.json', 'r', encoding='utf-8') as json_data:
            config = json.load(json_data)
    except Exception as e:
        print(e)
        raise Exception('Something is wrong with the server.json file, make sure it exists and is valid JSON.')

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

    return remote_prompt, config


def title_print(title, state=''):
    if state == 'task':
        fastprint('[{}] {} ... '.format(
            yellow('TASK'),
            title,
        ))
    elif state == 'succeeded':
        fastprint('\r[{}] {} ... done'.format(
            green('TASK'),
            title,
        ), end='\n')
    elif state == 'failed':
        fastprint('\r[{}] {} ... failed'.format(
            red('TASK'),
            title,
        ), end='\n')

        exit()


def check_request(task, result):
    if result.succeeded:
        title_print(task['title'], state='succeeded')
    elif result.failed:
        title_print(task['title'], state='failed')


def run_tasks(env, tasks, user=None):
    # Loop tasks
    for task in tasks:
        title_print(task['title'], state='task')

        # Generic command
        if 'command' in task:
            if user:
                task_result = sudo(task['command'], user=user)
            else:
                task_result = run(task['command'])
        # Fabric API
        elif 'fabric_command' in task:
            task_result = getattr(fabric.api, task['fabric_command'])(*task.get('fabric_args', []), **task.get('fabric_kwargs', {}))

        # Check result
        check_request(task, task_result)
