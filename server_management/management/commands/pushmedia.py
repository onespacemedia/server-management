import os
from django.conf import settings as django_settings
from fabric.api import env, hide, lcd, local, settings

from ._core import load_config, ServerManagementBaseCommand


class Command(ServerManagementBaseCommand):

    def __init__(self):
        super(Command, self).__init__()

    def handle(self, *args, **options):
        # Load server config from project
        load_config(env, options.get('remote', ''))

        # Set local project path
        local_project_path = django_settings.SITE_ROOT

        # Change into the local project folder
        with hide('output', 'running', 'warnings'):
            with lcd(local_project_path):
                project_folder = local(
                    "basename $( find {} -name 'wsgi.py' -not -path '*/.venv/*' -not -path '*/venv/*' | xargs -0 -n1 dirname )".format(
                        local_project_path
                    ), capture=True)

        with settings(warn_only=True):
            local('rsync --progress -O -av{} {}/ {}@{}:/var/www/{}_media/'.format(
                ' ' if not getattr(env, 'key_filename') else ' -e "ssh -i {}"'.format(
                    os.path.expanduser(env.key_filename),  # Fixes an rsync bug with ~ paths.
                ),
                django_settings.MEDIA_ROOT,
                env.user,
                env.host_string,
                project_folder,
            ))
