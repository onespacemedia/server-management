from getpass import getpass
import os
import re
from urllib import urlencode

from django.conf import settings as django_settings
from django.core.files.temp import NamedTemporaryFile
from django.template.loader import render_to_string
from fabric.api import *
import requests

from _core import load_config, ansible_task, run_tasks, check_request, ServerManagementBaseCommand


class Command(ServerManagementBaseCommand):

    def handle(self, *args, **options):
        # Load server config from project
        config, remote = load_config(env, options.get('remote', ''), config_user='root')

        # Set local project path
        local_project_path = django_settings.SITE_ROOT

        # Change into the local project folder
        with hide('output', 'running', 'warnings'):
            with lcd(local_project_path):

                # Get the Git repo URL.
                remotes = local('git remote', capture=True).split('\n')

                if len(remotes) == 1:
                    git_remote = local('git config --get remote.{}.url'.format(remotes[0]), capture=True)
                else:
                    def validate_choice(choice):
                        if choice in remotes:
                            return choice
                        raise Exception("That is not a valid choice.")

                    choice = prompt("Which Git remote would you like to use?", validate=validate_choice)
                    git_remote = local('git config --get remote.{}.url'.format(choice), capture=True)

                # Is this a bitbucket repo?
                is_bitbucket_repo = 'git@bitbucket.org' in git_remote
                is_github_repo = 'github.com' in git_remote

                if is_bitbucket_repo:
                    bb_regex = re.match(r'git@bitbucket\.org:(.+)/(.+)\.git', git_remote)

                    if bb_regex:
                        bitbucket_account = bb_regex.group(1)
                        bitbucket_repo = bb_regex.group(2)
                    else:
                        print 'Unable to determine Bitbucket details.'
                        exit()

                elif is_github_repo:
                    gh_regex = re.match(r'(?:git@|https:\/\/)github.com[:/]([\w-]+)/([\w-]+)\.git$', git_remote)

                    if gh_regex:
                        github_account = gh_regex.group(1)
                        github_repo = gh_regex.group(2)
                    else:
                        print 'Unable to determine Github details.'
                        exit()
                else:
                    print 'Unable to determine Git host from remote URL: {}'.format(git_remote)
                    exit()

                project_folder = local_project_path.replace(os.path.abspath(os.path.join(local_project_path, '..')) + '/', '')

                with settings(warn_only=True):
                    if local('[[ -e ../requirements.txt ]]').return_code:
                        print "No requirements.txt"
                        exit()

        # Compress the domain names for nginx
        domain_names = " ".join(django_settings.ALLOWED_HOSTS)

        # Use the site domain as a fallback domain
        fallback_domain_name = raw_input("What should the default domain be? ({}) ".format(django_settings.SITE_DOMAIN)) or django_settings.SITE_DOMAIN

        # Override username (for DO hosts).
        if env.user == 'deploy':
            env.user = 'root'

        # Print some information for the user
        print ""
        print "Project: {}".format(project_folder)
        print "Server IP: {}".format(env.host_string)
        print "Server user: {}".format(env.user)
        print ""

        # Get BitBucket / Github details

        if is_bitbucket_repo:
            if os.environ.get('BITBUCKET_USERNAME', False) and os.environ.get('BITBUCKET_PASSWORD', False):
                bitbucket_username = os.environ.get('BITBUCKET_USERNAME')
                bitbucket_password = os.environ.get('BITBUCKET_PASSWORD')
            else:
                bitbucket_username = prompt("Please enter your BitBucket username:")
                bitbucket_password = getpass("Please enter your BitBucket password: ")
        elif is_github_repo:
            if os.environ.get('GITHUB_TOKEN', False):
                github_token = os.environ.get('GITHUB_TOKEN')
            else:
                github_token = prompt("Please enter your Github token (obtained from https://github.com/settings/tokens):")

        print ""

        # Create session_files
        session_files = {
            'gunicorn_start': NamedTemporaryFile(delete=False),
            'supervisor_config': NamedTemporaryFile(delete=False),
            'memcached_supervisor_config': NamedTemporaryFile(delete=False),
            'nginx_site_config': NamedTemporaryFile(delete=False),
            'apt_periodic': NamedTemporaryFile(delete=False),
        }

        # Parse files
        session_files['gunicorn_start'].write(render_to_string('gunicorn_start', {
            'project': project_folder,
            'settings': remote['server'].get('settings_file', 'production')
        }))
        session_files['gunicorn_start'].close()

        session_files['supervisor_config'].write(render_to_string('supervisor_config', {
            'project': project_folder
        }))
        session_files['supervisor_config'].close()

        session_files['memcached_supervisor_config'].write(render_to_string('memcached_supervisor_config', {
            'project': project_folder
        }))
        session_files['memcached_supervisor_config'].close()

        session_files['nginx_site_config'].write(render_to_string('nginx_site_config', {
            'project': project_folder,
            'domain_names': domain_names,
            'fallback_domain_name': fallback_domain_name
        }))
        session_files['nginx_site_config'].close()

        session_files['apt_periodic'].write(render_to_string('apt_periodic'))
        session_files['apt_periodic'].close()

        # Check if optional packages are defined in the config.
        optional_packages = {}

        if 'optional_packages' in config:
            optional_packages = config['optional_packages']

        # Define base tasks
        base_tasks = [
            {
                'title': "Update apt cache and upgrade everything",
                'ansible_arguments': {
                    'module_name': 'apt',
                    'module_args': 'update_cache=yes upgrade=yes'
                }
            },
            {
                'title': "Add nodesource key",
                'ansible_arguments': {
                    'module_name': 'apt_key',
                    'module_args': 'url=https://deb.nodesource.com/gpgkey/nodesource.gpg.key'
                }
            },
            {
                'title': "Add nodesource repo",
                'ansible_arguments': {
                    'module_name': 'apt_repository',
                    'module_args': 'repo="deb https://deb.nodesource.com/node_5.x trusty main" update_cache=yes'
                }
            },
            {
                'title': 'Install unattended-upgrades',
                'ansible_arguments': {
                    'module_name': 'apt',
                    'module_args': 'pkg=unattended-upgrades state=present'
                }
            },
            {
                'title': 'Adjust APT update intervals',
                'ansible_arguments': {
                    'module_name': 'copy',
                    'module_args': 'src={} dest=/etc/apt/apt.conf.d/10periodic'.format(
                        session_files['apt_periodic'].name,
                    )
                }
            },
            {
                'title': 'Make sure unattended-upgrades only installs from $ubuntu_release-security',
                'ansible_arguments': {
                    'module_name': 'lineinfile',
                    'module_args': 'dest=/etc/apt/apt.conf.d/50unattended-upgrades regexp="$ubuntu_release-updates" state=absent'
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
                    'git',
                    'python-dev',
                    'python-pip',
                    'python-passlib',  # Required for generating the htpasswd file
                    'supervisor',
                    'libjpeg-dev',
                    'libffi-dev',
                    'nodejs',
                    'memcached',
                ] + (
                    ['libgeoip-dev'] if optional_packages.get('geoip', True) else []
                ) + (
                    ['libmysqlclient-dev'] if optional_packages.get('mysql', True) else []
                )
            },
            {
                'title': "Install virtualenv",
                'ansible_arguments': {
                    'module_name': 'pip',
                    'module_args': 'name=virtualenv'
                }
            },
            {
                'title': "Set the timezone to UTC",
                'ansible_arguments': {
                    'module_name': 'file',
                    'module_args': 'src=/usr/share/zoneinfo/UTC dest=/etc/localtime force=yes state=link'
                }
            }
        ]
        run_tasks(env, base_tasks)

        # Configure the firewall.
        firewall_tasks = [
            {
                'title': 'Allow SSH connections through the firewall',
                'ansible_arguments': {
                    'module_name': 'ufw',
                    'module_args': 'rule=allow port=22 proto=tcp'
                }
            },
            {
                'title': 'Allow HTTP connections through the firewall',
                'ansible_arguments': {
                    'module_name': 'ufw',
                    'module_args': 'rule=allow port=80 proto=tcp'
                }
            },
            {
                'title': 'Enable the firewall, deny all other traffic',
                'ansible_arguments': {
                    'module_name': 'ufw',
                    'module_args': 'state=enabled policy=deny'
                }
            }
        ]

        run_tasks(env, firewall_tasks)

        # Configure swap
        swap_tasks = [
            {
                'title': 'Create a swap file',
                'ansible_arguments': {
                    'module_name': 'command',
                    'module_args': 'fallocate -l 4G /swapfile'
                }
            },
            {
                'title': 'Set permissions on swapfile to 600',
                'ansible_arguments': {
                    'module_name': 'file',
                    'module_args': 'path=/swapfile owner=root group=root mode=0600'
                }

            },
            {
                'title': 'Format swapfile for swap',
                'ansible_arguments': {
                    'module_name': 'command',
                    'module_args': 'mkswap /swapfile'
                }
            },
            {
                'title': 'Add the file to the system as a swap file',
                'ansible_arguments': {
                    'module_name': 'command',
                    'module_args': 'swapon /swapfile'
                }
            },
            {
                'title': 'Write fstab line for swapfile',
                'ansible_arguments': {
                    'module_name': 'mount',
                    'module_args': 'name=none src=/swapfile fstype=swap opts=sw passno=0 dump=0 state=present'
                }
            }
        ]

        run_tasks(env, swap_tasks)

        # Define SSH tasks
        ssh_tasks = [
            {
                'title': 'Install fail2ban',
                'ansible_arguments': {
                    'module_name': 'apt',
                    'module_args': 'name={item} state=present'
                },
                'with_items': [
                    'fail2ban'
                ]
            },
            {
                'title': "Create the application group",
                'ansible_arguments': {
                    'module_name': 'group',
                    'module_args': 'name=webapps system=yes state=present'
                }
            },
            {
                'title': 'Add deploy user',
                'ansible_arguments': {
                    'module_name': 'user',
                    'module_args': 'name=deploy group=webapps generate_ssh_key=yes shell=/bin/bash'
                }
            },
            {
                'title': "Add authorized keys",
                'ansible_arguments': {
                    'module_name': 'command',
                    'module_args': 'mv /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys '
                                   'creates=/home/deploy/.ssh/authorized_keys'
                }
            },
            {
                'title': 'Fix file permissions of deploy authorized keys',
                'ansible_arguments': {
                    'module_name': 'file',
                    'module_args': 'path=/home/deploy/.ssh/authorized_keys owner=deploy group=webapps'
                }
            },
            {
                'title': 'Remove sudo group rights',
                'ansible_arguments': {
                    'module_name': 'lineinfile',
                    'module_args': 'dest=/etc/sudoers regexp="^%sudo" state=absent'
                }
            },
            {
                'title': 'Add deploy user to sudoers',
                'ansible_arguments': {
                    'module_name': 'lineinfile',
                    'module_args': 'dest=/etc/sudoers regexp="deploy ALL" line="deploy ALL=(ALL:ALL) NOPASSWD: ALL" state=present'
                }
            },
            {
                'title': 'Disallow root SSH access',
                'ansible_arguments': {
                    'module_name': 'lineinfile',
                    'module_args': 'dest=/etc/ssh/sshd_config regexp="^PermitRootLogin" line="PermitRootLogin no" state=present'
                }
            },
            {
                'title': 'Disallow password authentication',
                'ansible_arguments': {
                    'module_name': 'lineinfile',
                    'module_args': 'dest=/etc/ssh/sshd_config regexp="^PasswordAuthentication" line="PasswordAuthentication no" state=present'
                }
            },
            {
                'title': 'Restart SSH',
                'ansible_arguments': {
                    'module_name': 'service',
                    'module_args': 'name=ssh state=restarted'
                }
            }
        ]
        run_tasks(env, ssh_tasks)

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
                'title': "Backuping PostgreSQL main config file",
                'ansible_arguments': {
                    'module_name': 'command',
                    'module_args': 'mv /etc/postgresql/9.3/main/postgresql.conf '
                                   '/etc/postgresql/9.3/main/postgresql.conf.old '
                                   'creates=/etc/postgresql/9.3/main/postgresql.conf.old'
                }
            },
            {
                'title': "Optimising PostgreSQL via pgtune",
                'ansible_arguments': {
                    'module_name': 'command',
                    'module_args': 'pgtune -i /etc/postgresql/9.3/main/postgresql.conf.old -o '
                                   '/etc/postgresql/9.3/main/postgresql.conf --type=Web',
                    'sudo_user': 'postgres'
                }
            },
            {
                'title': "Ensure we have the database locale",
                'ansible_arguments': {
                    'module_name': 'locale_gen',
                    'module_args': 'name=en_GB.UTF-8 state=present'
                }
            },
            {
                'title': 'Reconfigure locales',
                'ansible_arguments': {
                    'module_name': 'command',
                    'module_args': 'dpkg-reconfigure locales'
                }
            },
            {
                'title': "Restart PostgreSQL",
                'ansible_arguments': {
                    'module_name': 'service',
                    'module_args': 'name=postgresql state=restarted enabled=yes'
                }
            },
            {
                'title': "Ensure database is created",
                'ansible_arguments': {
                    'module_name': 'postgresql_db',
                    'module_args': "name='{}' encoding='UTF-8' lc_collate='en_GB.UTF-8' lc_ctype='en_GB.UTF-8' "
                                   "template='template0' state=present".format(
                                       remote['database']['name']
                                   ),
                    'sudo_user': 'postgres'
                }
            },
            {
                'title': "Ensure user has access to the database",
                'ansible_arguments': {
                    'module_name': 'postgresql_user',
                    'module_args': "db='{}' name='{}' password='{}' priv=ALL state=present".format(
                        remote['database']['name'],
                        remote['database']['user'],
                        remote['database']['password']
                    ),
                    'sudo_user': 'postgres'
                }
            },
            {
                'title': "Ensure user does not have unnecessary privileges",
                'ansible_arguments': {
                    'module_name': 'postgresql_user',
                    'module_args': 'name={} role_attr_flags=NOSUPERUSER,NOCREATEDB state=present'.format(
                        remote['database']['name']
                    ),
                    'sudo_user': 'postgres'
                }
            },
            {
                'title': 'Pre-empt PostgreSQL breaking..',
                'ansible_arguments': {
                    'module_name': 'lineinfile',
                    'module_args': 'dest=/etc/postgresql/9.3/main/pg_ctl.conf regexp="^pg_ctl_options" line="pg_ctl_options = \'-l /tmp/pg.log\'" state=present'
                }
            },
        ]
        run_tasks(env, db_tasks)

        # Define web tasks
        web_tasks = [
            {
                'title': "Add the application user to the application group",
                'ansible_arguments': {
                    'module_name': 'user',
                    'module_args': 'name={} group=webapps state=present'.format(
                        project_folder
                    )
                }
            },
            {
                'title': "Create the project directory",
                'ansible_arguments': {
                    'module_name': 'file',
                    'module_args': 'path=/var/www/{} owner={} group=webapps mode=0775 state=directory'.format(
                        project_folder,
                        project_folder,
                    )
                }
            }
        ]
        run_tasks(env, web_tasks)

        # Get SSH Key from server
        print "[\033[95mTASK\033[0m] Request SSH key from server..."
        ssh_key_request = ansible_task(
            env,
            module_name='shell',
            module_args='cat /home/deploy/.ssh/id_rsa.pub'
        )
        check_request(ssh_key_request, env, "TASK")
        ssh_key = ssh_key_request['contacted'][env.host_string]['stdout']

        print ""
        # Get the current SSH keys in the repo
        if is_bitbucket_repo:
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
                    requests.post(
                        'https://bitbucket.org/api/1.0/repositories/{}/{}/deploy-keys/'.format(
                            bitbucket_account,
                            bitbucket_repo
                        ),
                        data=urlencode({
                            'label': 'Application Server ({})'.format(env.host_string),
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

        elif is_github_repo:
            print "[\033[95mTASK\033[0m] Adding the SSH key to Github..."

            try:
                requests.post('https://api.github.com/repos/{}/{}/keys'.format(github_account, github_repo), json={
                    'title': 'Application Server ({})'.format(env.host_string),
                    'key': ssh_key,
                    'read_only': True
                }, headers={
                    'Authorization': 'token {}'.format(github_token)
                })
            except Exception as e:
                print e.errors
                print "[\033[95mTASK\033[0m] [\033[91mFAILED\033[0m]"

        # Define git tasks
        if is_bitbucket_repo:
            git_url = "git@bitbucket.org:{}/{}.git".format(
                bitbucket_account,
                bitbucket_repo
            )
        elif is_github_repo:
            git_url = 'git@github.com:{}/{}.git'.format(
                github_account,
                github_repo
            )

        git_tasks = [
            {
                'title': "Setup the Git repo",
                'ansible_arguments': {
                    'module_name': 'git',
                    'module_args': 'repo={} dest={} accept_hostkey=yes ssh_opts="-o StrictHostKeyChecking=no"'.format(
                        git_url,
                        "/var/www/{}".format(
                            project_folder
                        )
                    ),
                    'sudo_user': 'deploy'
                }
            },
        ]
        run_tasks(env, git_tasks)

        # Define static tasks
        static_tasks = [
            {
                'title': "Make the static directory",
                'ansible_arguments': {
                    'module_name': 'file',
                    'module_args': 'path={} state=directory owner={} group=webapps mode=0775 recurse=yes'.format(
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
                    'module_args': 'path={} state=directory owner={} group=webapps mode=0775 recurse=yes'.format(
                        "/var/www/{}_media/".format(
                            project_folder
                        ),
                        project_folder
                    )
                }
            },
        ]
        run_tasks(env, static_tasks)

        # Define venv tasks
        venv_tasks = [
            {
                'title': "Create the virtualenv",
                'ansible_arguments': {
                    'module_name': 'command',
                    'module_args': 'virtualenv /var/www/{project}/.venv --no-site-packages creates=/var/www/{project}/.venv'.format(
                        project=project_folder
                    )
                }
            },
            {
                'title': "Create the Gunicorn script file",
                'ansible_arguments': {
                    'module_name': 'copy',
                    'module_args': 'src={file} dest=/var/www/{project}/gunicorn_start owner={project} group=webapps mode=0755 backup=yes'.format(
                        file=session_files['gunicorn_start'].name,
                        project=project_folder
                    )
                }
            },
            {
                'title': "Create the application log folder",
                'ansible_arguments': {
                    'module_name': 'file',
                    'module_args': 'path=/var/log owner={} group=webapps mode=0774 state=directory'.format(
                        project_folder
                    )
                }
            },
            {
                'title': "Create the application log file",
                'ansible_arguments': {
                    'module_name': 'command',
                    'module_args': 'touch /var/log/gunicorn_supervisor.log creates=/var/log/gunicorn_supervisor.log'
                }
            },
            {
                'title': "Set permission to the application log file",
                'ansible_arguments': {
                    'module_name': 'file',
                    'module_args': 'path=/var/log/gunicorn_supervisor.log owner={} group=webapps mode=0664 state=file'.format(
                        project_folder
                    )
                }
            },
        ]
        run_tasks(env, venv_tasks)

        # Check to see if we have a requirements file
        print "[\033[95mTASK\033[0m] Looking for a requirements.txt file..."
        requirements_check = ansible_task(
            env,
            module_name='stat',
            module_args='path=/var/www/{}/requirements.txt'.format(
                project_folder
            )
        )
        check_request(requirements_check, env, "TASK")

        print ""

        # Define requirement tasks
        requirement_tasks = []

        # Check to see if the requirements file exists
        if requirements_check['contacted'][env.host_string]['stat']['exists']:
            requirement_tasks.append(
                {
                    'title': "Install packages required by the Django app inside virtualenv",
                    'ansible_arguments': {
                        'module_name': 'pip',
                        'module_args': 'virtualenv=/var/www/{project}/.venv requirements=/var/www/{project}/requirements.txt'.format(
                            project=project_folder
                        )
                    }
                }
            )

        # Add the regular tasks
        requirement_tasks = requirement_tasks + [
            {
                'title': "Make sure Gunicorn is installed",
                'ansible_arguments': {
                    'module_name': 'pip',
                    'module_args': 'virtualenv=/var/www/{project}/.venv name=gunicorn'.format(
                        project=project_folder
                    )
                }
            },
            {
                'title': "Update media permissions",
                'ansible_arguments': {
                    'module_name': 'file',
                    'module_args': 'path=/var/www/{project}_media/ owner={project} group=webapps recurse=yes'.format(
                        project=project_folder
                    )
                }
            },
        ]

        run_tasks(env, requirement_tasks)

        # Define permission tasks
        permission_tasks = [
            {
                'title': "Ensure that the application file permissions are set properly",
                'ansible_arguments': {
                    'module_name': 'file',
                    'module_args': 'path=/var/www/{project}/.venv recurse=yes owner={project} group=webapps state=directory'.format(
                        project=project_folder
                    )
                }
            }
        ]
        run_tasks(env, permission_tasks)

        # Define nginx tasks
        nginx_tasks = [
            {
                'title': "Install Nginx",
                'ansible_arguments': {
                    'module_name': 'apt',
                    'module_args': 'name=nginx state=installed'
                }
            },
            {
                'title': "Create the Nginx configuration file",
                'ansible_arguments': {
                    'module_name': 'copy',
                    'module_args': 'src={} dest=/etc/nginx/sites-available/{} backup=yes'.format(
                        session_files['nginx_site_config'].name,
                        project_folder
                    )
                }
            },
            {
                'title': 'Create the .htpasswd file',
                'ansible_arguments': {
                    'module_name': 'htpasswd',
                    'module_args': ' path=/etc/nginx/htpasswd name=onespace password=media owner=root group=root mode=0644'
                }
            },
            {
                'title': "Ensure that the default site is disabled",
                'ansible_arguments': {
                    'module_name': 'command',
                    'module_args': 'rm /etc/nginx/sites-enabled/default removes=/etc/nginx/sites-enabled/default'
                }
            },
            {
                'title': "Ensure that the application site is enabled",
                'ansible_arguments': {
                    'module_name': 'command',
                    'module_args': 'ln -s /etc/nginx/sites-available/{project} /etc/nginx/sites-enabled/{project} creates=/etc/nginx/sites-enabled/{project}'.format(
                        project=project_folder
                    )
                }
            },
            {
                'title': "Reload Nginx",
                'ansible_arguments': {
                    'module_name': 'command',
                    'module_args': 'service nginx reload'
                }
            },
            {
                'title': "Ensure Nginx service is started",
                'ansible_arguments': {
                    'module_name': 'service',
                    'module_args': 'name=nginx state=started enabled=yes'
                }
            },
        ]
        run_tasks(env, nginx_tasks)

        # Define supervisor tasks
        supervisor_tasks = [
            {
                'title': "Create the Supervisor config file for the application",
                'ansible_arguments': {
                    'module_name': 'copy',
                    'module_args': 'src={} dest=/etc/supervisor/conf.d/{}.conf backup=yes'.format(
                        session_files['supervisor_config'].name,
                        project_folder
                    )
                }
            },
            {
                'title': "Stopping memcached and removing from startup runlevels",
                'ansible_arguments': {
                    'module_name': 'service',
                    'module_args': 'name=memcached state=stopped enabled=no'
                }
            },
            {
                'title': "Create the Supervisor config file for memcached",
                'ansible_arguments': {
                    'module_name': 'copy',
                    'module_args': 'src={} dest=/etc/supervisor/conf.d/memcached.conf backup=yes'.format(
                        session_files['memcached_supervisor_config'].name
                    )
                }
            },
            {
                'title': "Re-read the Supervisor config files",
                'ansible_arguments': {
                    'module_name': 'command',
                    'module_args': 'supervisorctl reread'
                }
            },
            {
                'title': "Update Supervisor to add the app in the process group",
                'ansible_arguments': {
                    'module_name': 'command',
                    'module_args': 'supervisorctl update'
                }
            },
        ]
        run_tasks(env, supervisor_tasks)

        # Define build system tasks
        build_systems = {
            "none": [],
            "npm": [
                {
                    'title': 'Symlink Node.js',
                    'ansible_arguments': {
                        'module_name': 'file',
                        'module_args': 'src=/usr/bin/nodejs dest=/usr/bin/node state=link'
                    }
                },
                {
                    'title': 'Install npm packages',
                    'ansible_arguments': {
                        'module_name': 'npm',
                        'module_args': 'path=/var/www/{project}'.format(
                            project=project_folder
                        )
                    }
                },
                {
                    'title': 'Initiate build',
                    'ansible_arguments': {
                        'module_name': 'shell',
                        'module_args': 'npm run build chdir=/var/www/{project}'.format(
                            project=project_folder,
                        )
                    }
                },
                {
                    'title': "Collect static files",
                    'ansible_arguments': {
                        'module_name': 'django_manage',
                        'module_args': 'command=collectstatic app_path=/var/www/{project} virtualenv=/var/www/{project}/.venv settings={project}.settings.{settings}'.format(
                            project=project_folder,
                            settings=remote['server'].get('settings_file',
                                                          'production'),
                        )
                    }
                }
            ]

        }
        run_tasks(env, build_systems[remote['server'].get('build_system', 'none')])

        # Delete files
        for session_file in session_files:
            os.unlink(session_files[session_file].name)

        print "Initial application deployment has completed. You should now pushdb and pushmedia."
