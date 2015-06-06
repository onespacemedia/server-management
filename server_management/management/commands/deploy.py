from getpass import getpass
import os
import re
from urllib import urlencode

from django.conf import settings as django_settings
from django.core.files.temp import NamedTemporaryFile
from django.core.management.base import BaseCommand
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

                project_folder = local(
                    "basename $( find {} -name 'wsgi.py' -not -path '*/.venv/*' -not -path '*/venv/*' | xargs -0 -n1 dirname )".format(
                        local_project_path
                    ), capture=True)

                with settings(warn_only=True):
                    if local('[[ -e ../requirements.txt ]]').return_code:
                        print "No requirements.txt"
                        exit()

        # Compress the domain names for nginx
        domain_names = " ".join(django_settings.ALLOWED_HOSTS)

        # Use the site domain as a fallback domain
        fallback_domain_name = raw_input("What should the default domain be? ({}) ".format(django_settings.SITE_DOMAIN)) or django_settings.SITE_DOMAIN

        # Print some information for the user
        print ""
        print "Project: {}".format(project_folder)
        print "Server IP: {}".format(env.host_string)
        print "Server user: {}".format(env.user)
        print ""

        # Get bitbucket details

        if os.environ.get('BITBUCKET_USERNAME', False) and os.environ.get('BITBUCKET_PASSWORD', False):
            bitbucket_username = os.environ.get('BITBUCKET_USERNAME')
            bitbucket_password = os.environ.get('BITBUCKET_PASSWORD')
        else:
            bitbucket_username = prompt("Please enter your BitBucket username:")
            bitbucket_password = getpass("Please enter your BitBucket password: ")

        print ""

        # Create session_files
        session_files = {
            'pgpass': NamedTemporaryFile(delete=False),
            'gunicorn_start': NamedTemporaryFile(delete=False),
            'supervisor_config': NamedTemporaryFile(delete=False),
            'nginx_site_config': NamedTemporaryFile(delete=False),
            'apt_periodic': NamedTemporaryFile(delete=False),
        }

        # Parse files
        session_files['pgpass'].write(render_to_string('pgpass', config['remote']['database']))
        session_files['pgpass'].close()

        session_files['gunicorn_start'].write(render_to_string('gunicorn_start', {
            'project': project_folder
        }))
        session_files['gunicorn_start'].close()

        session_files['supervisor_config'].write(render_to_string('supervisor_config', {
            'project': project_folder
        }))
        session_files['supervisor_config'].close()

        session_files['nginx_site_config'].write(render_to_string('nginx_site_config', {
            'project': project_folder,
            'domain_names': domain_names,
            'fallback_domain_name': fallback_domain_name
        }))
        session_files['nginx_site_config'].close()

        session_files['apt_periodic'].write(render_to_string('apt_periodic'))
        session_files['apt_periodic'].close()

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
                    'module_name':  'lineinfile',
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
                    'supervisor',
                    'libjpeg-dev',
                    'libffi-dev',
                    'npm',
                    'memcached',
                    'libgeoip-dev',
                ]
            },
            {
                'title': 'Symlink Node.js',
                'ansible_arguments': {
                    'module_name': 'file',
                    'module_args': 'src=/usr/bin/nodejs dest=/usr/bin/node state=link'
                }
            },
            {
                'title': 'Install bower with npm',
                'ansible_arguments': {
                    'module_name': 'npm',
                    'module_args': 'name=bower global=yes'
                }
            },
            {
                'title': 'Install gulp with npm',
                'ansible_arguments': {
                    'module_name': 'npm',
                    'module_args': 'name=gulp global=yes'
                }
            },
            {
                'title': "Install virtualenv",
                'ansible_arguments': {
                    'module_name': 'pip',
                    'module_args': 'name=virtualenv'
                }
            }
        ]
        run_tasks(env.host_string, base_tasks)

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
                    'module_name':  'lineinfile',
                    'module_args': 'dest=/etc/sudoers regexp="^%sudo" state=absent'
                }
            },
            {
                'title': 'Add deploy user to sudoers',
                'ansible_arguments': {
                    'module_name':  'lineinfile',
                    'module_args': 'dest=/etc/sudoers regexp="deploy ALL" line="deploy ALL=(ALL:ALL) NOPASSWD: ALL" state=present'
                }
            },
            {
                'title': 'Disallow root SSH access',
                'ansible_arguments': {
                    'module_name':  'lineinfile',
                    'module_args': 'dest=/etc/ssh/sshd_config regexp="^PermitRootLogin" line="PermitRootLogin no" state=present'
                }
            },
            {
                'title': 'Disallow password authentication',
                'ansible_arguments': {
                    'module_name':  'lineinfile',
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
        run_tasks(env.host_string, ssh_tasks)

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
                'title': "Ensure database is created",
                'ansible_arguments': {
                    'module_name': 'postgresql_db',
                    'module_args': "name='{}' encoding='UTF-8' lc_collate='en_GB.UTF-8' lc_ctype='en_GB.UTF-8' "
                                   "template='template0' state=present".format(
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
        ]
        run_tasks(env.host_string, db_tasks)

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
        run_tasks(env.host_string, web_tasks)

        # Get SSH Key from server
        print "[\033[95mTASK\033[0m] Request SSH key from server..."
        ssh_key_request = ansible_task(
            env.host_string,
            module_name='shell',
            module_args='cat /home/deploy/.ssh/id_rsa.pub'
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
                requests.post(
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
                    'module_args': 'repo={} dest={} accept_hostkey=yes ssh_opts="-o StrictHostKeyChecking=no"'.format(
                        "git@bitbucket.org:{}/{}.git".format(
                            bitbucket_account,
                            bitbucket_repo
                        ),
                        "/var/www/{}".format(
                            project_folder
                        )
                    ),
                    'sudo_user': 'deploy'
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
        run_tasks(env.host_string, static_tasks)

        # Define venv tasks
        venv_tasks = [
            {
                'title': "Create the virtualenv",
                'ansible_arguments': {
                    'module_name': 'command',
                    'module_args': 'virtualenv /var/www/{project}/.venv --no-site-packages creates=/var/www/{'
                                   'project}/.venv'.format(
                                       project=project_folder
                                   )
                }
            },
            {
                'title': "Create the Gunicorn script file",
                'ansible_arguments': {
                    'module_name': 'copy',
                    'module_args': 'src={file} dest=/var/www/{project}/.venv/bin/gunicorn_start owner={project} '
                                   'group=webapps mode=0755 backup=yes'.format(
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
                    'module_args': 'path=/var/log/gunicorn_supervisor.log owner={} group=webapps mode=0664 '
                                   'state=file'.format(
                                       project_folder
                                   )
                }
            },
        ]
        run_tasks(env.host_string, venv_tasks)

        # Check to see if we have a requirements file
        print "[\033[95mTASK\033[0m] Looking for a requirements.txt file..."
        requirements_check = ansible_task(
            env.host_string,
            module_name='stat',
            module_args='path=/var/www/{}/requirements.txt'.format(
                project_folder
            )
        )
        check_request(requirements_check, env.host_string, "TASK")

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
                        'module_args': 'virtualenv=/var/www/{project}/.venv requirements=/var/www/{'
                                       'project}/requirements.txt'.format(
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
                'title': "Collect static files",
                'ansible_arguments': {
                    'module_name': 'django_manage',
                    'module_args': 'command=collectstatic app_path=/var/www/{project} virtualenv=/var/www/{'
                                   'project}/.venv link=yes settings={project}.settings.production'.format(
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
                'title': 'Compile CSS',
                'ansible_arguments': {
                    'module_name': 'shell',
                    'module_args': 'gulp styles chdir=/var/www/{project}'.format(
                        project=project_folder,
                    )
                }
            }
        ]

        run_tasks(env.host_string, requirement_tasks)

        # Define permission tasks
        permission_tasks = [
            {
                'title': "Make the run directory",
                'ansible_arguments': {
                    'module_name': 'file',
                    'module_args': 'path={} state=directory owner={} group=webapps recurse=yes'.format(
                        "/var/www/{}/.venv/run".format(
                            project_folder
                        ),
                        project_folder
                    )
                }
            },
            {
                'title': "Ensure that the application file permissions are set properly",
                'ansible_arguments': {
                    'module_name': 'file',
                    'module_args': 'path=/var/www/{project}/.venv recurse=yes owner={project} group=webapps '
                                   'state=directory'.format(
                                       project=project_folder
                                   )
                }
            }
        ]
        run_tasks(env.host_string, permission_tasks)

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
                    'module_args': 'ln -s /etc/nginx/sites-available/{project} /etc/nginx/sites-enabled/{project} '
                                   'creates=/etc/nginx/sites-enabled/{project}'.format(
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
        run_tasks(env.host_string, nginx_tasks)

        # Define supervisor tasks
        supervisor_tasks = [
            {
                'title': "Create the Supervisor config file",
                'ansible_arguments': {
                    'module_name': 'copy',
                    'module_args': 'src={} dest=/etc/supervisor/conf.d/{}.conf backup=yes'.format(
                        session_files['supervisor_config'].name,
                        project_folder
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
        run_tasks(env.host_string, supervisor_tasks)

        # Delete files
        for session_file in session_files:
            os.unlink(session_files[session_file].name)

        with hide('output', 'running'):
            # Create a final dump of the database
            print "[\033[95mTASK\033[0m] Dumping local database to file..."
            local('pg_dump {name} -cOx -U {user} -f ~/{name}-final-dump.sql --clean'.format(
                name=config['local']['database']['name'],
                user=os.getlogin()
            ))
            print "[\033[95mTASK\033[0m] [\033[92mDONE\033[0m]"
            print ""

            # Create uploads folder
            print "[\033[95mTASK\033[0m] Create uploads folder..."
            local('mkdir -p {}'.format(
                django_settings.MEDIA_ROOT
            ))
            print "[\033[95mTASK\033[0m] [\033[92mDONE\033[0m]"
            print ""

            # Sync local files up to the server
            print "[\033[95mTASK\033[0m] Push local uploads to the server..."
            # Ensure the local media folder exists.
            local('mkdir -p {}'.format(
                django_settings.MEDIA_ROOT,
            ))

            local('rsync -rhe "ssh -o StrictHostKeyChecking=no" {}/ {}@{}:{}/'.format(
                django_settings.MEDIA_ROOT,
                'deploy',
                config['remote']['server']['ip'],
                '/var/www/{}_media'.format(
                    project_folder
                )
            ))
            print "[\033[95mTASK\033[0m] [\033[92mDONE\033[0m]"
            print ""

            # Push the database from earlier up to the server
            print "[\033[95mTASK\033[0m] Push local database to the server..."
            local('scp ~/{}-final-dump.sql {}@{}:/tmp/{}.sql'.format(
                config['local']['database']['name'],
                'deploy',
                config['remote']['server']['ip'],
                config['remote']['database']['name'],
            ))
            print "[\033[95mTASK\033[0m] [\033[92mDONE\033[0m]"
            print ""

            # Import the database file
            print "[\033[95mTASK\033[0m] Import uploaded database on the server..."
            sudo("su - {name} -c 'psql -q {name} < /tmp/{name}.sql > /dev/null 2>&1'".format(
                name=config['remote']['database']['name']
            ))
            print "[\033[95mTASK\033[0m] [\033[92mDONE\033[0m]"
            print ""

            # Remove the database file
            print "[\033[95mTASK\033[0m] Delete the uploaded database file on the server..."
            run('rm /tmp/{}.sql'.format(
                config['remote']['database']['name']
            ))
            print "[\033[95mTASK\033[0m] [\033[92mDONE\033[0m]"
            print ""

            # Remove the SQL file from the host
            print "[\033[95mTASK\033[0m] Delete the local database file..."
            local('rm ~/{}-final-dump.sql'.format(
                config['local']['database']['name']
            ))
            print "[\033[95mTASK\033[0m] [\033[92mDONE\033[0m]"
            print ""

            print "Deploy complete"
