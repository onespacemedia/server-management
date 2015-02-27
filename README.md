===============================
server-management
===============================

``server-management`` allows for very simple deployment and day-to-day management of Django projects.  Primarily used and maintained by the team at [Onespacemedia](http://www.onespacemedia.com/), but should work for most people.  It allows you to deploy a Django application, preferably onto a Ubuntu or Debian server, and maintain the application over time.

### Features:

* Deployment, using Ansible and Fabric.
* Pushing your local database to the remote server.
* Pushing your local media files to the remote server.
* Pulling the database from the remote server to your local machine.
* Pulling the media files from the remote server to your local machine.
* Update the remote server with the latest version of your project.

### Notes / Assumptions:

* This project currently makes the assumption that your code is hosted on BitBucket.  However, the code can be very easily updated to support generic Git hosts, pull requests are welcomed for this.
* The code takes database credentials from a file on disk, so projects using configuration data stored in environment variables are not currently supported.
* The application stack consists of Nginx, Supervisor, Gunicorn and PostgreSQL.
* The deployment process will deploy all services onto one machine, it does not support splitting services across multiple machines, though as we're using Ansible it could be possible in the future.
* Your project is expected to use a virtual environment, with the folder in the same directory as the ``manage.py`` file, and be named ``venv`` or ``.venv``.
* The deploy script currently logs in as root and installs the base packages as root. The application, PostgreSQL server and supervisor all run under their own users.
* The deployment script does not currently support uploading HTTPS / SSL certificates or automatically configurating nginx to handle HTTPS traffic.

Installation
------------

To install ``server-management`` simply run:

    $ pip install server-management
    
Configuration
-------------

We need to add ``server-management`` to our project, so add ``server_management`` to your ``INSTALLED_APPS``.

    INSTALLED_APPS = [
        ...
        'server_management',
    ]
    
Next, you need to create a ``server.json`` file which contains the information about your remote server and your database. This will live in the project folder above ``manage.py``, you can print the exact location with ``settings.SITE_ROOT``. An example file is as follows:

    {
        "local": {
            "database": {
                "name": "example_dev"
            }
        },
        "remote": {
            "server": {
                "ip": "12.34.45.78"
            },
            "database": {
                "name": "example_prod",
                "user": "example_prod_user",
                "password": ""
            }
        }
    }

The default PostgreSQL deployment uses trust authentication for connecting to the database, so a password is not usually required.


Usage
-----

Once the project has been added to your project you will have access to a number of ``manage.py`` commands, they are as follows:

* [``deploy``](#user-content-deploy)
* [``pulldb``](#user-content-pulldb)
* [``pullmedia``](#user-content-pullmedia)
* [``pushdb``](#user-content-pushdb)
* [``pushmedia``](#user-content-pushmedia)
* [``update``](#user-content-update)

### Deploy