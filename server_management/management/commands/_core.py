from django.conf import settings
import json
import ansible.runner
import ansible.inventory


def load_config():
    # Load the json file
    try:
        json_data = open("{}/server.json".format(
            settings.SITE_ROOT
        ))
        config = json.load(json_data)
    except:
        raise Exception("Something is wrong with the server.json file, make sure it exists and is valid JSON.")

    # Return the server config
    return config


def check_request(request, host, request_type, color='\033[95m'):
    if len(request['dark']) or request['contacted'][host].get('failed', None) is True:
        print "[{}{}\033[0m] [\033[91mFAILED\033[0m]".format(color, request_type)
        print request
        exit()
    else:
        print "[{}{}\033[0m] [\033[92mDONE\033[0m]".format(color, request_type)


def ansible_task(host, **kwargs):
    # Create ansible inventory
    ansible_inventory = ansible.inventory.Inventory([host])

    ansible_args = dict({
        'pattern': 'all',
        'inventory': ansible_inventory,
        'sudo': True,
        'sudo_user': 'root',
        'remote_user': 'root'
    }.items() + kwargs.items())

    ansible.constants.HOST_KEY_CHECKING = False

    return ansible.runner.Runner(**ansible_args).run()


def run_tasks(host, tasks):
    # Loop tasks
    for task in tasks:

        if not task.get('with_items'):
            print "[\033[95mTASK\033[0m] {}...".format(task['title'])

            # Run task with arguments
            task_result = ansible_task(host, **task['ansible_arguments'])

            # Check result
            check_request(task_result, host, "TASK")


        else:
            print "[\033[95mTASK\033[0m] {}...".format(task['title'])

            # Store task args pattern
            module_args_pattern = task['ansible_arguments']['module_args']

            for item in task.get('with_items'):

                print "[\033[94mITEM\033[0m] {}".format(item)

                # Format args with item
                task['ansible_arguments']['module_args'] = module_args_pattern.format(
                    item=item
                )

                # Run task with arguments
                task_result = ansible_task(host, **task['ansible_arguments'])

                # Check result
                check_request(task_result, host, "ITEM", color='\033[94m')

            print "[\033[95mTASK\033[0m] [\033[92mDONE\033[0m]"

        print ""
