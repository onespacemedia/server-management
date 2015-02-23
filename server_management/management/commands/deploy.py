from getpass import getpass
import os
import re
from urllib import urlencode
from django.conf import settings as django_settings
from django.core.files.temp import NamedTemporaryFile
from django.core.management.base import BaseCommand
from django.template import loader, Context
from django.template.loader import render_to_string
from fabric.api import *
import requests

from _core import load_config, ansible_task, run_tasks, check_request


class Command(BaseCommand):
    def handle(self, *args, **options):
        # Load server config from project
        config = load_config()

        # Define current host from settings in server config
        env.host_string = config['remote']['server']['ip']
        env.user = 'root'
        env.disable_known_hosts = True
        env.reject_unknown_hosts = False

        # Make sure we can connect to the server
        with hide('output', 'running', 'warnings'):
            with settings(warn_only=True):
                if not run('whoami'):
                    print "Failed to connect to remote server"
                    exit()

        # Set local project path
        local_project_path = django_settings.SITE_ROOT

        # Change into the local project folder
        with hide('output', 'running', 'warnings'):
            with lcd(local_project_path):

                # Get the Git repo URL.
                git_remote = local('git config --get remote.origin.url', capture=True)

                # Is this a bitbucket repo?
                is_bitbucket_repo = 'git@bitbucket.org' in git_remote

                if is_bitbucket_repo:
                    bb_regex = re.match(r'git@bitbucket\.org:(.+)/(.+)\.git', git_remote)

                    if bb_regex:
                        bitbucket_account = bb_regex.group(1)
                        bitbucket_repo = bb_regex.group(2)

                project_folder = local("basename $( find {} -name 'wsgi.py' -not -path '*/.venv/*' -not -path '*/venv/*' | xargs -0 -n1 dirname )".format(
                    local_project_path
                ), capture=True)

        # Compress the domain names for nginx
        domain_names = " ".join(django_settings.ALLOWED_HOSTS)

        # Use the site domain as a fallback domain
        fallback_domain_name = django_settings.SITE_DOMAIN

        # Print some information for the user
        print ""
        print "Project: {}".format(project_folder)
        print "Server ip: {}".format(env.host_string)
        print "Server user: {}".format(env.user)
        print ""

        # Get bitbucket details
        bitbucket_username = prompt("Please enter your BitBucket username:")
        bitbucket_password = getpass("Please enter your BitBucket password: ")

        print ""

        # Create session_files
        session_files = {
            'pgpass': NamedTemporaryFile(delete=False)
        }

        # Parse files
        session_files['pgpass'].write(render_to_string('pgpass', config['remote']['database']))
        session_files['pgpass'].close()

        # Define base tasks
        base_tasks = [
            {
                'title': "Upgrade everything",
                'ansible_arguments': {
                    'module_name': 'apt',
                    'module_args': 'update_cache=yes'
                }
            },
            {
                'title': "Install the following base packages",
                'ansible_arguments': {
                    'module_name': 'apt',
                    'module_args': 'name={item} force=yes state=present'
                },
                'with_items': [
                    'build-essential',
                    'htop',
                    'git',
                    'python-dev',
                    'python-pip',
                    'python-pycurl',
                    'python-httplib2',
                    'supervisor',
                    'openjdk-6-jre-headless',
                    'libjpeg-dev'
                ]
            },
            {
                'title': "Generate SSH key",
                'ansible_arguments': {
                    'module_name': 'user',
                    'module_args': 'name=root generate_ssh_key=yes ssh_key_bits=2048'
                }
            },
            {
                'title': "Install virtualenv",
                'ansible_arguments': {
                    'module_name': 'pip',
                    'module_args': 'name=virtualenv'
                }
            },
        ]
        run_tasks(env.host_string, base_tasks)

        # Define db tasks
        db_tasks = [
            {
                'title': "Install PostgreSQL",
                'ansible_arguments': {
                    'module_name': 'apt',
                    'module_args': 'name={item} force=yes state=present'
                },
                'with_items': [
                    'postgresql-9.3',
                    'postgresql-contrib-9.3',
                    'libpq-dev',
                    'python-psycopg2',
                    'pgtune'
                ]
            },
            {
                'title': "Ensure the PostgreSQL service is running",
                'ansible_arguments': {
                    'module_name': 'service',
                    'module_args': 'name=postgresql state=started enabled=yes'
                }
            },
            {
                'title': "Backuping Postgresql main config file",
                'ansible_arguments': {
                    'module_name': 'command',
                    'module_args': 'mv /etc/postgresql/9.3/main/postgresql.conf /etc/postgresql/9.3/main/postgresql.conf.old creates=/etc/postgresql/9.3/main/postgresql.conf.old'
                }
            },
            {
                'title': "Setting Postgresql Optmizing via pgtune",
                'ansible_arguments': {
                    'module_name': 'command',
                    'module_args': 'pgtune -i /etc/postgresql/9.3/main/postgresql.conf.old -o /etc/postgresql/9.3/main/postgresql.conf --type=Web',
                    'sudo_user': 'postgres'
                }
            },
            {
                'title': "Ensure database is created",
                'ansible_arguments': {
                    'module_name': 'postgresql_db',
                    'module_args': "name='{}' encoding='UTF-8' lc_collate='en_GB.UTF-8' lc_ctype='en_GB.UTF-8' template='template0' state=present".format(
                        config['remote']['database']['name']
                    ),
                    'sudo_user': 'postgres'
                }
            },
            {
                'title': "Ensure user has access to the database",
                'ansible_arguments': {
                    'module_name': 'postgresql_user',
                    'module_args': "db='{}' name='{}' password='{}' priv=ALL state=present".format(
                        config['remote']['database']['name'],
                        config['remote']['database']['user'],
                        config['remote']['database']['password']
                    ),
                    'sudo_user': 'postgres'
                }
            },
            {
                'title': "Ensure user does not have unnecessary privileges",
                'ansible_arguments': {
                    'module_name': 'postgresql_user',
                    'module_args': 'name={} role_attr_flags=NOSUPERUSER,NOCREATEDB state=present'.format(
                        config['remote']['database']['name']
                    ),
                    'sudo_user': 'postgres'
                }
            },
            {
                'title': "Make a .pgpass file",
                'ansible_arguments': {
                    'module_name': 'copy',
                    'module_args': 'src={} dest=~/.pgpass owner=root mode=0600 force=yes'.format(
                        session_files['pgpass'].name
                    )
                }
            },
        ]
        run_tasks(env.host_string, db_tasks)

        # Define web tasks
        web_tasks = [
            {
                'title': "Create the application group",
                'ansible_arguments': {
                    'module_name': 'group',
                    'module_args': 'name=webapps system=yes state=present'
                }
            },
            {
                'title': "Add the application user to the application group",
                'ansible_arguments': {
                    'module_name': 'user',
                    'module_args': 'name={} group=webapps state=present'.format(
                        project_folder
                    )
                }
            },
        ]
        run_tasks(env.host_string, web_tasks)

        # Get SSH Key from server
        print "[\033[95mTASK\033[0m] Request SSH key from server..."
        ssh_key_request = ansible_task(
            env.host_string,
            module_name='shell',
            module_args='cat ~/.ssh/id_rsa.pub'
        )
        check_request(ssh_key_request, env.host_string, "TASK")
        ssh_key = ssh_key_request['contacted'][env.host_string]['stdout']

        print ""

        # Get the current SSH keys in the repo
        print "[\033[95mTASK\033[0m] Checking bitbucket repository for an existing SSH key..."
        try:
            repo_ssh_keys = requests.get('https://bitbucket.org/api/1.0/repositories/{}/{}/deploy-keys/'.format(
                bitbucket_account,
                bitbucket_repo
            ), auth=(bitbucket_username, bitbucket_password))
        except:
            print "[\033[95mTASK\033[0m] [\033[91mFAILED\033[0m]"
            exit()
        print "[\033[95mTASK\033[0m] [\033[92mDONE\033[0m]"

        print ""

        if repo_ssh_keys.text.find(ssh_key) == -1:
            print "[\033[95mTASK\033[0m] Adding the SSH key to bitbucket..."

            try:
                add_key_to_repo = requests.post(
                    'https://bitbucket.org/api/1.0/repositories/{}/{}/deploy-keys/'.format(
                        bitbucket_account,
                        bitbucket_repo
                    ),
                    data=urlencode({
                        'label': 'Remote Server',
                        'key': ssh_key
                    }),
                    auth=(bitbucket_username, bitbucket_password)
                )
            except Exception as error:
                raise error
                print "[\033[95mTASK\033[0m] [\033[91mFAILED\033[0m]"
                exit()
            print "[\033[95mTASK\033[0m] [\033[92mDONE\033[0m]"

            print ""

        # Define git tasks
        git_tasks = [
            {
                'title': "Setup the Git repo",
                'ansible_arguments': {
                    'module_name': 'git',
                    'module_args': 'repo={} dest={} accept_hostkey=yes'.format(
                        "git@bitbucket.org:{}/{}.git".format(
                            bitbucket_account,
                            bitbucket_repo
                        ),
                        "/var/www/{}".format(
                            project_folder
                        )
                    )
                }
            },
        ]
        run_tasks(env.host_string, git_tasks)

        # Define static tasks
        static_tasks = [
            {
                'title': "Make the static directory",
                'ansible_arguments': {
                    'module_name': 'file',
                    'module_args': 'path={} state=directory owner={} recurse=yes'.format(
                        "/var/www/{}_static/".format(
                            project_folder
                        ),
                        project_folder
                    )
                }
            },
            {
                'title': "Make the media directory",
                'ansible_arguments': {
                    'module_name': 'file',
                    'module_args': 'path={} state=directory owner={} recurse=yes'.format(
                        "/var/www/{}_media/".format(
                            project_folder
                        ),
                        project_folder
                    )
                }
            },
        ]
        run_tasks(env.host_string, static_tasks)

        # Delete files
        for session_file in session_files:
            os.unlink(session_files[session_file].name)
