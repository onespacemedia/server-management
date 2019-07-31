import os

from django.conf import settings as django_settings

from ._core import ServerManagementBaseCommand, load_config, run_tasks, confirm, prompt


class Command(ServerManagementBaseCommand):

    def handle(self, *args, **options):
        # Load server config from project
        config, connection = load_config(options.get('remote', ''), config_user='root', debug=options.get('debug', False))
        remote = config['remotes'][config['remote_name']]

        if django_settings.DEBUG:
            print(
                "You're currently using your local settings file, you need use production instead.\n"
                "To use production settings pass `--settings={}` to the deploy command.".format(
                    os.getenv('DJANGO_SETTINGS_MODULE').replace('.local', '.production')
                )
            )
            exit()

        # Compress the domain names for nginx
        domain_names = ' '.join(django_settings.ALLOWED_HOSTS)

        # Use the site domain as a fallback domain
        fallback_domain_name = django_settings.SITE_DOMAIN

        if not options['noinput']:
            fallback_domain_name = prompt('What should the default domain be?', default=fallback_domain_name)
            domain_names = prompt('Which domains would you like to enable in nginx?', default=domain_names)
        else:
            print(f'Default domain: {fallback_domain_name}')
            print(f'Domains to be enabled in nginx: {domain_names}')

        # If the domain is pointing to the droplet already, we can setup SSL.
        setup_ssl_for = [
            domain_name
            for domain_name in domain_names.split(' ')
            if connection.local(f'dig +short {domain_name}', capture=True) == remote['server']['ip']
        ]

        if not setup_ssl_for:
            print(f"Sorry, it's $CURRENT_YEAR, you need to use SSL. Please update the domain DNS to point to {remote['server']['ip']}.")
            exit()

        for domain_name in domain_names.split(' '):
            if domain_name not in setup_ssl_for:
                print(f'SSL will not be configured for {domain_name}')

        if not options['noinput']:
            if not confirm('Do you want to continue?'):
                exit()

        # Define nginx tasks
        nginx_tasks = [
            {
                'title': 'Ensure Nginx service is stopped',  # This allows Certbot to run.
                'command': 'service nginx stop',
            },
            {
                'title': 'Run certbot',
                'command': 'certbot certonly --standalone -n --agree-tos --email developers@onespacemedia.com --cert-name {} --domains {}'.format(
                    fallback_domain_name,
                    ','.join(setup_ssl_for)
                ),
            },
            {
                'title': 'Ensure Nginx service is started',
                'command': 'service nginx start',
            },
        ]
        run_tasks(connection, nginx_tasks)
