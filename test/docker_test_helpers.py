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

from docker import Client

d = Client()

# FIXME
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def _handle_request(container, port=80, expected_status_code=200, wait=30, timeout=0.5, expected_phrase=None):
    """
    Helper method to determine if the container is listetning on specific port
    and returning the exected status code. If the 'expected_phrase' parameter
    is specified, it additionally checks if the response body contains the
    specified string.
    
    By default it assumes that we are checking port 80 for return code 200.
    """
    logger.info("Checking if the container is returning status code %s on port %s" % (expected_status_code, port))

    success = False
    start_time = time.time()

    ip = container.ip_address
    latest_status_code = 0
        
    while time.time() < start_time + wait:
        try:
            response = requests.get('http://%s:%s' % (ip, port), timeout = timeout, stream=False)
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
                    success = True
                    break

                if expected_phrase in response.text:
                    # The expected_phrase parameter was found in the body
                    logger.info("Document body contains the '%s' phrase!" % expected_phrase)
                    success = True
                else:
                    # The phrase was not found in the response
                    logger.error("Failure! Correct status code received but the document body does not contain the '%s' phrase!" % expected_phrase)
                    logger.debug("Received body:\n%s" % response.text)

                break

        time.sleep(1)
        
    return success

def _expect_message(container, messages):
    """
    This is a helper method to scan the container logs for specific messages.
    Returns True if all messages were fond, False otherwise.
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

    return False

def _execute(command, **kwargs):
    """
    Helper method to execute a shell command and redirect the logs to logger
    with proper log level.
    """

    logger.debug("Executing '%s' command..." % command)

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
                    # fix it
                    logger.log(levels[output], line)
                    read = True
            return read

        while True:
            if not read_output():
                break

        process.wait()
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
    command = "sti build --loglevel=5 --forcePull=false --contextDir=%s %s %s %s" % (args.get('path', '.'), application, base_image_id, image_id)

    logger.info("Executing new STI build...")

    if _execute(command):
        logger.info("STI build succeeded, image %s was built" % image_id)
        return image_id

    logger.error("STI build failed, check logs!")
    return None

def _run_command_expect_message(cmd, find, container, wait=30):
    """Helper routine for running a command in a container and inspecting
       the result."""
    start_time = time.time()
    while time.time() < start_time + wait:
        output = container.execute(cmd)
        # in an ideal world we'd check the return code of the command, but
        # docker python client library doesn't provide us with that, so we
        # instead look for a predictable string in the output
        if output.find(find) >= 0:
            return True
        time.sleep(1)
    return False
