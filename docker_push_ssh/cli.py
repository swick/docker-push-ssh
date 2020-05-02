# Copyright 2018, Bryan Thornbury
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import argparse
import os
import socket
import sys
import time
import urllib.request
import urllib.error

from command import Command


def getLocalIp():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    localIp = s.getsockname()[0]
    s.close()

    print("[Local IP] " + localIp)

    return localIp


def waitForSshTunnelInit(retries=20, delay=1.0):
    for _ in range(retries):
        time.sleep(delay)

        try:
            response = urllib.request.urlopen("http://localhost:5000/v2/", timeout=5)
        except (socket.error, urllib.error.URLError, urllib.error.HTTPError):
            continue

        if response.getcode() == 200:
            return True

    return False


def pushImage(dockerImageTagList, sshHost, primeImages, registryPort):
    # Setup remote docker registry
    print("Setting up secure private registry... ")
    registryCommandResult = Command("ssh", [
        sshHost,
        "sh -l -c \"podman rm -f docker-push-ssh-registry; " +
        "podman run -d -v /var/lib/registry:/var/lib/registry " +
        "--name docker-push-ssh-registry -p 127.0.0.1:{0}:5000 registry\"".format(registryPort)
    ]).environment_dict(os.environ).execute()

    if registryCommandResult.failed():
        print("ERROR")
        print(registryCommandResult.stdout)
        print(registryCommandResult.stderr)
        return False

    try:
        # Establish ssh tunnel
        print("Establishing SSH Tunnel...")

        sshTunnelCommand = Command("ssh", [
            "-N",
            "-L", "*:5000:localhost:{0}".format(registryPort),
            sshHost
        ])

        sshTunnelCommand.environment_dict(os.environ).execute(waitForExit=False)

#        if sshTunnelCommandResult.failed():
#            print("ERROR")
#            print(sshTunnelCommandResult.stdout)
#            print(sshTunnelCommandResult.stderr)
#            return False

        print("Waiting for SSH Tunnel Initialization...")

        if not waitForSshTunnelInit():
            print("ERROR")
            print("SSH Tunnel failed to initialize.")

#            logsCmd = Command("docker", ["logs", "docker-push-ssh-tunnel"]).environment_dict(os.environ).execute()
#            print(logsCmd.stdout, logsCmd.stderr)
            return False

#        if sshTunnelCommandResult.failed():
#            print("ERROR")
#            print(sshTunnelCommandResult.stdout)
#            print(sshTunnelCommandResult.stderr)
#            return False

        print("Priming Registry with base images...")
        for primeImage in (primeImages or []):
            
            print("Priming base image ({0})".format(primeImage))

            primingCommand = Command("ssh", [
                sshHost,
                "sh -l -c \"podman pull {0}".format(primeImage) +
                " && podman tag {0} localhost:{1}/{0} && podman push localhost:{1}/{0}\"".format(primeImage, registryPort)
            ]).environment_dict(os.environ).execute()

            if primingCommand.failed():
                print("ERROR")
                print(primingCommand.stdout)
                print(primingCommand.stderr)
                return False

        print("Tagging image(s) for push...")
        for dockerImageTag in dockerImageTagList:
            tagCommandResult = Command("podman", [
                "tag",
                dockerImageTag,
                "localhost:5000/{0}".format(dockerImageTag)
            ]).environment_dict(os.environ).execute()

            if tagCommandResult.failed():
                print("ERROR")
                print(tagCommandResult.stdout)
                print(tagCommandResult.stderr)
                return False

        print("Pushing Image(s) from local host...")
        for dockerImageTag in dockerImageTagList:
            pushDockerImageCommandResult = Command("podman", [
                "push",
                "localhost:5000/{0}".format(dockerImageTag)
            ]).environment_dict(os.environ).execute()

            if pushDockerImageCommandResult.failed():
                print("ERROR")

                print(pushDockerImageCommandResult.stdout)
                print(pushDockerImageCommandResult.stderr)

                print("Error Pushing Image: Ensure localhost:5000 is added to your insecure registries.")
                print("More Details (OS X): "
                      "https://stackoverflow.com/questions/32808215/where-to-set-the-insecure-registry-flag-on-mac-os")
                print("More Details (Linux): "
                      "https://stackoverflow.com/questions/42211380/add-insecure-registry-to-docker")
                return False

            print("Pushed Image {0} Successfully...".format(dockerImageTag))

        print("Pulling and Retagging Image on remote host...")
        for dockerImageTag in dockerImageTagList:
            pullDockerImageCommandResult = Command("ssh", [
                sshHost,
                "sh -l -c \"podman pull " + "localhost:{1}/{0}".format(dockerImageTag, registryPort) +
                " && podman tag localhost:{1}/{0} {0}\"".format(dockerImageTag, registryPort)
            ]).environment_dict(os.environ).execute()

            if pullDockerImageCommandResult.failed():
                print("ERROR")
                print(pullDockerImageCommandResult.stdout)
                print(pullDockerImageCommandResult.stderr)
                return False

            print("Pulled Image {0} Successfully...".format(dockerImageTag))

    finally:
        print("Cleaning up...")
        Command("ssh", [
            sshHost,
            "sh -l -c \"podman rm -f docker-push-ssh-registry\""
        ]).environment_dict(os.environ).execute()

        sshTunnelCommand.terminate()

        for dockerImageTag in dockerImageTagList:
            Command("podman", [
                "image", "rm",
                "localhost:5000/{0}".format(dockerImageTag)
            ]).environment_dict(os.environ).execute()

    return True


def main():
    parser = argparse.ArgumentParser(description="A utility to securely push a docker image from your local host to a "
                                                 "remote host over ssh without using docker save/load or needing to "
                                                 "setup a private registry.")

    parser.add_argument("ssh_host", help="Host to push docker image to. (ex. username@myhost.com)")

    parser.add_argument("docker_image", nargs='+',
                        help="Docker image tag(s) to push. Specify one or more separated by spaces.")

    parser.add_argument("-p", "--ssh-port", type=str, help="[optional] Port on ssh host to connect to. (Default is 22)", default="22")

    parser.add_argument("-r", "--registry-port", type=str,
                        help="[optional] Remote registry port on ssh host to forward to. (Default is 5000)", default="5000")

    parser.add_argument("--prime-image", help="[optional] [list] Base images with which to prime the registry from the remote host. Docker pull is performed on the remote host.", action="append")

    args = parser.parse_args()

    print("[REQUIRED] Ensure localhost:5000 is added to your insecure registries.")

    success = pushImage(args.docker_image, args.ssh_host, args.prime_image, args.registry_port)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
