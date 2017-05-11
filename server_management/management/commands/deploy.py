from getpass import getpass
import os
import re
from urllib import urlencode

from django.conf import settings as django_settings
from django.core.files.temp import NamedTemporaryFile
from django.template.loader import render_to_string
from fabric.api import abort, env, hide, local, lcd, prompt, settings
from fabric.contrib.console import confirm
import requests

from ._core import load_config, run_tasks, check_request, ServerManagementBaseCommand


class Command(ServerManagementBaseCommand):

    def handle(self, *args, **options):
        # Load server config from project
        config, remote = load_config(env, options.get('remote', ''), config_user='root', debug=options['debug'])

        # Set local project path
        local_project_path = django_settings.SITE_ROOT

        if django_settings.DEBUG:
            abort(
                "You're currently using your local settings file, you need use production instead.\n"
                "To use production settings pass `--settings={}` to the deploy command.".format(
                    os.getenv('DJANGO_SETTINGS_MODULE').replace('.local', '.production')
                )
            )

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
                        raise Exception('Unable to determine Github details.')
                else:
                    raise Exception('Unable to determine Git host from remote URL: {}'.format(git_remote))

                project_folder = local_project_path.replace(os.path.abspath(os.path.join(local_project_path, '..')) + '/', '')

                with settings(warn_only=True):
                    if local('[[ -e ../requirements.txt ]]').return_code:
                        raise Exception("No requirements.txt")

        # Compress the domain names for nginx
        domain_names = " ".join(django_settings.ALLOWED_HOSTS)

        # Use the site domain as a fallback domain
        fallback_domain_name = prompt("What should the default domain be?", default=django_settings.SITE_DOMAIN)

        domain_names = prompt('Which domains would you like to enable in nginx?', default=domain_names)

        # If the domain is pointing to the droplet already, we can setup SSL.
        setup_ssl_for = [
            domain_name
            for domain_name in domain_names.split(' ')
            if local('dig +short {}'.format(domain_name), capture=True) == remote['server']['ip']
        ]

        if not setup_ssl_for:
            abort("Sorry, it's $CURRENT_YEAR, you need to use SSL. Please update the domain DNS to point to {}.".format(
                remote['server']['ip']
            ))

        for domain_name in domain_names.split(' '):
            if domain_name not in setup_ssl_for:
                print 'SSL will not be configured for {}'.format(domain_name)

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
            # Add nginx and Let's Encrypt PPAs.  We add them up here because an
            # `apt-get update` is require for them to be truly added and that
            # comes next.
            {
                'title': 'Add nginx PPA',
                'command': 'add-apt-repository -y ppa:nginx/stable',
            },
            {
                'title': "Add Let's Encrypt PPA",
                'command': 'add-apt-repository -y ppa:certbot/certbot',
            },
            {
                'title': 'Update apt cache',
                'command': 'apt-get update',
            },
            {
                'title': 'Upgrade everything',
                'command': 'apt-get upgrade -y',
            },
            {
                'title': 'Install unattended-upgrades',
                'command': 'apt-get install -y unattended-upgrades',
            },
            {
                'title': "Install the base packages",
                'command': 'apt-get install -y {}'.format(
                    ' '.join([
                        # Base requirements
                        'build-essential',
                        'git',

                        # Project requirements
                        'python-dev',
                        'python-pip',
                        'python-passlib',  # Required for generating the htpasswd file
                        'supervisor',
                        'libjpeg-dev',
                        'libffi-dev',
                        'libssl-dev',  # Required for nvm.
                        'nodejs',
                        'memcached',
                        'fail2ban',

                        # Postgres requirements
                        'postgresql',
                        'libpq-dev',
                        'python-psycopg2',  # TODO: Is this required?

                        # Other
                        'libgeoip-dev' if optional_packages.get('geoip', True) else '',
                        'libmysqlclient-dev' if optional_packages.get('mysql', True) else '',
                    ])
                )
            },
            {
                'title': 'Adjust APT update intervals',
                'fabric_command': 'put',
                'fabric_args': [session_files['apt_periodic'].name, '/etc/apt/apt.conf.d/10periodic'],
            },
            {
                'title': 'Update pip',
                'command': 'pip install -U pip',
            },
            {
                'title': "Install virtualenv",
                'command': 'pip install virtualenv',  # TODO: Will this need to change if we use Python 3? (probably)
            },
            {
                'title': "Set the timezone to UTC",
                'command': 'timedatectl set-timezone UTC',
            }
        ]

        run_tasks(env, base_tasks)

        # Configure swap
        swap_tasks = [
            {
                'title': 'Create a swap file',
                'command': 'fallocate -l 4G /swapfile',
            },
            {
                'title': 'Set permissions on swapfile to 600',
                'command': 'chmod 0600 /swapfile'
            },
            {
                'title': 'Format swapfile for swap',
                'command': 'mkswap /swapfile',
            },
            {
                'title': 'Add the file to the system as a swap file',
                'command': 'swapon /swapfile',
            },
            {
                'title': 'Write fstab line for swapfile',
                'command': "echo '/swapfile none swap sw 0 0' >> /etc/fstab",
            },
            {
                'title': 'Change swappiness',
                'command': 'sysctl vm.swappiness=10'
            },
            {
                'title': 'Write swappiness to file',
                'command': "echo 'vm.swappiness=10' >> /etc/sysctl.conf",
            },
            {
                'title': 'Reduce cache pressure',
                'command': 'sysctl vm.vfs_cache_pressure=50',
            },
            {
                'title': 'Write cache pressure to file',
                'command': "echo 'vm.vfs_cache_pressure=50' >> /etc/sysctl.conf",
            }
        ]

        run_tasks(env, swap_tasks)

        # Define SSH tasks
        ssh_tasks = [
            {
                'title': "Create the application group",
                'command': 'addgroup -r webapps',  # -r creates a 'system' group
            },
            {
                'title': 'Add deploy user',
                'command': 'adduser --shell /bin/bash --disabled-password deploy webapps',
                'ansible_arguments': {
                    'module_name': 'user',
                    'module_args': 'name=deploy group=webapps generate_ssh_key=yes shell=/bin/bash'
                }
            },
            {
                'title': 'Generate SSH keys for deploy user',
                'command': "ssh-keygen -C test -f ~deploy/.ssh/id_rsa -N ''"
            },
            {
                'title': "Add authorized keys to deploy user",
                'command': 'mv /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys ',
            },
            {
                'title': 'Check deploy user file permissions',
                'command': '; '.join([
                    'chmod 0750 ~deploy',
                    'chmod 0700 ~deploy/.ssh',
                    'chmod 0600 ~deploy/.ssh/id_rsa',
                    'chmod 0644 ~deploy/.ssh/id_rsa.pub',
                    'chown deploy:webapps ~deploy/.ssh/authorized_keys',
                ]),
            },
            {
                'title': 'Remove sudo group rights',
                'command': "sed -i 's/^%sudo/# %sudo/' /etc/sudoers",
            },
            {
                'title': 'Enable the sudoers include',
                'command': "sed -i 's/^#includedir/includedir/' /etc/sudoers",
            },
            {
                'title': 'Add deploy user to sudoers',
                'command': 'echo "deploy ALL=(ALL:ALL) NOPASSWD: ALL" > /etc/sudoers.d/deploy',
            },
            {
                'title': 'Ensure the deploy sudoers file has the correct permissions',
                'command': 'chmod 0440 /etc/sudoers.d/deploy',
            },
            {
                'title': 'Disallow root SSH access',
                'command': "sed -i 's/^PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config",
            },
            {
                'title': 'Disallow password authentication',
                'command': "sed -i 's/^PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config",
            },
            {
                'title': 'Restart SSH',
                'command': 'service ssh restart',
            }
        ]
        run_tasks(env, ssh_tasks)

        # Define db tasks
        db_tasks = [
            {
                'title': "Ensure the PostgreSQL service is running",  # Why?
                'command': 'service postgresql start',
            },
            {
                'title': 'Modify the locales config',
                'command': '; '.join([
                    "sed -i 's/^# en_GB.UTF-8/en_GB.UTF-8/' /etc/locale.gen",  # Uncomment the GB line
                    "sed -i 's/^en_US.UTF-8/# en_US.UTF-8/' /etc/locale.gen",  # Comment out the US line
                ]),
            },
            {
                'title': "Generate locales",
                'command': 'locale-gen --purge',
            },
            {
                'title': 'Modify default locales',
                'command': "sed -i 's/en_US/en_GB/' /etc/default/locale",
            },
            {
                'title': 'Reconfigure locales',
                'command': 'LANG=en_GB.UTF-8 dpkg-reconfigure -f noninteractive locales',
            },
            {
                'title': "Restart PostgreSQL",  # Why?
                'command': 'service postgresql restart',
            },
            {
                'title': "Ensure database is created",
                'ansible_arguments': {
                    'module_name': 'postgresql_db',
                    'module_args': "name='{}' encoding='UTF-8' lc_collate='en_GB.UTF-8' lc_ctype='en_GB.UTF-8' "
                                   "template='template0' state=present".format(
                                       remote['database']['name'],
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
                        remote['database']['password'],
                    ),
                    'sudo_user': 'postgres'
                }
            },
            {
                'title': "Ensure user does not have unnecessary privileges",
                'ansible_arguments': {
                    'module_name': 'postgresql_user',
                    'module_args': 'name={} role_attr_flags=NOSUPERUSER,NOCREATEDB state=present'.format(
                        remote['database']['name'],
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
                        project_folder,
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
            module_args='cat /home/deploy/.ssh/id_rsa.pub',
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
                    bitbucket_repo,
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
                            bitbucket_repo,
                        ),
                        data=urlencode({
                            'label': 'Application Server ({})'.format(env.host_string),
                            'key': ssh_key,
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
                response = requests.post('https://api.github.com/repos/{}/{}/keys'.format(github_account, github_repo), json={
                    'title': 'Application Server ({})'.format(env.host_string),
                    'key': ssh_key,
                    'read_only': True,
                }, headers={
                    'Authorization': 'token {}'.format(github_token)
                })

                print response.text
            except Exception as e:
                print e.errors
                print "[\033[95mTASK\033[0m] [\033[91mFAILED\033[0m]"

        # Define git tasks
        if is_bitbucket_repo:
            git_url = "git@bitbucket.org:{}/{}.git".format(
                bitbucket_account,
                bitbucket_repo,
            )
        elif is_github_repo:
            git_url = 'git@github.com:{}/{}.git'.format(
                github_account,
                github_repo,
            )

        git_tasks = [
            {
                'title': "Setup the Git repo",
                'ansible_arguments': {
                    'module_name': 'git',
                    'module_args': 'repo={} dest={} version=develop accept_hostkey=yes ssh_opts="-o StrictHostKeyChecking=no"'.format(
                        git_url,
                        "/var/www/{}".format(
                            project_folder,
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
                        project_folder,
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
                        project_folder,
                    )
                }
            },
        ]
        run_tasks(env, static_tasks)

        # Define venv tasks
        venv_tasks = [
            {
                'title': "Create the virtualenv",
                'command': 'virtualenv /var/www/{project}/.venv --no-site-packages creates=/var/www/{project}/.venv'.format(,
                    )
                }
            },
            {
                'title': "Create the Gunicorn script file",
                'ansible_arguments': {
                    'module_name': 'copy',
                    'module_args': 'src={file} dest=/var/www/{project}/gunicorn_start owner={project} group=webapps mode=0755 backup=yes'.format(
                        file=session_files['gunicorn_start'].name,
                        project=project_folder,
                    )
                }
            },
            {
                'title': "Create the application log folder",
                'ansible_arguments': {
                    'module_name': 'file',
                    'module_args': 'path=/var/log owner={} group=webapps mode=0774 state=directory'.format(
                        project_folder,
                    )
                }
            },
            {
                'title': "Create the application log file",
                'command': 'touch /var/log/gunicorn_supervisor.log creates=/var/log/gunicorn_supervisor.log',
            },
            {
                'title': "Set permission to the application log file",
                'ansible_arguments': {
                    'module_name': 'file',
                    'module_args': 'path=/var/log/gunicorn_supervisor.log owner={} group=webapps mode=0664 state=file'.format(
                        project_folder,
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
                project_folder,
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
                            project=project_folder,
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
                        project=project_folder,
                    )
                }
            },
            {
                'title': "Update media permissions",
                'ansible_arguments': {
                    'module_name': 'file',
                    'module_args': 'path=/var/www/{project}_media/ owner={project} group=webapps recurse=yes'.format(
                        project=project_folder,
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
                        project=project_folder,
                    )
                }
            }
        ]
        run_tasks(env, permission_tasks)

        # Define nginx tasks
        nginx_tasks = [
            {
                'title': "Install Nginx and Certbot",
                'ansible_arguments': {
                    'module_name': 'apt',
                    'module_args': 'name={item} force=yes state=present'
                },
                'with_items': [
                    'nginx',
                    'certbot',
                    'python-certbot-nginx',
                ],
            },
            {
                'title': "Ensure Nginx service is stopped",  # This allows Certbot to run.
                'ansible_arguments': {
                    'module_name': 'service',
                    'module_args': 'name=nginx state=stopped enabled=yes'
                }
            },
            {
                'title': "Create the Nginx configuration file",
                'ansible_arguments': {
                    'module_name': 'copy',
                    'module_args': 'src={} dest=/etc/nginx/sites-available/{} backup=yes'.format(
                        session_files['nginx_site_config'].name,
                        project_folder,
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
                'command': 'rm /etc/nginx/sites-enabled/default removes=/etc/nginx/sites-enabled/default',
            },
            {
                'title': "Ensure that the application site is enabled",
                'command': 'ln -s /etc/nginx/sites-available/{project} /etc/nginx/sites-enabled/{project} creates=/etc/nginx/sites-enabled/{project}'.format(,
                    )
                }
            },
            {
                'title': 'Run certbot',
                'command': 'certbot certonly --standalone -n --agree-tos --email developers@onespacemedia.com --cert-name {} --domains {}'.format(,
                        ','.join(setup_ssl_for)
                    )
                }
            },
            {
                'title': 'Generate DH parameters (this may take a little while)',
                'command': 'openssl dhparam -out /etc/ssl/dhparam.pem 2048',
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

        # Configure the firewall.
        firewall_tasks = [
            {
                'title': 'Allow SSH connections through the firewall',
                'command': 'ufw allow OpenSSH'
            },
            {
                'title': 'Allow SSH connections through the firewall',
                'command': 'ufw allow "Nginx Full"'
            },
            {
                'title': 'Enable the firewall, deny all other traffic',
                'command': 'ufw --force enable',  # --force makes it non-interactive
            }
        ]

        run_tasks(env, firewall_tasks)

        # Define supervisor tasks
        supervisor_tasks = [
            {
                'title': "Create the Supervisor config file for the application",
                'ansible_arguments': {
                    'module_name': 'copy',
                    'module_args': 'src={} dest=/etc/supervisor/conf.d/{}.conf backup=yes'.format(
                        session_files['supervisor_config'].name,
                        project_folder,
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
                        session_files['memcached_supervisor_config'].name,
                    )
                }
            },
            {
                'title': "Re-read the Supervisor config files",
                'command': 'supervisorctl reread',
            },
            {
                'title': "Update Supervisor to add the app in the process group",
                'command': 'supervisorctl update',
            },
        ]
        run_tasks(env, supervisor_tasks)

        # Define build system tasks
        build_systems = {
            "none": [],
            "npm": [
                {
                    'title': 'Install nvm',
                    'ansible_arguments': {
                        'module_name': 'shell',
                        'module_args': 'sudo -H -u {project} bash -c "curl -o- https://raw.githubusercontent.com/creationix/nvm/v0.32.1/install.sh | bash" executable=/bin/bash'.format(
                            project=project_folder,
                        )
                    }
                },
                {
                    'title': 'Activate nvm then install node and yarn',
                    'ansible_arguments': {
                        'module_name': 'shell',
                        'module_args': 'sudo -H -u {project} bash -c ". ~/.nvm/nvm.sh && nvm install && npm install -g yarn" chdir=/var/www/{project} executable=/bin/bash'.format(
                            project=project_folder,
                        )
                    }
                },
                {
                    'title': 'Fix some permissions',
                    'ansible_arguments': {
                        'module_name': 'shell',
                        'module_args': 'chown {project}:webapps -R /var/www/*; chmod -R g+w /var/www/{project}*; chmod ug+rwX -R /var/www/{project}/.git executable=/bin/bash'.format(
                            project=project_folder,
                        )
                    }
                },
                {
                    'title': 'Install node packages',
                    'ansible_arguments': {
                        'module_name': 'shell',
                        'module_args': 'sudo -H -u {project} bash -c ". ~/.nvm/nvm.sh && yarn" chdir=/var/www/{project} executable=/bin/bash'.format(
                            project=project_folder,
                        )
                    }
                },
                {
                    'title': 'Initiate build',
                    'ansible_arguments': {
                        'module_name': 'shell',
                        'module_args': 'sudo -H -u {project} bash -c ". ~/.nvm/nvm.sh && cd /var/www/{project} && yarn run build" executable=/bin/bash'.format(
                            project=project_folder,
                        )
                    }
                },
                {
                    'title': 'Ensure static folder exists in project',
                    'ansible_arguments': {
                        'module_name': 'file',
                        'module_args': 'state=directory owner={project} group=webapps path=/var/www/{project}/{project}/static/'.format(
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
                            settings=remote['server'].get('settings_file', 'production'),
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
