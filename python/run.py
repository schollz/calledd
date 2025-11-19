from flask import Flask, request, Response
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather
import os
from pyngrok import ngrok
import threading
import time
from enum import Enum
import logging
from colorlog import ColoredFormatter
from dotenv import load_dotenv
import socket
import re

load_dotenv()

app = Flask(__name__)

TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER')
TARGET_PHONE_NUMBER = os.environ.get('TARGET_PHONE_NUMBER')
TRANSFER_NUMBER = os.environ.get('TRANSFER_NUMBER')

SPEECH_TIMEOUT = int(os.environ.get('SPEECH_TIMEOUT', '10'))
SPEECH_TIMEOUT_AUTO = os.environ.get('SPEECH_TIMEOUT_AUTO', 'auto')
SPEECH_MODEL = os.environ.get('SPEECH_MODEL', 'default')
SPEECH_LANGUAGE = os.environ.get('SPEECH_LANGUAGE', 'en-US')
SPEECH_PROFANITY_FILTER = os.environ.get('SPEECH_PROFANITY_FILTER', 'true')
SPEECH_HINTS = os.environ.get('SPEECH_HINTS', 'code,banana')
BANANA_TIMEOUT = int(os.environ.get('BANANA_TIMEOUT', '30'))


client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

formatter = ColoredFormatter(
    "%(log_color)s%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s%(reset)s",
    datefmt='%Y-%m-%d %H:%M:%S',
    log_colors={
        'DEBUG': 'cyan',
        'INFO': 'green',
        'WARNING': 'yellow',
        'ERROR': 'red',
        'CRITICAL': 'red,bg_white',
    }
)

handler = logging.StreamHandler()
handler.setFormatter(formatter)

logger = logging.getLogger(__name__)
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)

werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.setLevel(logging.WARNING)

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port

class CallState(Enum):
    WAITING_FOR_FIRST_CODE = 1
    SENDING_FIRST_CODE = 2
    WAITING_FOR_SECOND_CODE = 3
    SENDING_SECOND_CODE = 4
    WAITING_FOR_BANANA = 5
    TRANSFERRING = 6
    COMPLETE = 7

class StateMachine:
    def __init__(self):
        self.state = CallState.WAITING_FOR_FIRST_CODE
        self.codes = []
        self.current_call_sid = None
        self.public_url = None
        self.banana_timeout = None
        self.port = get_free_port()
        
    def reset(self):
        logger.info("Resetting state machine")
        self.state = CallState.WAITING_FOR_FIRST_CODE
        self.codes = []
        self.current_call_sid = None
        if self.banana_timeout:
            self.banana_timeout.cancel()
            self.banana_timeout = None
        
    def process_code(self, code, call_sid):
        logger.info(f"Current state: {self.state.name}, Received code: {code}")
        
        if self.state == CallState.WAITING_FOR_FIRST_CODE:
            self.codes.append(code)
            self.current_call_sid = call_sid
            self.state = CallState.SENDING_FIRST_CODE
            threading.Thread(target=self.send_digits, args=(call_sid, code, 0)).start()
            
        elif self.state == CallState.WAITING_FOR_SECOND_CODE:
            self.codes.append(code)
            self.state = CallState.SENDING_SECOND_CODE
            threading.Thread(target=self.send_digits, args=(call_sid, code, 1)).start()
            
    def process_banana(self, call_sid):
        if self.state == CallState.WAITING_FOR_BANANA:
            logger.info("Banana keyword detected - initiating transfer")
            if self.banana_timeout:
                self.banana_timeout.cancel()
                self.banana_timeout = None
            self.state = CallState.TRANSFERRING
            threading.Thread(target=self.transfer_call, args=(call_sid,)).start()
            
    def send_digits(self, call_sid, code, code_index):
        time.sleep(1)
        try:
            twiml = f'<Response><Play digits="{code}"/><Pause length="2"/><Redirect>{self.public_url}/voice</Redirect></Response>'
            logger.debug(f"twiml to send: {twiml}")
            client.calls(call_sid).update(twiml=twiml)
            logger.info(f"Successfully sent code {code_index + 1}: {code}")
            
            if code_index == 0:
                self.state = CallState.WAITING_FOR_SECOND_CODE
                logger.info("Waiting for second code")
            elif code_index == 1:
                self.state = CallState.WAITING_FOR_BANANA
                self.banana_timeout = threading.Timer(BANANA_TIMEOUT, self.banana_timeout_handler, args=(call_sid,))
                self.banana_timeout.start()
                logger.info(f"Waiting for banana keyword ({BANANA_TIMEOUT} second timeout)")
                
        except Exception as e:
            logger.error(f"Error sending digits: {e}")
            
    def banana_timeout_handler(self, call_sid):
        logger.warning(f"Banana timeout after {BANANA_TIMEOUT} seconds - hanging up and retrying")
        try:
            client.calls(call_sid).update(status='completed')
        except Exception as e:
            logger.error(f"Error hanging up call: {e}")
        time.sleep(2)
        self.reset()
        self.make_call()
        
    def transfer_call(self, call_sid):
        try:
            twiml = f'<Response><Dial>{TRANSFER_NUMBER}</Dial></Response>'
            client.calls(call_sid).update(twiml=twiml)
            logger.info(f"Call transferred to {TRANSFER_NUMBER}")
            self.state = CallState.COMPLETE
        except Exception as e:
            logger.error(f"Error transferring call: {e}")
            
    def make_call(self):
        if not self.public_url:
            logger.info(f"Starting ngrok tunnel on port {self.port}...")
            tunnel = ngrok.connect(self.port)
            self.public_url = tunnel.public_url
            logger.info(f"ngrok tunnel established: {self.public_url}")
        
        logger.info(f"Initiating call to {TARGET_PHONE_NUMBER}")
        call = client.calls.create(
            to=TARGET_PHONE_NUMBER,
            from_=TWILIO_PHONE_NUMBER,
            url=f"{self.public_url}/voice"
        )
        
        self.current_call_sid = call.sid
        logger.info(f"Call initiated with SID: {call.sid}")

