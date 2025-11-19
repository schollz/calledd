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

SPEECH_TIMEOUT = int(os.environ.get('SPEECH_TIMEOUT', '3'))
SPEECH_LANGUAGE = os.environ.get('SPEECH_LANGUAGE', 'en-US')
SPEECH_PROFANITY_FILTER = os.environ.get('SPEECH_PROFANITY_FILTER', 'false')
BANANA_TIMEOUT = int(os.environ.get('BANANA_TIMEOUT', '120'))
REDIAL_PHRASES = ['goodbye','please call again']

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
    WAITING_FOR_VERIFICATION_CODE = 1
    WAITING_FOR_PHONE_TREE = 2
    WAITING_FOR_BANANA = 3
    COMPLETE = 4

GATHER_CONFIGS = {
    CallState.WAITING_FOR_VERIFICATION_CODE: {
        'speechModel': 'numbers_and_commands',
        'hints': '0,1,2,3,4,5,6,7,8,9,code,verification',
        'timeout': 2,
    },
    CallState.WAITING_FOR_PHONE_TREE: {
        'speechModel': 'experimental_utterances',
        'hints': 'press,enter,option,menu,edd',
        'timeout': 2,
    },
    CallState.WAITING_FOR_BANANA: {
        'speechModel': 'experimental_utterances',
        'hints': 'banana,transfer,representative,agent',
        'timeout': 2,
    },
}

def format_digits_with_pauses(digits, pause_seconds):
    digits = digits.replace(' ', '')
    pause_length = int(pause_seconds / 0.5)
    digits_with_pauses = ('w' * pause_length).join(digits) + ('w' * pause_length)
    logger.debug(f"Original digits: {digits}, with pauses: {digits_with_pauses}")
    return digits_with_pauses

class StateMachine:
    def __init__(self):
        self.state = CallState.WAITING_FOR_VERIFICATION_CODE
        self.current_call_sid = None
        self.public_url = None
        self.banana_timeout = None
        self.port = get_free_port()

    def reset(self):
        logger.info("Resetting state machine")
        self.state = CallState.WAITING_FOR_VERIFICATION_CODE
        self.current_call_sid = None
        if self.banana_timeout:
            self.banana_timeout.cancel()
            self.banana_timeout = None

    def banana_timeout_handler(self, call_sid):
        logger.warning(f"Banana timeout after {BANANA_TIMEOUT} seconds - hanging up and retrying")
        try:
            client.calls(call_sid).update(status='completed')
        except Exception as e:
            logger.error(f"Error hanging up call: {e}")
        time.sleep(2)
        self.reset()
        self.make_call()

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
    current_state = state_machine.state
    config = GATHER_CONFIGS.get(current_state, GATHER_CONFIGS[CallState.WAITING_FOR_VERIFICATION_CODE])

    logger.debug(f"Voice endpoint hit - state: {current_state.name}, model: {config['speechModel']}")

    response = VoiceResponse()
    gather = Gather(
        input='speech',
        action='/process_speech',
        timeout=config['timeout'],
        speechTimeout='auto',
        speechModel=config['speechModel'],
        language=SPEECH_LANGUAGE,
        profanityFilter=SPEECH_PROFANITY_FILTER,
        hints=config['hints']
    )
    response.append(gather)
    return Response(str(response), mimetype='text/xml')

