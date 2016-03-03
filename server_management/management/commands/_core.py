from optparse import make_option

from django.conf import settings
from django.core.management.base import BaseCommand
from fabric.api import hide, prompt, run, settings as fabric_settings
from fabric.contrib.console import confirm
import json
import ansible.runner
import ansible.inventory
import sys


class ServerManagementBaseCommand(BaseCommand):
    option_list = BaseCommand.option_list + (
        make_option(
            '--remote',
            dest='remote',
            default=None,
            help='remote host'
        ),
    )


def load_config(env, remote=None, config_user='deploy'):
    env['sudo_prefix'] += '-H '

    # Load the json file
    try:
        json_data = open("{}/server.json".format(
            settings.SITE_ROOT
        ))
        config = json.load(json_data)
    except:
        raise Exception(
            "Something is wrong with the server.json file, make sure it exists and is valid JSON.")

    # Define current host from settings in server config
    # First check if there is a single remote or multiple.
    if 'remote' in config:
        env.host_string = config['remote']['server']['ip']
        remote = config['remote']
        config['remote_name'] = 'production'
    elif 'remotes' in config:
        # Prompt for a host selection.
        remote_keys = config['remotes'].keys()
        if len(remote_keys) == 0:
            print "No remotes specified in config."
            exit()
        elif len(remote_keys) == 1:
            remote_prompt = remote_keys[0]

        elif remote:
            remote_prompt = remote
            if remote_prompt not in remote_keys:
                raise Exception("Invalid remote name `{}`.".format(remote))
                exit()
        else:
            print "Available hosts: {}".format(
                ', '.join(config['remotes'].keys())
            )

            remote_prompt = prompt("Please enter a remote: ",
                                   default=remote_keys[0])

        env.host_string = config['remotes'][remote_prompt]['server']['ip']
        remote = config['remotes'][remote_prompt]
        config['remote_name'] = remote_prompt
    else:
        print "No remotes specified in config."
        exit()

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
                print "Failed to connect to remote server"
                exit()

    # Return the server config
    return config, remote


def check_request(request, env, request_type, color='\033[95m'):
    if len(request['dark']) or request['contacted'][env.host_string].get(
            'failed', None) is True:
        print "[{}{}\033[0m] [\033[91mFAILED\033[0m]".format(color,
                                                             request_type)
        print request
        exit()
    else:
        print "[{}{}\033[0m] [\033[92mDONE\033[0m]".format(color, request_type)


def ansible_task(env, **kwargs):
    # Create ansible inventory
    ansible_inventory = ansible.inventory.Inventory([env.host_string])

    ansible_args = dict({
                            'pattern': 'all',
                            'inventory': ansible_inventory,
                            'sudo': True,
                            'sudo_user': 'root',
                            'remote_user': env.user
                        }.items() + kwargs.items())

    if getattr(env, 'key_filename'):
        ansible_args['private_key_file'] = env.key_filename

    return ansible.runner.Runner(**ansible_args).run()


def run_tasks(env, tasks):
    # Loop tasks
    for task in tasks:

        if not task.get('with_items'):
            print "[\033[95mTASK\033[0m] {}...".format(task['title'])

            # Run task with arguments
            task_result = ansible_task(env, **task['ansible_arguments'])

            # Check result
            check_request(task_result, env, "TASK")

        else:
            print "[\033[95mTASK\033[0m] {}...".format(task['title'])

            # Store task args pattern
            module_args_pattern = task['ansible_arguments']['module_args']

            for item in task.get('with_items'):
                print "[\033[94mITEM\033[0m] {}".format(item)

                # Format args with item
                task['ansible_arguments'][
                    'module_args'] = module_args_pattern.format(
                    item=item
                )

                # Run task with arguments
                task_result = ansible_task(env, **task['ansible_arguments'])

                # Check result
                check_request(task_result, env, "ITEM", color='\033[94m')

            print "[\033[95mTASK\033[0m] [\033[92mDONE\033[0m]"

        print ""
