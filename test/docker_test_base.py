"""
The MIT License (MIT)

Copyright (c) 2015 Red Hat

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import print_function, unicode_literals
from docker import Client
from StringIO import StringIO

import fnmatch
import imp
import inspect
import json
import logging
import os
import re
import requests
import select
import subprocess
import sys
import time
import traceback
import xml.etree.cElementTree as ET
from docker import Client

d = Client()

class DockerTest(object):
    """ Base class for all Docker integration tests """

    def __init__(self, image_id, tests, git_repo_path, results_dir, logger=None, **kwargs):
        self.test_file_pattern = "test_*py"
        self.image_id = image_id
        self.tests = tests
        self.git_repo_path = git_repo_path
        self.logger = logger
        self.results_dir = results_dir
        self.kwargs = kwargs
     
    def _log(self, m, level=logging.INFO):
        """ log using logger, or print to stdout """
        if self.logger:
            self.logger.log(level, m)
        else:
            print(m)

    def _generate_xunit_file(self, results):
        root = ET.Element("testsuite", name="mw_docker_tests")
        for test, result in results.items():
            doc = ET.SubElement(root, "testcase", classname=test, name=test)
            if (not result):
                ET.SubElement(doc, "failure", message="error occured")
        tree = ET.ElementTree(root)
        self._log("creating results dir: " + self.results_dir )
        try:
            os.stat(self.results_dir)
        except:
            os.mkdir(self.results_dir)
        tree.write(self.results_dir +  "/mw_test_out.xml")

    def _start_container(self, image):
        """ Starts a detached container for selected image """
        self._log("creating container from image '%s'..." % image, logging.DEBUG)
        container = d.create_container(image=image, detach=True)
        self._log("starting container '%s'..." % container.get('Id'), logging.DEBUG)
        d.start(container=container)
        return container

    def _stop_container(self, container):
        """
        Stops (and removes) selected container.
        Additionally saves the STDOUT output to a `container_output` file for later investigation.
        """
        with open('container_output', 'w') as f:
            print(d.attach(container=container.get('Id'), stream=False, logs=True), file=f)
        f.closed
        if container:
            self._log("removing container '%s'" % container['Id'], logging.DEBUG)
            d.kill(container=container)
            d.remove_container(container)
        else:
            self._log("no container to tear down", logging.DEBUG)

    def _expect_message(self, image_or_container, messages):
        """
        This is a helper method to scan the container logs for specific messages.
        Returns True if all messages were fond, False otherwise.
        """
        found = True

        # Start a container if necessary
        # TODO: move this to a helper method so it can be reused
        # (ensuring that we stop and remove the container afterwards)
        if isinstance(image_or_container, basestring):
            container = self._start_container(image_or_container)
        else:
            container = image_or_container

        found_messages = []
        start_time = time.time()

        # TODO: Add customization option for timeout
        while time.time() < start_time + 30:
            if len(messages) == len(found_messages):
                break

            logs = d.attach(container=container.get('Id'), stream=False, logs=True)

            # TODO: needs refactor
            for message in messages:
                if message in logs:
                    if message not in found_messages:
                        found_messages.append(message)
                        self._log("Message '%s' was found in the logs" % message, logging.INFO)
                break

            # TODO: Add customization option for sleep time
            time.sleep(1)

        # Stop the container if we started it
        # TODO: This feels a bit weird, fix it?
        if isinstance(image_or_container, basestring):
            self._stop_container(container)

        if len(messages) == len(found_messages):
            self._log("All messages (%s) found in the logs!" % messages, logging.INFO)
            return True
        else:
            for m in messages:
                if m not in found_messages:
                    self._log("Message '%s' was found in the logs" % m, logging.ERROR)

        return False

    def _execute(self, command, **kwargs):
        """
        Helper method to execute a shell command and redirect the logs to logger
        with proper log level.
        """

        self._log("Executing '%s' command..." % command, logging.DEBUG)

        try:
            process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)

            levels = {
                process.stdout: logging.DEBUG,
                process.stderr: logging.ERROR
            }

            def read_output():
                ready = select.select([process.stdout, process.stderr], [], [], 1000)[0]
                read = False
                for output in ready:
                    line = output.readline()[:-1]
                    if line:
                      self._log(line, levels[output])
                      read = True
                return read

            while True:
              if not read_output():
                break

            process.wait()
        except subprocess.CalledProcessError as e:
            self._log("Command '%s' failed, check logs" % command, logging.ERROR)
            return False

        return True

    def _sti_build(self, application, **args):
        """
        This is a helper method that executes a build with STI tool.
        If the build is successful it returns the image ID, None otherwise.
        """
        # TODO: extend args with loglevel

        # Resulting image ID
        image_id = "integ-" + self.image_id
        command = "sti build --loglevel=5 --forcePull=false --contextDir=%s %s %s %s" % (args.get('path', '.'), application, self.image_id, image_id)

        self._log("Executing new STI build...", logging.INFO)

        if self._execute(command):
            self._log("STI build succeeded, image %s was built" % image_id, logging.INFO)
            return image_id

        self._log("STI build failed, check logs!" % logging.ERROR)
        return None

    def setup(self):
        """ this method is called before every test run """
        self.container = self._start_container(self.image_id)

    def teardown(self):
        """ called after every test run """
        self._stop_container(self.container)
        self.container = None

    def run(self):
        """ entry point, run all tests and return results """
        # just hacky to have this module on path
        this_module_path =  os.path.dirname(inspect.getfile(self.__class__))
        sys.path.append(this_module_path)
        results = {}
        test_files = {}
        passed = True

        for root, dirs, files in os.walk(os.getcwd()):
            for filename in fnmatch.filter(files, self.test_file_pattern):
                test_file =  os.path.join(root, filename)
                test_module = imp.load_source("", test_file)
                test_class = test_module.run( self.image_id, self.tests,
                                              self.git_repo_path, self.results_dir,
                                              logger=None)
                test_class.setup()
                for test in test_class.tests:
                    if ( "all" in self.tests or test_class.tag in self.tests):
                        test_name = test.__func__.__name__
                        self._log("starting test '%s'" % test_name, logging.INFO)
                        try:
                            test_result = test(test_class)
                        except Exception as ex:
                            results[test_name] = traceback.format_exc()
                            passed = False
                        else:
                            results[test_name] = test_result
                            if test_result is False:
                                passed = False
                                self._log("test result: '%s'" % results[test_name], logging.INFO)
                test_class.teardown()
        self._log("did tests pass? '%s'" % passed, logging.INFO)
        self._generate_xunit_file(results)
        return results, passed



def run(image_id, tests, git_repo_path, results_dir, logger=None, **kwargs):
    e = DockerTest(image_id, tests, git_repo_path, results_dir, logger=None, **kwargs)
    return e.run()
