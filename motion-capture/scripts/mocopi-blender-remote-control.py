# Remote control your motion capture from your phone
# Allows you to stay engaged in your mocap work instead of pressing
# buttons in blender to constantly start and stop recordings
# --------------------------------------------------
# uses your local WIFI network
# this requires the mocopi blender receiver plugin since this
# targets that record python operation call

# Instructions
# -----------------------------
# Make sure the mocopi blender receiver plugin is active
# https://github.com/sony/mocopi-receiver-plugin-blender

# Load this script in a Blender panel. 
# Look at the mocopi plug in to find the public IP address we
# will visit on your phone (e.g. http://192.168.1.165:3333)
# make sure to put port 3333 on the end to connect
# Make sure your phone is your WIFI network
# Run this script
# with the play button on the python panel.
# try to access URL from your phone
# toggling the record button should start new recordings and new takes

import bpy
import threading
import queue
from http.server import BaseHTTPRequestHandler, HTTPServer


PORT = 3333

# Requests from the HTTP thread go here
request_queue = queue.Queue()


HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Blender Mocap</title>

    <style>
        body {
            margin: 0;
            background: #111;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            font-family: Arial, sans-serif;
        }

        button {
            width: 80vw;
            height: 60vh;
            max-width: 500px;
            max-height: 500px;
            font-size: 60px;
            font-weight: bold;
            border: none;
            border-radius: 30px;
            background: #d22;
            color: white;
            touch-action: manipulation;
        }

        button:active {
            transform: scale(0.97);
            background: #a11;
        }
    </style>
</head>

<body>

<button onclick="record()">RECORD</button>

<script>
function record() {
    fetch('/record', {
        method: 'POST'
    })
    .then(response => response.text())
    .then(text => {
        console.log(text);
    });
}
</script>

</body>
</html>
"""


class BlenderHTTPHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(HTML.encode())

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/record":

            # Tell Blender's main thread to execute the operator
            request_queue.put("record")

            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Recording command sent")

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Keep HTTP requests from cluttering Blender's console
        pass


def process_requests():
    """
    Runs on Blender's main thread.
    """

    while not request_queue.empty():

        command = request_queue.get()

        if command == "record":

            print("Mocopi RECORD button pressed")

            bpy.ops.mocopi_receiver_plugin.avatar_panel(
                id=1,
                action='recording_button'
            )

    # Check again in 100ms
    return 0.1


def start_server():

    server = HTTPServer(("0.0.0.0", PORT), BlenderHTTPHandler)

    print(f"Blender HTTP server running on port {PORT}")

    server.serve_forever()


# Start Blender-side request processing
if not any(
    timer.__name__ == "process_requests"
    for timer in []
):
    bpy.app.timers.register(process_requests)


# Start HTTP server in background thread
server_thread = threading.Thread(
    target=start_server,
    daemon=True
)

server_thread.start()

print(f"Open http://<BLENDER-PC-IP>:{PORT} on your phone")