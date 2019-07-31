import os

from django.conf import settings as django_settings

from ._core import ServerManagementBaseCommand, load_config


class Command(ServerManagementBaseCommand):

    def handle(self, *args, **options):
        # Load server config from project
        _, connection = load_config(options.get('remote', ''), debug=options.get('debug', False))

        connection.local('rsync --rsync-path="sudo -u {} rsync" --progress --exclude "cache/" -O -av{} {}/ {}@{}:/var/www/{}_media/'.format(
            os.path.basename(django_settings.SITE_ROOT),
            '' if not connection.connect_kwargs.get('key_filename') else ' -e "ssh -i {}"'.format(
                os.path.expanduser(connection.connect_kwargs['key_filename']),  # Fixes an rsync bug with ~ paths.
            ),
            django_settings.MEDIA_ROOT,
            connection.user,
            connection.host,
            os.path.basename(django_settings.SITE_ROOT),
        ))
