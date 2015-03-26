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
import uuid
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
        self.results_dir = results_dir
        self.kwargs = kwargs

        if logger:
            self.logger = logger
        else:
            self.logger = logging.getLogger("dock.middleware")
     
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
        self._log("Creating results dir: " + self.results_dir )
        try:
            os.stat(self.results_dir)
        except:
            os.mkdir(self.results_dir)
        tree.write(self.results_dir +  "/mw_test_out.xml")

    def _start_container(self, image):
        """ Starts a detached container for selected image """
        self._log("Creating container from image '%s'..." % image, logging.DEBUG)
        container = d.create_container(image=image, detach=True)
        self._log("Starting container '%s'..." % container.get('Id'), logging.DEBUG)
        d.start(container=container)
        return container

    def _stop_container(self, container):
        """
        Stops (and removes) selected container.
        Additionally saves the STDOUT output to a `container_output` file for later investigation.
        """
        with open('container_output.txt', 'w') as f:
            print(d.attach(container=container.get('Id'), stream=False, logs=True), file=f)
        f.closed
        if container:
            self._log("Removing container '%s'" % container['Id'], logging.DEBUG)
            d.kill(container=container)
            d.remove_container(container)
        else:
            self._log("no container to tear down", logging.DEBUG)

    def _handle_request(self, port=80, expected_status_code=200, wait=30, timeout=0.5, expected_phrase=None):
        """
        Helper method to determine if the container is listetning on specific port
        and returning the exected status code. If the 'expected_phrase' parameter
        is specified, it additionally checks if the response body contains the
        specified string.

        By default it assumes that we are checking port 80 for return code 200.
        """
        self._log("Checking if the container is returning status code %s on port %s" % (expected_status_code, port), logging.INFO)

        success = False
        start_time = time.time()

        ip = d.inspect_container(container=self.container)['NetworkSettings']['IPAddress']
        latest_status_code = 0

        while time.time() < start_time + wait:
            try:
                response = requests.get('http://%s:%s' % (ip, port), timeout = timeout, stream=False)
            except Exception as ex:
                # Logging as warning, bcause this does not neccessarily means
                # something bad. For example the server did not boot yet.
                self._log("Exception caught: %s" % repr(ex), logging.WARN)
            else:
                latest_status_code = response.status_code
                self._log("Response code from the container on port %s: %s (expected: %s)" % (port, latest_status_code, expected_status_code), logging.DEBUG)
                if latest_status_code == expected_status_code:
                    if not expected_phrase:
                        # The expected_phrase parameter was not set
                        success = True
                        break

                    if expected_phrase in response.text:
                        # The expected_phrase parameter was found in the body
                        self._log("Document body contains the '%s' phrase!" % expected_phrase, logging.INFO)
                        success = True
                    else:
                        # The phrase was not found in the response
                        self._log("Failure! Correct status code received but the document body does not contain the '%s' phrase!" % expected_phrase, logging.ERROR)
                        self._log("Received body:\n%s" % response.text, logging.DEBUG)

                    break

            time.sleep(1)

        return success

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

    def _run_tests_from_class(self, test_class,  results):
        test_count = 0
        test_class.setup()
        self._log("Running tests from class '%s'..." % test_class.__class__.__name__, logging.INFO)
        # Loop through all methods from our class
        for test_name, test in inspect.getmembers(test_class, inspect.ismethod):
            # Take only ones which name starts with "test_"
            if test_name.startswith("test_"):
                self._log("Running test '%s'" % test_name, logging.INFO)
                test_count += 1
                try:
                    test_result =  test()
                except Exception as ex:
                    results[test_name] = traceback.format_exc()
                    passed = False
                else:
                    results[test_name] = test_result
                    if test_result is False:
                        passed = False
                        failed_tests.append(test_name)
                        self._log("==> Test '%s' failed!" % test_name, logging.ERROR)
                    else:
                        self._log("==> Test '%s' passed!" % test_name, logging.INFO)
        test_class.teardown()
        return test_count
    

    def setup(self):
        """ This method is called before every test run """
        self.container = self._start_container(self.image_id)

    def teardown(self):
        """ Called after every test run """
        self._stop_container(self.container)
        self.container = None

    def run(self):
        """ Entry point, run all tests and return results """
        # just hacky to have this module on path
        this_module_path =  os.path.dirname(inspect.getfile(self.__class__))
        sys.path.append(this_module_path)
        results = {}
        passed = True

        # Simple stats
        test_count = 0
        failed_tests = []
        if self.tests:
            self._log("Using user provided test location: %s" % self.tests, logging.DEBUG)
            tests_pattern = self.tests
        else:
            self._log("Using default test location: %s" % self.test_file_pattern, logging.DEBUG)
            tests_pattern = self.test_file_pattern

        for path in tests_pattern.split(','):
            dirname, pattern = path.rsplit("/",1)
            # If we get only pattern we use CWD to find classes
            if not dirname:
                dirname = os.getcwd()
                
            for root, dirs, files in os.walk(dirname):
                # Skip the Git directory itself
                if ".git" in root:
                    continue
                for filename in fnmatch.filter(files, pattern):
                    test_file =  os.path.join(root, filename)
                    module_marker = str(uuid.uuid4())
                    # Load class to unique namespace
                    test_module = imp.load_source(module_marker, test_file)
                    
                    # Get all classes from our module
                    for name, clazz in inspect.getmembers(test_module, inspect.isclass):
                        # Check that class is from our namespace
                        if module_marker == clazz.__module__:
                            # Instantiate class
                            cls = getattr(test_module, name)
                            test_class = cls( self.image_id, self.tests,
                                              self.git_repo_path, self.results_dir,
                                              logger=None)
                            test_count += self._run_tests_from_class(test_class, results)

        if test_count > 0:
            if passed:
                self._log("==> Summary: All tests passed!", logging.INFO)
            else:
                self._log("==> Summary: %s of %s tests failed!" % (len(failed_tests), test_count), logging.ERROR)
                self._log("Failed tests: %s" % failed_tests, logging.ERROR)

        self._generate_xunit_file(results)
        return results, passed



def run(image_id, tests, git_repo_path, results_dir, logger=None, **kwargs):
    e = DockerTest(image_id, tests, git_repo_path, results_dir, logger=None, **kwargs)
    return e.run()
