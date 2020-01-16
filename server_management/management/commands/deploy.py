from __future__ import print_function

import os
import re
from getpass import getpass
from urllib.parse import urlencode

import requests
from django.conf import settings as django_settings
from django.core.files.temp import NamedTemporaryFile
from django.template.loader import render_to_string
from fabric.api import abort, env, hide, lcd, local, prompt, run, settings

from ._core import (ServerManagementBaseCommand, load_config, run_tasks,
                    title_print)


class Command(ServerManagementBaseCommand):

    # This is a complicated method which is vastly overloaded.  To improve it in
    # the future we could look to moving each individual block of actions into
    # either their own methods, or into their own files, which are then registered
    # with the deployment system.

    def handle(self, *args, **options):  # pylint: disable=too-complex,too-many-locals,too-many-branches,too-many-statements
        # Load server config from project
        config, remote = load_config(env, options.get('remote', ''), config_user='root', debug=options.get('debug', False))

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
                        raise Exception('That is not a valid choice.')

                    choice = prompt('Which Git remote would you like to use?', validate=validate_choice)
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
                        raise Exception('Unable to determine Bitbucket details.')

                elif is_github_repo:
                    gh_regex = re.match(r'(?:git@|https://)github.com[:/]([\w\-]+)/([\w\-.]+)\.git$', git_remote)

                    if gh_regex:
                        github_account = gh_regex.group(1)
                        github_repo = gh_regex.group(2)
                    else:
                        raise Exception('Unable to determine Github details.')
                else:
                    raise Exception('Unable to determine Git host from remote URL: {}'.format(git_remote))

                project_folder = local_project_path.replace(
                    os.path.abspath(os.path.join(local_project_path, '..')) + '/', '')

                with settings(warn_only=True):
                    if local('[[ -e ../requirements.txt ]]').return_code:
                        raise Exception("No requirements.txt")

        # Compress the domain names for nginx
        production_domain_names = ' '.join([
            host for host in
            django_settings.ALLOWED_HOSTS
            if '.onespace.media' not in host
        ])
        staging_domain_names = ' '.join([
            host for host in
            django_settings.ALLOWED_HOSTS
            if '.onespace.media' in host
        ])

        # Use the site domain as a fallback domain
        fallback_domain_name = django_settings.SITE_DOMAIN

        if not options.get('noinput', False):
            fallback_domain_name = prompt('What should the default domain be?', default=fallback_domain_name)
            production_domain_names = prompt('Which domains would you like to enable in the PRODUCTION nginx config?', default=production_domain_names)
            staging_domain_names = prompt('Which domains would you like to enable in the STAGING nginx config?', default=staging_domain_names)
        else:
            print('Default domain: ', fallback_domain_name)
            print('Production domains to be enabled in nginx: ', production_domain_names)
            print('Staging domains to be enabled in nginx: ', staging_domain_names)

        # If the domain is pointing to the droplet already, we can setup SSL.
        setup_ssl_for = [
            domain_name
            for domain_name in staging_domain_names.split(' ')
            if local(f'dig +short {domain_name}', capture=True) == remote['server']['ip']
        ]

        if not setup_ssl_for:
            abort("None of the supplied domain names are pointing to the server IP, which means SSL cannot be configured (it's required). Please update the domain DNS to point to {}.".format(
                remote['server']['ip']
            ))

        for domain_name in staging_domain_names.split(' '):
            if domain_name not in setup_ssl_for:
                print(f'SSL will not be configured for {domain_name}')

        # Override username (for DO hosts).
        if env.user == 'deploy':
            env.user = 'root'

        # Print some information for the user
        print('')
        print(f'Project: {project_folder}')
        print(f'Server IP: {env.host_string}')
        print(f'Server user: {env.user}')
        print('')

        # Get BitBucket / Github details

        if is_bitbucket_repo:
            if os.environ.get('BITBUCKET_USERNAME', False) and os.environ.get('BITBUCKET_PASSWORD', False):
                bitbucket_username = os.environ.get('BITBUCKET_USERNAME')
                bitbucket_password = os.environ.get('BITBUCKET_PASSWORD')
            else:
                bitbucket_username = prompt('Please enter your BitBucket username:')
                bitbucket_password = getpass('Please enter your BitBucket password: ')
        elif is_github_repo:
            if os.environ.get('GITHUB_TOKEN', False):
                github_token = os.environ.get('GITHUB_TOKEN')
            else:
                github_token = prompt(
                    'Please enter your Github token (obtained from https://github.com/settings/tokens):')

        circle_token = os.environ.get('CIRCLE_TOKEN', '')

        print("")

        # Create session_files
        session_files = {
            'supervisor_config': NamedTemporaryFile(mode='w+', delete=False),
            'supervisor_init': NamedTemporaryFile(mode='w+', delete=False),
            'nginx_production': NamedTemporaryFile(mode='w+', delete=False),
            'nginx_staging': NamedTemporaryFile(mode='w+', delete=False),
            'apt_periodic': NamedTemporaryFile(mode='w+', delete=False),
            'certbot_cronjob': NamedTemporaryFile(mode='w+', delete=False),
        }

        # Parse files
        session_files['supervisor_config'].write(render_to_string('supervisor_config', {
            'project': project_folder
        }))
        session_files['supervisor_config'].close()

        session_files['supervisor_init'].write(render_to_string('supervisor_init', {
            'project': project_folder
        }))
        session_files['supervisor_init'].close()

        # Production nginx config
        session_files['nginx_production'].write(render_to_string('nginx_production', {
            'project': project_folder,
            'domain_names': production_domain_names,
            'fallback_domain_name': fallback_domain_name
        }))
        session_files['nginx_production'].close()

        # Staging nginx config
        session_files['nginx_staging'].write(render_to_string('nginx_staging', {
            'project': project_folder,
            'domain_names': staging_domain_names,
            'fallback_domain_name': fallback_domain_name
        }))
        session_files['nginx_staging'].close()

        session_files['apt_periodic'].write(render_to_string('apt_periodic'))
        session_files['apt_periodic'].close()

        session_files['certbot_cronjob'].write(render_to_string('certbot_cronjob'))
        session_files['certbot_cronjob'].close()

        # Define the locales first.
        locale_tasks = [
            {
                'title': 'Modify the locales config',
                'command': '; '.join([
                    "sed -i 's/^# en_GB.UTF-8/en_GB.UTF-8/' /etc/locale.gen",  # Uncomment the GB line
                ]),
            },
            {
                'title': 'Generate locales',
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
        ]

        run_tasks(env, locale_tasks)

        # Check if optional packages are defined in the config.
        optional_packages = {}

        if 'optional_packages' in config:
            optional_packages = config['optional_packages']

        python_version_full = remote['server'].get('python_version', '3')
        pip_command = 'pip3'
        python_command = f'python{python_version_full}'

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
                'command': 'apt-get upgrade -yq',
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
                        'ufw',  # Installed by default on Ubuntu, not elsewhere

                        # Project requirements
                        f'{python_command}-dev',
                        'python-pip',  # For supervisor
                        'python3-pip',
                        'apache2-utils',  # Required for htpasswd
                        'python3-passlib',  # Required for generating the htpasswd file
                        'libjpeg-dev',
                        'libffi-dev',
                        'libssl-dev',  # Required for nvm.
                        'nodejs',
                        'memcached',
                        'fail2ban',

                        # Nginx things
                        'nginx',
                        'certbot',
                        'python-certbot-nginx',

                        # Postgres requirements
                        'postgresql',
                        'libpq-dev',
                        'python3-psycopg2',  # TODO: Is this required?

                        # Other
                        'libgeoip-dev' if optional_packages.get('geoip', True) else '',
                        'libmysqlclient-dev' if optional_packages.get('mysql', True) else '',
                        'python3.6',
                        'python3.6-dev',
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
                'command': f'{pip_command} install -U pip'
            },
            {
                'title': 'Install virtualenv',
                'command': f'{pip_command} install virtualenv',
            },
            {
                'title': 'Install supervisor',
                'command': f'pip2 install supervisor',
            },
            {
                'title': 'Set the timezone to UTC',
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

        # Check to see if we've already configured a swap file. This handles
        # the case where the deploy command is being re-run.
        cache_pressure = run('cat /proc/sys/vm/vfs_cache_pressure')

        if cache_pressure != '50':
            run_tasks(env, swap_tasks)

        # Define SSH tasks
        ssh_tasks = [
            {
                'title': 'Create the application group',
                'command': 'addgroup --system webapps',
            },
            {
                'title': 'Add the application user',
                'command': f'adduser --shell /bin/bash --system --disabled-password --ingroup webapps {project_folder}',
            },
            {
                'title': "Add .ssh folder to application user's home directory",
                'command': f'mkdir ~{project_folder}/.ssh',
            },
            {
                'title': 'Generate SSH keys for application user',
                'command': f"ssh-keygen -C application-server -f ~{project_folder}/.ssh/id_rsa -N ''",
            },
            {
                'title': 'Make the application directory',
                'command': '; '.join([
                    'mkdir -m 0775 -p /var/www/{project}',
                    'chown {project}:webapps /var/www/{project}',
                ]).format(
                    project=project_folder,
                ),
            },
            {
                'title': 'Check application user file permissions',
                'command': '&& '.join([
                    'chmod 0750 ~{project}',
                    'chmod 0700 ~{project}/.ssh',
                    'chmod 0600 ~{project}/.ssh/id_rsa',
                    'chmod 0644 ~{project}/.ssh/id_rsa.pub',
                    'chown -R {project}:webapps ~{project}',
                ]).format(
                    project=project_folder,
                ),
            },
            {
                'title': 'Add deploy user',
                'command': 'adduser --shell /bin/bash --disabled-password --system --ingroup webapps deploy',
            },
            {
                'title': "Add .ssh folder to deploy user's home directory",
                'command': 'mkdir ~deploy/.ssh',
            },
            {
                'title': 'Add authorized keys to deploy user',
                'command': f'mv ~{env.user}/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys',
            },
            {
                'title': 'Check deploy user file permissions',
                'command': '; '.join([
                    'chmod 0750 ~deploy',
                    'chmod 0700 ~deploy/.ssh',
                    'chmod 0644 ~deploy/.ssh/authorized_keys',
                    'chown -R deploy:webapps ~deploy',
                ]),
            },
            {
                'title': 'Remove sudo group rights',
                'command': "sed -i 's/^%sudo/# %sudo/' /etc/sudoers",
            },
            {
                'title': 'Add deploy user to sudoers',
                'command': 'echo "deploy ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/deploy',
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
        db_name = remote['database']['name']
        db_user = remote['database']['user']

        db_tasks = [
            {
                'title': 'Create the application postgres role',
                'command': f'su - postgres -c "createuser {db_user}"',
            },
            {
                'title': 'Ensure database is created',
                'command': f'su - postgres -c "createdb {db_name} --encoding=UTF-8 --locale=en_GB.UTF-8 --template=template0 --owner={db_user} --no-password"',
            },
            {
                'title': 'Ensure user has access to the database',
                'command': f'su - postgres -c "psql {db_name} -c \'GRANT ALL ON DATABASE {db_name} TO {db_user}\'"',
            },
            {
                'title': 'Ensure user does not have unnecessary privileges',
                'command': f'su - postgres -c "psql {db_name} -c \'ALTER USER {db_user} WITH NOSUPERUSER NOCREATEDB\'"',
            },
        ]
        run_tasks(env, db_tasks)

        # Get SSH Key from server
        ssh_key = run(f'cat ~{project_folder}/.ssh/id_rsa.pub')

        # Get the current SSH keys in the repo
        if is_bitbucket_repo:
            task_title = 'Checking bitbucket repository for an existing SSH key'

            title_print(task_title, state='task')

            try:
                repo_ssh_keys = requests.get('https://bitbucket.org/api/1.0/repositories/{}/{}/deploy-keys/'.format(
                    bitbucket_account,
                    bitbucket_repo,
                ), auth=(bitbucket_username, bitbucket_password))
            except requests.RequestException:
                title_print(task_title, state='failed')
                exit()

            title_print(task_title, state='succeeded')

            task_title = 'Adding the SSH key to bitbucket'

            if repo_ssh_keys.text.find(ssh_key) == -1:
                title_print(task_title, state='task')

                try:
                    requests.post(
                        'https://bitbucket.org/api/1.0/repositories/{}/{}/deploy-keys/'.format(
                            bitbucket_account,
                            bitbucket_repo,
                        ),
                        data=urlencode({
                            'label': f'Application Server ({env.host_string})',
                            'key': ssh_key,
                        }),
                        auth=(bitbucket_username, bitbucket_password)
                    )
                except Exception as e:
                    title_print(task_title, state='failed')
                    raise e

                title_print(task_title, state='succeeded')

        elif is_github_repo:
            task_title = 'Adding the SSH key to Github'

            title_print(task_title, state='task')

            try:
                response = requests.post(
                    f'https://api.github.com/repos/{github_account}/{github_repo}/keys',
                    json={
                        'title': f'Application Server ({env.host_string})',
                        'key': ssh_key,
                        'read_only': True,
                    },
                    headers={
                        'Authorization': f'token {github_token}',
                    })

                if options.get('debug', False):
                    print(response.text)
            except Exception as e:
                title_print(task_title, state='failed')
                raise e

            title_print(task_title, state='succeeded')

        # Define git tasks
        if is_bitbucket_repo:
            git_url = f'git@bitbucket.org:{bitbucket_account}/{bitbucket_repo}.git'
        elif is_github_repo:
            git_url = f'git@github.com:{github_account}/{github_repo}.git'

        git_branch = local('git symbolic-ref --short HEAD', capture=True)

        git_tasks = [
            {
                'title': 'Add Github key to known hosts',
                'command': f'ssh-keyscan -H github.com >> ~{project_folder}/.ssh/known_hosts',
            },
            {
                'title': 'Setup the Git repo',
                'command': 'cd /tmp; git clone -b {branch} {url} {project}'.format(
                    branch=git_branch,
                    url=git_url,
                    project=f'/var/www/{project_folder}',
                ),
            },
        ]
        run_tasks(env, git_tasks, user=project_folder)

        # Define static tasks
        static_tasks = [
            {
                'title': 'Make the static directory',
                'command': '; '.join([
                    f'mkdir -m 0775 -p {django_settings.STATIC_ROOT}',
                    f'chown {project_folder}:webapps {django_settings.STATIC_ROOT}',
                ]),
            },
            {
                'title': 'Make the media directory',
                'command': '; '.join([
                    f'mkdir -m 0775 -p {django_settings.MEDIA_ROOT}',
                    f'chown {project_folder}:webapps {django_settings.MEDIA_ROOT}'
                ]),
            },
        ]
        run_tasks(env, static_tasks)

        # Define venv tasks
        git_hash = run(f'cd /var/www/{project_folder}; git rev-parse --short HEAD')
        venv_path = f'/var/www/{project_folder}/.venv-{git_hash}'

        venv_tasks = [
            {
                'title': 'Create the virtualenv for this commit',
                'command': f'virtualenv -p python{python_version_full} {venv_path}',
            },
            {
                'title': 'Symlink the .venv folder to the commit venv',
                'command': f'ln -s {venv_path} /var/www/{project_folder}/.venv',
            },
            # This shouldn't be necessary (we think we upgraded pip earlier)
            # but it is - you'll get complaints about bdist_wheel without
            # this.
            {
                'title': 'Upgrade pip inside the virtualenv',
                'command': f'/var/www/{project_folder}/.venv/bin/pip install --upgrade pip',
            },
        ]
        run_tasks(env, venv_tasks, user=project_folder)

        gunicorn_tasks = [
            {
                'title': 'Make the Gunicorn script file executable',
                'command': f'chmod +x /var/www/{project_folder}/gunicorn_start',
            },
            {
                'title': 'chown the Gunicorn script file',
                'command': 'chown {project}:webapps /var/www/{project}/gunicorn_start'.format(
                    project=project_folder,
                )
            },
        ]
        run_tasks(env, gunicorn_tasks)

        log_tasks = [
            {
                'title': 'Create the application log file',
                'command': '; '.join([
                    'touch /var/log/gunicorn_supervisor.log',
                    f'chown {project_folder}:webapps /var/log/gunicorn_supervisor.log',
                    'chmod 0644 /var/log/gunicorn_supervisor.log',
                ]),
            },
        ]
        run_tasks(env, log_tasks)

        requirement_tasks = [
            {
                # Check to see if we have a requirements file. Even though we check for
                # it at the start of the deployment process, it hasn't necessarily been
                # committed. So this check covers that.

                # We cd to /tmp/ because git lines in the requirements files breaks things.

                'title': "Install packages required by the Django app inside virtualenv",
                'command': 'if [ -f /var/www/{project}/requirements.txt ]; then cd /tmp; /var/www/{project}/.venv/bin/pip '
                           'install -r /var/www/{project}/requirements.txt; fi'.format(
                               project=project_folder,
                           ),
            },
            {
                'title': 'Make sure Gunicorn is installed',
                'command': f'/var/www/{project_folder}/.venv/bin/pip install gunicorn',
            },
        ]

        run_tasks(env, requirement_tasks, user=project_folder)

        # Define nginx tasks
        nginx_tasks = [
            {
                'title': 'Ensure Nginx service is stopped',  # This allows Certbot to run.
                'command': 'service nginx stop',
            },
            {
                'title': 'Create the production Nginx configuration file',
                'fabric_command': 'put',
                'fabric_args': [
                    session_files['nginx_production'].name,
                    f'/etc/nginx/sites-available/{project_folder}_production',
                ],
            },
            {
                'title': 'Create the staging Nginx configuration file',
                'fabric_command': 'put',
                'fabric_args': [
                    session_files['nginx_staging'].name,
                    f'/etc/nginx/sites-available/{project_folder}_staging',
                ],
            },
            {
                'title': 'Create the .htpasswd file',
                'command': 'htpasswd -c -b /etc/nginx/htpasswd onespace media',
            },
            {
                'title': 'Ensure that the default site is disabled',
                'command': 'rm /etc/nginx/sites-enabled/default',
            },
            {
                'title': 'Ensure that the production Nginx config is enabled',
                'command': 'ln -s /etc/nginx/sites-available/{project}_production /etc/nginx/sites-enabled/{project}_production'.format(
                    project=project_folder,
                ),
            },
            {
                'title': 'Ensure that the staging Nginx config is enabled',
                'command': 'ln -s /etc/nginx/sites-available/{project}_staging /etc/nginx/sites-enabled/{project}_staging'.format(
                    project=project_folder,
                ),
            },
            {
                'title': 'Run certbot',
                'command': 'certbot certonly --standalone -n --agree-tos --email developers@onespacemedia.com '
                           '--cert-name {} --domains {}'.format(
                               fallback_domain_name,
                               ','.join(setup_ssl_for)
                           ),
            },
            {
                'title': 'Generate DH parameters (this may take a little while)',
                'command': 'openssl dhparam -out /etc/ssl/dhparam.pem 2048',
            },
            {
                'title': 'Ensure Nginx service is started',
                'command': 'service nginx start',
            },
            {
                'title': 'Configure certbot cronjob',
                'fabric_command': 'put',
                'fabric_args': [session_files['certbot_cronjob'].name, '/etc/cron.d/certbot'],
            },
            {
                'title': 'Ensure the certbot cronjob has the correct file permissions',
                'command': 'chmod 0644 /etc/cron.d/certbot',
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
                'title': 'Create the Supervisor config folder',
                'command': 'sudo mkdir /etc/supervisor',
            },
            {
                'title': 'Create the Supervisor config file',
                'fabric_command': 'put',
                'fabric_args': [
                    session_files['supervisor_config'].name,
                    '/etc/supervisor/supervisord.conf',
                ],
            },
            {
                'title': 'Create the Supervisor init script',
                'fabric_command': 'put',
                'fabric_args': [
                    session_files['supervisor_init'].name,
                    '/etc/init.d/supervisord',
                ],
            },
            {
                'title': 'Make the Supervisor init script executable',
                'command': 'chmod +x /etc/init.d/supervisord',
            },
            {
                'title': 'Add Supervisor to the list of services',
                'command': 'update-rc.d supervisord defaults',
            },
            {
                'title': 'Create supervisor log directory',
                'command': 'mkdir -p /var/log/supervisor/',
            },
            {
                'title': 'Stopping memcached and removing from startup runlevels',
                'command': '; '.join([
                    'service memcached stop',
                    'systemctl disable memcached',
                ]),
            },
            {
                'title': 'Start Supervisor',
                'command': 'service supervisord start',
            },
        ]
        run_tasks(env, supervisor_tasks)

        # Define build system tasks
        build_systems = {
            "none": [],
            "npm": [
                {
                    'title': 'Create .bashrc file',
                    'command': f'touch ~{project_folder}/.bashrc',
                },
                {
                    'title': 'Install nvm',
                    'command': 'cd /tmp; curl -o- https://raw.githubusercontent.com/creationix/nvm/v0.32.1/install.sh'
                               ' | bash',
                },
                {
                    'title': 'Activate nvm then install node and yarn',
                    'command': '&& '.join([
                        'cd /var/www/{project}',
                        '. ~{project}/.nvm/nvm.sh',
                        'nvm install',
                        'npm install -g yarn',
                        'yarn',
                        'yarn run build',
                    ]).format(
                        project=project_folder,
                    ),
                },
                {
                    'title': 'Ensure static folder exists in project',
                    'command': 'if [ ! -d "/var/www/{project}/{project}/static/" ]; then mkdir /var/www/{project}/{project}/static/; fi'.format(
                        project=project_folder,
                    ),
                },
                {
                    'title': 'Collect static files',
                    'command': '/var/www/{project}/.venv/bin/python /var/www/{project}/manage.py collectstatic '
                               '--noinput --link --settings={project}.settings.{settings}'.format(
                                   project=project_folder,
                                   settings=remote['server'].get('settings_file', 'production'),
                               ),
                }
            ]
        }

        run_tasks(env, build_systems[remote['server'].get('build_system', 'none')], user=project_folder)

        # Delete files
        for session_file in session_files:
            os.unlink(session_files[session_file].name)

        # Add the project to CircleCI
        circle_tasks = [
            {
                'title': 'Create the CircleCI SSH key',
                'fabric_command': 'local',
                'fabric_args': ["mkdir -p dist; ssh-keygen -C circleci -f dist/id_rsa -N ''"],
            },
            {
                'title': 'Follow the project on CircleCI',
                'fabric_command': 'local',
                'fabric_args': [
                    f'curl -X POST https://circleci.com/api/v1.1/project/github/{github_account}/{github_repo}/follow?circle-token={circle_token}',
                ]
            },
            {
                'title': 'Add private SSH key to CircleCI',
                'fabric_command': 'local',
                'fabric_args': [
                    'curl -X POST --header "Content-Type: application/json" -d \'{{"hostname":"{'
                    'fallback_domain_name}","private_key":"{private_key}"}}\' '
                    'https://circleci.com/api/v1.1/project/github/{github_account}/{'
                    'github_repo}/ssh-key?circle-token={circle_token}'.format(
                        fallback_domain_name=fallback_domain_name,
                        private_key=open('dist/id_rsa', 'r').read(),
                        github_account=github_account,
                        github_repo=github_repo,
                        circle_token=circle_token,
                    )]
            },
            {
                'title': 'Add public key to server',
                'command': 'echo "{}" >> ~deploy/.ssh/authorized_keys'.format(
                    open('dist/id_rsa.pub', 'r').read()
                )
            },
        ]

        if circle_token and is_github_repo:
            run_tasks(env, circle_tasks)

        print('Initial application deployment has completed. You should now pushdb and pushmedia.')
