import os

from django.conf import settings as django_settings

from ._core import ServerManagementBaseCommand, load_config


class Command(ServerManagementBaseCommand):
    def handle(self, *args, **options):
        # Load server config from project
        _, connection = load_config(options.get('remote', ''), debug=options.get('debug', False))

        connection.local('mkdir -p {}'.format(
            os.path.join(django_settings.MEDIA_ROOT, 'uploads')
        ))

        connection.local('mkdir -p {}'.format(
            django_settings.STATIC_ROOT
        ))

        connection.local('rsync --progress -av{} --exclude "assets/" --exclude "cache/" {}@{}:/var/www/{}_media/ {}'.format(
            '' if not connection.connect_kwargs.get('key_filename') else ' -e "ssh -i {}"'.format(
                os.path.expanduser(connection.connect_kwargs['key_filename']),  # Fixes an rsync bug with ~ paths.
            ),
            connection.user,
            connection.host,
            os.path.basename(django_settings.SITE_ROOT),
            django_settings.MEDIA_ROOT
        ))
