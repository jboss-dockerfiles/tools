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

import logging
import requests
import select
import subprocess
import time
import sys
from docker_test_base import Container
from docker import Client

d = Client()

# FIXME
LOG_FORMAT='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
logging.basicConfig(level=logging.DEBUG, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

def handle_request(container, port=80, expected_status_code=200, wait=30, timeout=0.5, expected_phrase=None, path='/'):
    """
    Helper method to determine if the container is listening on a specific port
    and returning the expected status code. If the 'expected_phrase' parameter
    is specified, it additionally checks if the response body contains the
    specified string.
    
    By default it assumes that we are checking port 80 for return code 200,
    with a path of '/'.
    """
    logger.info("Checking if the container is returning status code %s on port %s" % (expected_status_code, port))

    start_time = time.time()

    ip = container.ip_address
    latest_status_code = 0
        
    while time.time() < start_time + wait:
        try:
            response = requests.get('http://%s:%s%s' % (ip, port, path), timeout = timeout, stream=False)
        except Exception as ex:
            # Logging as warning, bcause this does not neccessarily means
            # something bad. For example the server did not boot yet.
            logger.warn("Exception caught: %s" % repr(ex))
        else:
            latest_status_code = response.status_code
            logger.debug("Response code from the container on port %s: %s (expected: %s)" % (port, latest_status_code, expected_status_code))
            if latest_status_code == expected_status_code:
                if not expected_phrase:
                    # The expected_phrase parameter was not set
                    return True

                if expected_phrase in response.text:
                    # The expected_phrase parameter was found in the body
                    logger.info("Document body contains the '%s' phrase!" % expected_phrase)
                    return True
                else:
                    # The phrase was not found in the response
                    raise Exception("Failure! Correct status code received but the document body does not contain the '%s' phrase!" % expected_phrase,
                        "Received body:\n%s" % response.text) # XXX: better diagnostics

        time.sleep(1)
    raise Exception("handle_request failed", expected_status_code) # XXX: better diagnostics

def expect_message(container, messages):
    """
    This is a helper method to scan the container logs for specific messages.
    Returns True if all messages were found, False otherwise.
    """
    found = True
    found_messages = []
    start_time = time.time()

    # TODO: Add customization option for timeout
    while time.time() < start_time + 30:
        if len(messages) == len(found_messages):
            break

        logs = d.attach(container=container.container.get('Id'), stream=False, logs=True)

        # TODO: needs refactor
        for message in messages:
            logger.debug("Trying to find message '%s' in logs..." % message)
            if message in logs and message not in found_messages:
                found_messages.append(message)
                logger.info("Message '%s' was found in the logs" % message)

        # TODO: Add customization option for sleep time
        time.sleep(1)

    if len(messages) == len(found_messages):
        logger.info("All messages (%s) found in the logs!" % messages)
        return True
    else:
        for m in messages:
            if m not in found_messages:
                logger.error("Message '%s' was not found in the logs" % m)

    raise Exception("expect_message failed", messages)

def _execute(command, **kwargs):
    """
    Helper method to execute a shell command and redirect the logs to logger
    with proper log level.
    """

    logger.debug("Executing '%s' command..." % command)

    try:
        proc = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)


        levels = {
            proc.stdout: logging.DEBUG,
            proc.stderr: logging.ERROR
        }

        fcntl.fcntl(
            proc.stderr.fileno(),
            fcntl.F_SETFL,
            fcntl.fcntl(proc.stderr.fileno(), fcntl.F_GETFL) | os.O_NONBLOCK,
        )

        fcntl.fcntl(
            proc.stdout.fileno(),
            fcntl.F_SETFL,
            fcntl.fcntl(proc.stdout.fileno(), fcntl.F_GETFL) | os.O_NONBLOCK,
        )

        while proc.poll() == None:
            readx = select.select([proc.stdout, proc.stderr], [], [])[0]
            for output in readx:
                line = output.readline()
                logger.log(levels[output], line)

        proc.wait()

    except subprocess.CalledProcessError as e:
        logger.error("Command '%s' failed, check logs" % command)
        return False

    return True

def _sti_build(base_image_id, application, **args):
    """
    This is a helper method that executes a build with STI tool.
    If the build is successful it returns the image ID, None otherwise.
    """
    # TODO: extend args with loglevel

    # Resulting image ID
    image_id = "integ-" + base_image_id
    command = "sti build --loglevel=3 --force-pull=false --context-dir=%s %s %s %s" % (args.get('path', '.'), application, base_image_id, image_id)

    logger.info("Executing new STI build...")

    if _execute(command):
        logger.info("STI build succeeded, image %s was built" % image_id)
        return image_id

    logger.error("STI build failed, check logs!")
    return None

def run_command_expect_message(container, cmd, find, wait=30):
    """Helper routine for running a command in a container and inspecting
       the result."""
    start_time = time.time()
    while time.time() < start_time + wait:
        output = container.execute(cmd)
        # in an ideal world we'd check the return code of the command, but
        # docker python client library doesn't provide us with that, so we
        # instead look for a predictable string in the output
        if find in output:
            return True
        time.sleep(1)
    raise Exception("run_command_expect_message didn't find message", output)

#this decorator can be used only with our test_methods
class sti_build(object):
    def __init__(self, application, **kwargs):
        self.kwargs = kwargs
        self.application = application

    def __call__(self, func):
        decorator = self
        def wrap(self, **kwargs):
            image_id = "integ-" + self.runner.image_id
            command = "sti build --loglevel=3 --force-pull=false --context-dir=%s %s %s %s" % (decorator.kwargs.get('path', '.'), decorator.application, self.runner.image_id, image_id)
            logger.debug("Executing new STI build...")
            if _execute(command):
                logger.debug("STI build succeeded, image %s was built" % image_id)
            else:
                logger.error("STI build failed, check logs!")
            container = Container(image_id, name = func.__name__)
            with container:
                self.sti_container = container
                func(self)
            return True
        return wrap 