state_machine = StateMachine()

@app.route("/voice", methods=['GET', 'POST'])
def voice():
    logger.debug("Voice endpoint hit - setting up speech gather")
    response = VoiceResponse()
    gather = Gather(
        input='speech',
        action='/process_speech',
        timeout=SPEECH_TIMEOUT,
        speechTimeout=SPEECH_TIMEOUT_AUTO,
        speechModel=SPEECH_MODEL,
        language=SPEECH_LANGUAGE,
        profanityFilter=SPEECH_PROFANITY_FILTER,
        hints=SPEECH_HINTS
    )
    response.append(gather)
    response.redirect('/voice')
    return Response(str(response), mimetype='text/xml')

@app.route("/process_speech", methods=['POST'])
def process_speech():
    speech_result = request.values.get('SpeechResult', '')
    call_sid = request.values.get('CallSid', '')
    confidence = request.values.get('Confidence', 'N/A')
    
    logger.info(f"Speech transcribed: '{speech_result}' (confidence: {confidence})")
    logger.debug(f"Current state: {state_machine.state.name}")
    
    if state_machine.state == CallState.WAITING_FOR_BANANA and 'banana' in speech_result.lower():
        state_machine.process_banana(call_sid)

    if state_machine.state in [CallState.WAITING_FOR_FIRST_CODE, CallState.WAITING_FOR_SECOND_CODE]:
        speech_lower = speech_result.lower()
        code = None

        # Pattern: "verification code for 425"
        if ' code ' in speech_lower:
            # Extract the largest sequence of consecutive digits
            digit_sequences = re.findall(r'\d+', speech_result)
            if digit_sequences:
                code = max(digit_sequences, key=len)

        if code:
            code = ''.join(filter(str.isalnum, code))
            state_machine.process_code(code, call_sid)
    
    response = VoiceResponse()
    if state_machine.state in [CallState.COMPLETE, CallState.TRANSFERRING]:
        logger.info("Call completing - hanging up")
        response.hangup()
    else:
        response.pause(length=2)
        response.redirect('/voice')
    return Response(str(response), mimetype='text/xml')

if __name__ == "__main__":
    logger.info("="*60)
    logger.info("Starting Twilio Speech-to-Text Call Automation")
    logger.info("="*60)
    logger.info(f"Speech timeout: {SPEECH_TIMEOUT}s")
    logger.info(f"Speech model: {SPEECH_MODEL}")
    logger.info(f"Speech language: {SPEECH_LANGUAGE}")
    logger.info(f"Speech hints: {SPEECH_HINTS}")
    logger.info(f"Banana timeout: {BANANA_TIMEOUT}s")
    logger.info(f"Server port: {state_machine.port}")
    logger.info("="*60)

    threading.Thread(target=lambda: app.run(port=state_machine.port, debug=False)).start()

    logger.info(f"Flask server starting on port {state_machine.port}...")
    time.sleep(2)
    
    state_machine.make_call()
    
    while True:
        time.sleep(1)