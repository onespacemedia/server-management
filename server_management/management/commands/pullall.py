from django.core.management import call_command

from server_management.management.commands._core import ServerManagementBaseCommand, title_print, get_remote


class Command(ServerManagementBaseCommand):

    def handle(self, *args, **options):
        remote_prompt, _ = get_remote(options.get('remote', ''))

        title_print('Pulling database', 'task')
        call_command('pulldb', remote=remote_prompt)
        title_print('Pulling database', 'succeeded')

        title_print('Pulling media', 'task')
        call_command('pullmedia', remote=remote_prompt)
        title_print('Pulling media', 'succeeded')

        call_command('thumbnail', 'clear')