@app.route("/process_speech", methods=['POST'])
def process_speech():
    speech_result = request.values.get('SpeechResult', '')
    speech_lower = speech_result.lower()
    speech_lower = ' '.join(speech_lower.split())
    call_sid = request.values.get('CallSid', '')
    confidence = request.values.get('Confidence', 'N/A')

    logger.info(f"\033[33m{speech_result}\033[0m (confidence: {confidence})")
    logger.debug(f"Current state: {state_machine.state.name}")

    response = VoiceResponse()

    # State machine re-routing on keywords
    if 'verification code' in speech_lower and state_machine.state != CallState.WAITING_FOR_VERIFICATION_CODE:
        logger.info("Detected 'verification code' keyword - resetting to WAITING_FOR_VERIFICATION_CODE state")
        state_machine.state = CallState.WAITING_FOR_VERIFICATION_CODE

    # State machine logic
    if any(phrase in speech_lower for phrase in REDIAL_PHRASES):
        logger.info(f"Detected redial phrase: {speech_lower} - resetting state machine and retrying call")
        state_machine.reset()
        threading.Thread(target=state_machine.make_call).start()
        response.hangup()

    elif state_machine.state == CallState.WAITING_FOR_BANANA and 'banana' in speech_result.lower():
        logger.info("Banana keyword detected - initiating transfer")
        if state_machine.banana_timeout:
            state_machine.banana_timeout.cancel()
            state_machine.banana_timeout = None
        state_machine.state = CallState.COMPLETE
        response.dial(TRANSFER_NUMBER)
        logger.info(f"Call transferred to {TRANSFER_NUMBER}")

    elif state_machine.state == CallState.WAITING_FOR_PHONE_TREE:
        digits = '3 1 0'
        logger.info(f"Phone tree prompt detected - sending digits: {digits}")
        state_machine.state = CallState.WAITING_FOR_BANANA

        response.pause(length=1)
        response.play(digits=format_digits_with_pauses(digits, 5))
        response.redirect('/voice')

        logger.info("Waiting for banana keyword")
        state_machine.banana_timeout = threading.Timer(BANANA_TIMEOUT, state_machine.banana_timeout_handler, args=(call_sid,))
        state_machine.banana_timeout.start()

    elif state_machine.state == CallState.WAITING_FOR_VERIFICATION_CODE:
        code = None

        if 'code' in speech_lower:
            speech_lower = speech_lower.replace('zero', '0').replace('one', '1').replace('two', '2')\
                .replace('three', '3').replace('four', '4').replace('five', '5').replace('six', '6')\
                .replace('seven', '7').replace('eight', '8').replace('nine', '9')
            # remove spaces
            speech_lower = speech_lower.replace(' ', '')
            # find longest contiguous digit sequence
            digit_sequences = re.findall(r'\d+', speech_lower)
            logger.debug(f"Digit sequences found: {digit_sequences}")
            if digit_sequences:
                code = max(digit_sequences, key=len)
            
        if code:
            code = ''.join(filter(str.isalnum, code))
            logger.info(f"Verification code detected: {code}")
            state_machine.current_call_sid = call_sid
            state_machine.state = CallState.WAITING_FOR_PHONE_TREE

            response.pause(length=1)
            response.play(digits=format_digits_with_pauses(code, 0.5))
            response.redirect('/voice')
            logger.info(f"Successfully sent code: {code}")            
        else:
            response.pause(length=2)
            response.redirect('/voice')
    else:
        # just continue gathering
        logger.debug("No action taken - continuing to gather speech")
        response.pause(length=1)
        response.redirect('/voice')

    return Response(str(response), mimetype='text/xml')

if __name__ == "__main__":
    logger.info("="*60)
    logger.info("Starting Twilio Speech-to-Text Call Automation")
    logger.info("="*60)
    logger.info(f"Speech language: {SPEECH_LANGUAGE}")
    logger.info(f"Banana timeout: {BANANA_TIMEOUT}s")
    logger.info(f"Server port: {state_machine.port}")
    logger.info("Dynamic speech models per state:")
    for state, config in GATHER_CONFIGS.items():
        logger.info(f"  {state.name}: {config['speechModel']} (timeout={config['timeout']}s)")
    logger.info("="*60)

    threading.Thread(target=lambda: app.run(port=state_machine.port, debug=False)).start()

    logger.info(f"Flask server starting on port {state_machine.port}...")
    time.sleep(2)
    
    state_machine.make_call()
    
    while True:
        time.sleep(1)