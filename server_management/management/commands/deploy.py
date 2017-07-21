from __future__ import print_function

from getpass import getpass
import os
import re
from urllib.parse import urlencode

from django.conf import settings as django_settings
from django.core.files.temp import NamedTemporaryFile
from django.template.loader import render_to_string
from fabric.api import abort, env, hide, local, lcd, prompt, run, settings
import requests

from ._core import load_config, run_tasks, ServerManagementBaseCommand, title_print


class Command(ServerManagementBaseCommand):
    def handle(self, noinput, debug, remote='', *args, **options):
        # Load server config from project
        config, remote = load_config(env, remote, config_user='root', debug=debug)

        # Set local project path
        local_project_path = django_settings.SITE_ROOT

        print(os.getenv('DJANGO_SETTINGS_MODULE'))

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
        domain_names = " ".join(django_settings.ALLOWED_HOSTS)

        # Use the site domain as a fallback domain
        fallback_domain_name = django_settings.SITE_DOMAIN

        if not noinput:
            fallback_domain_name = prompt('What should the default domain be?', default=fallback_domain_name)
            domain_names = prompt('Which domains would you like to enable in nginx?', default=domain_names)
        else:
            print('Default domain: ', fallback_domain_name)
            print('Domains to be enabled in nginx: ', domain_names)

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
                print('SSL will not be configured for {}'.format(domain_name))

        # Override username (for DO hosts).
        if env.user == 'deploy':
            env.user = 'root'

        # Print some information for the user
        print('')
        print('Project: {}'.format(project_folder))
        print('Server IP: {}'.format(env.host_string))
        print('Server user: {}'.format(env.user))
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

        circle_token = os.environ.get('CIRCLE_TOKEN', None)

        print("")

        # Create session_files
        session_files = {
            'gunicorn_start': NamedTemporaryFile(mode='w+', delete=False),
            'supervisor_config': NamedTemporaryFile(mode='w+', delete=False),
            'memcached_supervisor_config': NamedTemporaryFile(mode='w+', delete=False),
            'nginx_site_config': NamedTemporaryFile(mode='w+', delete=False),
            'apt_periodic': NamedTemporaryFile(mode='w+', delete=False),
            'certbot_cronjob': NamedTemporaryFile(mode='w+', delete=False),
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
        python_version = python_version_full[0]
        pip_command = 'pip{}'.format(3 if python_version == '3' else '')
        python_command = 'python{}'.format(python_version_full)
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
                        'ufw',  # Installed by default on Ubuntu, not elsewhere

                        # Project requirements
                        '{}-dev'.format(python_command),
                        'python{}-pip'.format('3' if python_version == '3' else ''),
                        'apache2-utils',  # Required for htpasswd
                        'python{}-passlib'.format('3' if python_version == '3' else ''),  # Required for generating the htpasswd file
                        'supervisor',
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
                        'python{}-psycopg2'.format(3 if python_version == '3' else ''),  # TODO: Is this required?

                        # Required under Python 3.
                        'python3-venv' if python_version == '3' else '',

                        # Other
                        'libgeoip-dev' if optional_packages.get('geoip', True) else '',
                        'libmysqlclient-dev' if optional_packages.get('mysql', True) else '',
                        'python3.6' if python_version_full == '3.6' else '',
                        'python3.6-dev' if python_version_full == '3.6' else '',
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
                'command': '{} install -U pip'.format(pip_command),
            },
            {
                'title': 'Install virtualenv',
                'command': '{} install virtualenv'.format(pip_command),
            },
            {
                'title': 'Set the timezone to UTC',
                'command': 'timedatectl set-timezone UTC',
            }
        ]

        if python_version_full == '3.6':
            base_tasks.insert(0, {
                'title': 'Add Python 3.6 PPA',
                'command': 'add-apt-repository -y ppa:jonathonf/python-3.6',
            })

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
                'command': 'adduser --shell /bin/bash --system --disabled-password --ingroup webapps {name}'.format(
                    name=project_folder,
                ),
            },
            {
                'title': "Add .ssh folder to application user's home directory",
                'command': 'mkdir ~{}/.ssh'.format(project_folder),
            },
            {
                'title': 'Generate SSH keys for application user',
                'command': "ssh-keygen -C application-server -f ~{}/.ssh/id_rsa -N ''".format(
                    project_folder,
                )
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
                'command': 'mv ~{}/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys'.format(
                    env.user,
                ),
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
        db_tasks = [
            {
                'title': 'Create the application postgres role',
                'command': 'su - postgres -c "createuser {name}"'.format(
                    name=remote['database']['name'],
                ),
            },
            {
                'title': 'Ensure database is created',
                'command': 'su - postgres -c "createdb {name} --encoding=UTF-8 --locale=en_GB.UTF-8 '
                           '--template=template0 --owner={owner} --no-password"'.format(
                    name=remote['database']['name'],
                    owner=remote['database']['user'],
                ),
            },
            {
                'title': 'Ensure user has access to the database',
                'command': 'su - postgres -c "psql {name} -c \'GRANT ALL ON DATABASE {name} TO {owner}\'"'.format(
                    name=remote['database']['name'],
                    owner=remote['database']['user'],
                ),
            },
            {
                'title': 'Ensure user does not have unnecessary privileges',
                'command': 'su - postgres -c "psql {name} -c \'ALTER USER {owner} WITH NOSUPERUSER '
                           'NOCREATEDB\'"'.format(
                    name=remote['database']['name'],
                    owner=remote['database']['user'],
                ),
            },
        ]
        run_tasks(env, db_tasks)

        # Get SSH Key from server
        ssh_key = run('cat ~{}/.ssh/id_rsa.pub'.format(project_folder))

        # Get the current SSH keys in the repo
        if is_bitbucket_repo:
            task_title = 'Checking bitbucket repository for an existing SSH key'

            title_print(task_title, state='task')

            try:
                repo_ssh_keys = requests.get('https://bitbucket.org/api/1.0/repositories/{}/{}/deploy-keys/'.format(
                    bitbucket_account,
                    bitbucket_repo,
                ), auth=(bitbucket_username, bitbucket_password))
            except:
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
                            'label': 'Application Server ({})'.format(env.host_string),
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
                response = requests.post('https://api.github.com/repos/{}/{}/keys'.format(github_account, github_repo),
                                         json={
                                             'title': 'Application Server ({})'.format(env.host_string),
                                             'key': ssh_key,
                                             'read_only': True,
                                         }, headers={
                        'Authorization': 'token {}'.format(github_token)
                    })

                if debug:
                    print(response.text)
            except Exception as e:
                title_print(task_title, state='failed')
                raise e

            title_print(task_title, state='succeeded')

        # Define git tasks
        if is_bitbucket_repo:
            git_url = 'git@bitbucket.org:{}/{}.git'.format(
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
                'title': 'Add Github key to known hosts',
                'command': 'ssh-keyscan -H github.com >> ~{project}/.ssh/known_hosts'.format(
                    project=project_folder,
                ),
            },
            {
                'title': 'Setup the Git repo',
                'command': 'cd /tmp; git clone {url} {project}'.format(
                    url=git_url,
                    project='/var/www/{}'.format(
                        project_folder,
                    )
                ),
            },
        ]
        run_tasks(env, git_tasks, user=project_folder)

        # Define static tasks
        static_tasks = [
            {
                'title': 'Make the static directory',
                'command': '; '.join([
                    'mkdir -m 0775 -p {dir}',
                    'chown {project}:webapps {dir}',
                ]).format(
                    project=project_folder,
                    dir=django_settings.STATIC_ROOT,
                ),
            },
            {
                'title': 'Make the media directory',
                'command': '; '.join([
                    'mkdir -m 0775 -p {dir}',
                    'chown {project}:webapps {dir}'
                ]).format(
                    project=project_folder,
                    dir=django_settings.MEDIA_ROOT,
                ),
            },
        ]
        run_tasks(env, static_tasks)

        virtualenv_command = (
            'virtualenv -p python{python_full} /var/www/{project}/.venv'
        )
        # Define venv tasks
        venv_tasks = [
            {
                'title': 'Create the virtualenv',
                'command': virtualenv_command.format(
                    python_full=python_version_full,
                    project=project_folder,
                ),
            },
            # This shouldn't be necessary (we think we upgraded pip earlier)
            # but it is - you'll get complaints about bdist_wheel without
            # this.
            {
                'title': 'Upgrade pip inside the virtualenv',
                'command': '/var/www/{project}/.venv/bin/pip install --upgrade pip'.format(
                    project=project_folder,
                ),
            },
        ]
        run_tasks(env, venv_tasks, user=project_folder)

        gunicorn_tasks = [
            {
                'title': 'Create the Gunicorn script file',
                'fabric_command': 'put',
                'fabric_args': [session_files['gunicorn_start'].name, '/var/www/{project}/gunicorn_start'.format(
                    project=project_folder,
                )],
            },
            {
                'title': 'Make the Gunicorn script file executable',
                'command': 'chmod +x /var/www/{project}/gunicorn_start'.format(
                    project=project_folder,
                )
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
                    'chown {}:webapps /var/log/gunicorn_supervisor.log'.format(
                        project_folder,
                    ),
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

                'title': "Install packages required by the Django app inside virtualenv",
                'command': 'if [ -f /var/www/{project}/requirements.txt ]; then /var/www/{project}/.venv/bin/pip '
                           'install -r /var/www/{project}/requirements.txt; fi'.format(
                    project=project_folder,
                ),
            },
            {
                'title': 'Make sure Gunicorn is installed',
                'command': '/var/www/{project}/.venv/bin/pip install gunicorn'.format(
                    project=project_folder,
                ),
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
                'title': 'Create the Nginx configuration file',
                'fabric_command': 'put',
                'fabric_args': [session_files['nginx_site_config'].name, '/etc/nginx/sites-available/{}'.format(
                    project_folder,
                )],
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
                'title': 'Ensure that the application site is enabled',
                'command': 'ln -s /etc/nginx/sites-available/{project} /etc/nginx/sites-enabled/{project}'.format(
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
                'title': 'Create the Supervisor config file for the application',
                'fabric_command': 'put',
                'fabric_args': [session_files['supervisor_config'].name, '/etc/supervisor/conf.d/{}.conf'.format(
                    project_folder,
                )],
            },
            {
                'title': 'Stopping memcached and removing from startup runlevels',
                'command': '; '.join([
                    'service memcached stop',
                    'systemctl disable memcached',
                ]),
            },
            {
                'title': 'Create the Supervisor config file for memcached',
                'fabric_command': 'put',
                'fabric_args': [session_files['memcached_supervisor_config'].name,
                                '/etc/supervisor/conf.d/memcached.conf'],
            },
            {
                'title': 'Re-read the Supervisor config files',
                'command': 'supervisorctl reread',
            },
            {
                'title': 'Update Supervisor to add the app in the process group',
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
                    'command': 'if [ ! -d "/var/www/{project}/{project}/static/" ]; then mkdir /var/www/{project}/{'
                               'project}/static/; fi'.format(
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
                'fabric_args': ['mkdir dist; ssh-keygen -C circleci -f dist/id_rsa -N '''],
            },
            {
                'title': 'Follow the project on CircleCI',
                'fabric_command': 'local',
                'fabric_args': [
                    'curl -X POST https://circleci.com/api/v1.1/project/github/{github_account}/{'
                    'github_repo}/follow?circle-token={circle_token}'.format(
                        github_account=github_account,
                        github_repo=github_repo,
                        circle_token=circle_token,
                    )]
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
