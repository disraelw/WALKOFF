import json

from flask import current_app, request, send_file
from flask_jwt_extended import jwt_required

from walkoff.appgateway.apiutil import get_app_device_api, UnknownApp, UnknownDevice, InvalidArgument
from walkoff.appgateway.validator import validate_device_fields
from walkoff.executiondb.device import Device, App
from walkoff.security import permissions_accepted_for_resources, ResourcePermissions
from walkoff.server.decorators import with_resource_factory
from walkoff.server.problem import Problem
from walkoff.server.returncodes import *
from walkoff.config import Config
from redis import Redis
from collections import OrderedDict
from itertools import islice

try:
    from StringIO import StringIO
except ImportError:
    from io import StringIO

redis_cache = Redis(host=Config.CACHE["host"], port=Config.CACHE["port"])

with_device = with_resource_factory(
    'device',
    lambda device_id: redis_cache.hget("globals", device_id))


def get_device_json_with_app_name(device):
    device_json = json.loads(device)
    # app = redis_cache.hget("app-apis", device_json
    # app = current_app.running_context.execution_db.session.query(App).filter(App.id == device_json['app_id']).first()
    # device_json['app_name'] = app.name if app is not None else ''
    return device_json


def read_all_devices():
    @jwt_required
    @permissions_accepted_for_resources(ResourcePermissions('devices', ['read']))
    def __func():
        page = request.args.get('page', 1, type=int)
        devices = OrderedDict(sorted(redis_cache.hgetall("globals").items(), key=lambda kv: kv[0]))
        start = (page-1) * current_app.config['ITEMS_PER_PAGE']
        stop = start + current_app.config['ITEMS_PER_PAGE']

        return [get_device_json_with_app_name(device[1]) for device in islice(devices.items(), start, stop)], SUCCESS

    return __func()


def read_device(device_id, mode=None):
    @jwt_required
    @permissions_accepted_for_resources(ResourcePermissions('devices', ['read']))
    @with_device('read', device_id)
    def __func(device):
        if mode == "export":
            f = StringIO()
            f.write(json.dumps(get_device_json_with_app_name(device), sort_keys=True, indent=4, separators=(',', ': ')))
            f.seek(0)
            return send_file(f, attachment_filename=device.name + '.json', as_attachment=True), SUCCESS
        else:
            return get_device_json_with_app_name(device), SUCCESS

    return __func()


def delete_device(device_id):
    @jwt_required
    @permissions_accepted_for_resources(ResourcePermissions('devices', ['delete']))
    @with_device('delete', device_id)
    def __func(device):
        redis_cache.hdel("globals", device_id)
        current_app.logger.info('Device removed {0}'.format(device_id))
        return None, NO_CONTENT

    return __func()


def add_configuration_keys_to_device_json(device_fields, device_fields_api):
    device_fields_api = {field['name']: field for field in device_fields_api}
    for field in device_fields:
        add_configuration_keys_to_field(device_fields_api, field)


def add_configuration_keys_to_field(device_fields_api, field):
    if field['name'] in device_fields_api:
        field['type'] = device_fields_api[field['name']]['type']
        if 'encrypted' in device_fields_api[field['name']]:
            field['encrypted'] = device_fields_api[field['name']]['encrypted']


def create_device():
    @jwt_required
    @permissions_accepted_for_resources(ResourcePermissions('devices', ['create']))
    def __func():
        if request.files and 'file' in request.files:
            f = request.files['file']
            add_device_json = json.loads(f.read().decode('utf-8'))
        else:
            add_device_json = request.get_json()
        if redis_cache.hexists("globals", add_device_json['app_name']):
            current_app.logger.error('Could not create device {0}. '
                                     'Device already exists.'.format(add_device_json['name']))
            return Problem.from_crud_resource(
                OBJECT_EXISTS_ERROR,
                'device',
                'create',
                'Device with name {} already exists.'.format(add_device_json['name']))

        fields = {field['name']: field['value'] for field in add_device_json['fields']}
        app = add_device_json['app_name']
        device_type = add_device_json['type']
        # try:
        device_api = get_app_device_api(app, device_type)
        device_fields_api = device_api['fields']
        #     validate_device_fields(device_fields_api, fields, device_type, app)
        # except (UnknownApp, UnknownDevice, InvalidArgument) as e:
        #     return __crud_device_error_handler('create', e, app, device_type)
        # else:
        fields = add_device_json['fields']
        add_configuration_keys_to_device_json(fields, device_fields_api)
        # app = current_app.running_context.execution_db.session.query(App).filter(App.name == app).first()
        app = redis_cache.hget("app-apis", app)
        if app is None:
            current_app.logger.error('SEVERE: App defined in api does not have corresponding entry in database. '
                                     'Cannot add device')
            return Problem.from_crud_resource(
                INVALID_INPUT_ERROR,
                'device',
                'create',
                'App {} does not exist.'.format(add_device_json['app_name']))
        redis_cache.hset("globals", add_device_json["app_name"], add_device_json)
        # device = Device.from_json(add_device_json)
        # app.add_device(device)
        # current_app.running_context.execution_db.session.add(device)
        # current_app.running_context.execution_db.session.commit()
        device_json = get_device_json_with_app_name(device)
        return device_json, OBJECT_CREATED

    return __func()


def update_device():
    @jwt_required
    @permissions_accepted_for_resources(ResourcePermissions('devices', ['update']))
    @with_device('update', request.get_json()['id'])
    def __func(device):
        update_device_json = request.get_json()
        return _update_device(device, update_device_json)

    return __func()


def patch_device():
    @jwt_required
    @permissions_accepted_for_resources(ResourcePermissions('devices', ['update']))
    @with_device('update', request.get_json()['id'])
    def __func(device):
        update_device_json = request.get_json()
        return _update_device(device, update_device_json, validate_required=False)

    return __func()


def _update_device(device, update_device_json, validate_required=True):
    fields = ({field['name']: field['value'] for field in update_device_json['fields']}
              if 'fields' in update_device_json else None)
    app = update_device_json['app_name']
    device_type = update_device_json['type'] if 'type' in update_device_json else device.type
    try:
        device_api = get_app_device_api(app, device_type)
        device_fields_api = device_api['fields']
        if fields is not None:
            validate_device_fields(device_fields_api, fields, device_type, app, validate_required=validate_required)
    except (UnknownApp, UnknownDevice, InvalidArgument) as e:
        return __crud_device_error_handler('update', e, app, device_type)
    else:
        if fields is not None:
            fields = update_device_json['fields'] if 'fields' in update_device_json else None
            add_configuration_keys_to_device_json(fields, device_fields_api)
        device.update_from_json(update_device_json, complete_object=validate_required)
        current_app.running_context.execution_db.session.commit()
        device_json = get_device_json_with_app_name(device)
        return device_json, SUCCESS


__device_error_messages = {UnknownApp: ('App does not exist', 'Unknown app.'),
                           UnknownDevice: ('Type does not exist', 'Unknown device type.'),
                           InvalidArgument: ('Invalid input', 'Invalid device fields.')}


def __crud_device_error_handler(operation, exception, app, device_type):
    ret = __device_error_messages[exception.__class__]
    message = 'Could not {0} device for app {1}, type {2}. {3}.'.format(operation, app, device_type, ret[0])
    current_app.logger.error(message)
    return Problem(INVALID_INPUT_ERROR, ret[1], message)
