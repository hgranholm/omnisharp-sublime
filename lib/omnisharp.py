import os
import sublime
import threading
import json
import urllib
import urllib.parse
import socket
import subprocess
import queue
import traceback
import sys
import signal

from .helpers import get_settings
from .helpers import current_solution_filepath_or_project_rootpath
from .urllib3 import PoolManager

from queue import Queue

IS_EXTERNAL_SERVER_ENABLE = False
IS_NT_CONSOLE_VISIBLE = False 

launcher_procs = {
}

server_ports = {
}

class WorkerThread(threading.Thread):
    _worker_threads = []
    _worker_queue = Queue()

    def run(self):
        http = PoolManager(headers={'Content-Type': 'application/json'})
        while True:
            url, data, timeout, callback = self._worker_queue.get()
            try:
                response = http.urlopen('POST', url, body=data)
                callback(response.data)
            except:
                traceback.print_exc(file=sys.stdout)
                callback(None)

    @classmethod
    def make_worker_threads(cls, count):
        while len(cls._worker_threads) < count:
            new_worker_thread = cls()
            new_worker_thread.start()
            cls._worker_threads.append(new_worker_thread)

    @classmethod
    def add_work(cls, url, data, timeout, callback):
        cls._worker_queue.put((url, data, timeout, callback))

WorkerThread.make_worker_threads(1)

def urlopen_async(url, callback, data, timeout):
    WorkerThread.add_work(url, data, timeout, callback)

def get_response(view, endpoint, callback, params=None, timeout=None):
    solution_path =  current_solution_filepath_or_project_rootpath(view)

    print('response:', solution_path)
    if solution_path is None or solution_path not in server_ports:
        callback(None)
        return
        
    parameters = {}
    location = view.sel()[0]
    cursor = view.rowcol(location.begin())

    parameters['line'] = str(cursor[0] + 1)
    parameters['column'] = str(cursor[1] + 1)
    parameters['buffer'] = view.substr(sublime.Region(0, view.size()))
    parameters['filename'] = view.file_name()

    if params is not None:
        parameters.update(params)
    if timeout is None:
        timeout = int(get_settings(view, 'omnisharp_response_timeout'))

    host = 'localhost'
    port = server_ports[solution_path]

    httpurl = "http://%s:%s/" % (host, port)

    target = urllib.parse.urljoin(httpurl, endpoint)
    data = json.dumps(parameters).encode('utf-8')
    print('request: %s' % target)
    print('======== request params ======== \n %s' % data)

    def urlopen_callback(data):
        print('======== response ========')
        print(data)
        if data is None:
            print(None)
            # traceback.print_stack(file=sys.stdout)
            print('CALLBACK_ERROR')
            callback(None)

            # if solution_path in launcher_procs:
            #     print('TERMINATE_OMNI_SHARP')
            #     launcher_procs[solution_path].terminate();

            #     del launcher_procs[solution_path]
            #     del server_ports[solution_path]

        else:
            jsonStr = data.decode('utf-8')
            print(jsonStr)
            jsonObj = json.loads(jsonStr)
            # traceback.print_stack(file=sys.stdout)
            print('callback data')
            callback(jsonObj)

    urlopen_async(
        target,
        urlopen_callback,
        data,
        timeout)

def get_response_from_empty_httppost(view, endpoint, callback, timeout=None):
    solution_path =  current_solution_filepath_or_project_rootpath(view)

    print(solution_path)
    print(server_ports)
    if solution_path is None or solution_path not in server_ports:
        callback(None)
        return
    parameters = {}
    location = view.sel()[0]
    cursor = view.rowcol(location.begin())

    if timeout is None:
        timeout = int(get_settings(view, 'omnisharp_response_timeout'))

    host = 'localhost'
    port = server_ports[solution_path]

    httpurl = "http://%s:%s/" % (host, port)

    target = urllib.parse.urljoin(httpurl, endpoint)
    data = urllib.parse.urlencode(parameters).encode('utf-8')
    print('request: %s' % target)
    print('======== no request params ======== \n')

    def urlopen_callback(data):
        print('======== response ========')
        if data is None:
            print(None)
            # traceback.print_stack(file=sys.stdout)
            print('callback none')
            callback(None)
        else:
            jsonStr = data.decode('utf-8')
            print(jsonStr)
            jsonObj = json.loads(jsonStr)
            # traceback.print_stack(file=sys.stdout)
            print('callback data')
            callback(jsonObj)

    urlopen_async(
        target,
        urlopen_callback,
        data,
        timeout)

def _available_port():
    if IS_EXTERNAL_SERVER_ENABLE:
        return 2000

    s = socket.socket()
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()

    return port

def create_omnisharp_server_subprocess(view):
    solution_path = current_solution_filepath_or_project_rootpath(view) 
    if solution_path in launcher_procs:
        print("already_bound_solution:%s" % solution_path)
        return

    print("solution_path:%s" % solution_path)

    omni_port = _available_port()
    print('omni_port:%s' % omni_port)
    
    
    config_file = get_settings(view, "omnisharp_server_config_location")

    if IS_EXTERNAL_SERVER_ENABLE:
        launcher_proc = None
        omni_port = 2000
    else:
        try:
            omni_exe_paths = find_omni_exe_paths()
            omni_exe_path = "\"" + omni_exe_paths[0] + "\""

            args = [
                omni_exe_path, 
                '-s', '"' + solution_path + '"',
                '-p', str(omni_port),
                '-config', '"' + config_file + '"',
                '--hostPID', str(os.getpid())
            ]

            cmd = ' '.join(args)
            print(cmd)
            
            view.window().run_command("exec",{"cmd":cmd,"shell":"true","quiet":"true"})
            view.window().run_command("hide_panel", {"panel": "output.exec"})

        except Exception as e:
            print('RAISE_OMNI_SHARP_LAUNCHER_EXCEPTION:%s' % repr(e))
            return

    launcher_procs[solution_path] = True
    server_ports[solution_path] = omni_port

def find_omni_exe_paths():
    if os.name == 'posix':
        source_file_path = os.path.realpath(__file__)
        script_name = 'omnisharp'
    else:
        source_file_path = os.path.realpath(__file__).replace('\\', '/')
        script_name = 'omnisharp.cmd'

    source_dir_path = os.path.dirname(source_file_path)
    plugin_dir_path = os.path.dirname(source_dir_path)
    print(plugin_dir_path)

    omni_exe_candidate_rel_paths = [
        'omnisharp-roslyn/artifacts/build/omnisharp/' + script_name,
        'PrebuiltOmniSharpServer/' + script_name,
    ]

    omni_exe_candidate_abs_paths = [
        '/'.join((plugin_dir_path, rel_path))
        for rel_path in omni_exe_candidate_rel_paths
    ]

    return [omni_exe_path 
        for omni_exe_path in omni_exe_candidate_abs_paths
        if os.access(omni_exe_path, os.R_OK)]

