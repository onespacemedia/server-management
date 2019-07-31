import json
import os

from django.conf import settings
from django.core.management import call_command

from server_management.management.commands._core import ServerManagementBaseCommand, title_print, get_remote


class Command(ServerManagementBaseCommand):

    def handle(self, *args, **options):
        # Load the json file
        try:
            with open(os.path.join(settings.SITE_ROOT, 'server.json'), 'r', encoding='utf-8') as json_data:
                config = json.load(json_data)
        except Exception as e:
            print(e)
            raise Exception('Something is wrong with the server.json file, make sure it exists and is valid JSON.')

        remote_prompt = get_remote(options.get('remote', ''), config)

        title_print('Pulling database', 'task')
        call_command('pulldb', remote=remote_prompt)
        title_print('Pulling database', 'succeeded')

        title_print('Pulling media', 'task')
        call_command('pullmedia', remote=remote_prompt)
        title_print('Pulling media', 'succeeded')

        call_command('thumbnail', 'clear')
