import os
from fabric.api import env, local, run, settings, sudo

from ._core import load_config, ServerManagementBaseCommand, run_tasks


class Command(ServerManagementBaseCommand):

    def handle(self, *args, **options):
        # Load server config from project
        config, remote = load_config(env, options.get('remote', ''), debug=options['debug'])

        with settings(warn_only=True):

            # Create a final dump of the database
            local('pg_dump {name} -cOx -U {user} -f ~/{name}.sql --clean'.format(
                name=config['local']['database']['name'],
                user=os.getlogin()
            ))

            # Push the database from earlier up to the server
            local('scp{}~/{}.sql {}@{}:/tmp/{}.sql'.format(
                ' ' if not getattr(env, 'key_filename') else ' -i {} '.format(env.key_filename),
                config['local']['database']['name'],
                env.user,
                remote['server']['ip'],
                remote['database']['name'],
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

            run_tasks(env, db_tasks)

            # Import the database file
            # sudo("su - {name} -c 'psql -q {name} < /tmp/{name}.sql > /dev/null 2>&1'".format(

            with settings(sudo_user=remote['database']['user']):
                sudo('psql {name} < /tmp/{name}.sql'.format(
                    name=remote['database']['name']
                ))

            # Remove the database file
            run('rm /tmp/{}.sql'.format(
                remote['database']['name']
            ))

            # Remove the SQL file from the host
            local('rm ~/{}.sql'.format(
                config['local']['database']['name']
            ))
