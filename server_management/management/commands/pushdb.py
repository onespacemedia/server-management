import os

from django.conf import settings as django_settings
from invoke import run as local

from ._core import ServerManagementBaseCommand, load_config, run_tasks, title_print
from .backupdb import perform_backup


class Command(ServerManagementBaseCommand):

    def handle(self, *args, **options):
        if not getattr(django_settings, 'SERVER_MANAGEMENT_ENABLE_PUSHDB', False):
            raise Exception('Database pushing has been disabled.')

        # Load server config from project
        config, connection = load_config(options.get('remote', ''), debug=options.get('debug', False))
        remote = config['remotes'][config['remote_name']]

        title_print('Backing up database', 'task')
        perform_backup(config, connection)
        title_print('Backing up database', 'succeeded')

        # Create a final dump of the database. We use Invoke's run() here as Postgres' pg_dump won't work since postgres won't be in $PATH for connection.local()
        local('pg_dump {name} -cOx -U {user} -f ~/{name}.sql --clean'.format(
            name=config['local']['database']['name'],
            user=os.getlogin()
        ))

        # Push the database from earlier up to the server
        connection.local('scp{}~/{}.sql {}@{}:/{}'.format(
            f' -i {connection.connect_kwargs["key_filename"]} ' if connection.connect_kwargs.get('key_filename') else ' ',
            config['local']['database']['name'],
            connection.user,
            remote['server']['ip'],
            os.path.join(
                'tmp',
                f'{remote["database"]["name"]}.sql'
            )
        ))

        # Define db tasks
        db_tasks = [
            dict(title='Stop Supervisor tasks', command='sudo supervisorctl stop all'),
            {
                'title': 'Drop database',
                'command': 'sudo su - postgres -c "dropdb -w {name}"'.format(
                    name=remote['database']['name'],
                ),
            },
            {
                'title': 'Ensure database is created',
                'command': 'sudo su - postgres -c "createdb {name} --encoding=UTF-8 --locale=en_GB.UTF-8 --template=template0 --owner={owner} --no-password"'.format(
                    name=remote['database']['name'],
                    owner=remote['database']['user'],
                ),
            },
            {
                'title': 'Ensure user has access to the database',
                'command': 'sudo su - postgres -c "psql {name} -c \'GRANT ALL ON DATABASE {name} TO {owner}\'"'.format(
                    name=remote['database']['name'],
                    owner=remote['database']['user'],
                ),
            },
            {
                'title': 'Ensure user does not have unnecessary privileges',
                'command': 'sudo su - postgres -c "psql {name} -c \'ALTER USER {owner} WITH NOSUPERUSER NOCREATEDB\'"'.format(
                    name=remote['database']['name'],
                    owner=remote['database']['user'],
                ),
            },
            {
                'title': 'Start Supervisor tasks',
                'command': 'sudo supervisorctl start all',
            },
        ]

        run_tasks(connection, db_tasks)

        # Import the database file
        # sudo("su - {name} -c 'psql -q {name} < /tmp/{name}.sql > /dev/null 2>&1'".format(
        connection.sudo('psql {} < /{}'.format(
            remote['database']['name'],
            os.path.join(
                'tmp',
                f'{remote["database"]["name"]}.sql'
            )
        ), user=remote['database']['user'])

        # Remove the database file
        connection.run('rm /tmp/{}.sql'.format(
            remote['database']['name']
        ))

        # Remove the SQL file from the host
        connection.local('rm ~/{}.sql'.format(
            config['local']['database']['name']
        ))
