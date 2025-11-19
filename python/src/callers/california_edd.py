from flask import request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
import os
import threading
import time
from enum import Enum
import re
from src.utils.logging import logger
from src.callers.base import BaseCaller, BaseCallState

class CaliforniaEDDState(Enum):
    WAITING_FOR_VERIFICATION_CODE = 1
    WAITING_FOR_PHONE_TREE = 2
    WAITING_FOR_BANANA = 3
    COMPLETE = 4

GATHER_CONFIGS = {
    CaliforniaEDDState.WAITING_FOR_VERIFICATION_CODE: {
        'speechModel': 'numbers_and_commands',
        'hints': '0,1,2,3,4,5,6,7,8,9,code,verification',
        'timeout': 2,
    },
    CaliforniaEDDState.WAITING_FOR_PHONE_TREE: {
        'speechModel': 'experimental_utterances',
        'hints': 'press,enter,option,menu,edd',
        'timeout': 2,
    },
    CaliforniaEDDState.WAITING_FOR_BANANA: {
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

class CaliforniaEDDCaller(BaseCaller):
    def __init__(self, twilio_sid, twilio_token, twilio_number, target_number, transfer_number, ngrok_token=None):
        super().__init__(twilio_sid, twilio_token, twilio_number, target_number, transfer_number, ngrok_token)

        self.state = CaliforniaEDDState.WAITING_FOR_VERIFICATION_CODE
        self.banana_timeout = None

        self.speech_language = os.environ.get('SPEECH_LANGUAGE', 'en-US')
        self.speech_profanity_filter = os.environ.get('SPEECH_PROFANITY_FILTER', 'false')
        self.banana_timeout_seconds = int(os.environ.get('BANANA_TIMEOUT', '120'))
        self.redial_phrases = ['goodbye', 'please call again']

    def _register_routes(self):
        self.app.add_url_rule('/voice', 'voice', self.voice, methods=['GET', 'POST'])
        self.app.add_url_rule('/process_speech', 'process_speech', self.process_speech, methods=['POST'])

    def get_state(self):
        if self.state == CaliforniaEDDState.COMPLETE:
            return BaseCallState.COMPLETE
        elif self.current_call_sid:
            return BaseCallState.CALLING
        else:
            return BaseCallState.IDLE

    def reset(self):
        logger.info("Resetting EDD state machine")
        self.state = CaliforniaEDDState.WAITING_FOR_VERIFICATION_CODE
        self.current_call_sid = None
        if self.banana_timeout:
            self.banana_timeout.cancel()
            self.banana_timeout = None

    def banana_timeout_handler(self, call_sid):
        logger.warning(f"Banana timeout after {self.banana_timeout_seconds} seconds - hanging up and retrying")
        try:
            self.twilio_client.calls(call_sid).update(status='completed')
        except Exception as e:
            logger.error(f"Error hanging up call: {e}")
        time.sleep(2)
        self.reset()
        self.make_call()

    def voice(self):
        current_state = self.state
        config = GATHER_CONFIGS.get(current_state, GATHER_CONFIGS[CaliforniaEDDState.WAITING_FOR_VERIFICATION_CODE])

        logger.debug(f"Voice endpoint hit - state: {current_state.name}, model: {config['speechModel']}")

        response = VoiceResponse()
        gather = Gather(
            input='speech',
            action='/process_speech',
            timeout=config['timeout'],
            speechTimeout='auto',
            speechModel=config['speechModel'],
            language=self.speech_language,
            profanityFilter=self.speech_profanity_filter,
            hints=config['hints']
        )
        response.append(gather)
        return Response(str(response), mimetype='text/xml')

    def process_speech(self):
        speech_result = request.values.get('SpeechResult', '')
        speech_lower = speech_result.lower()
        speech_lower = ' '.join(speech_lower.split())
        call_sid = request.values.get('CallSid', '')
        confidence = request.values.get('Confidence', 'N/A')

        logger.info(f"\033[33m{speech_result}\033[0m (confidence: {confidence})")
        logger.debug(f"Current state: {self.state.name}")

        response = VoiceResponse()

        if 'verification code' in speech_lower and self.state != CaliforniaEDDState.WAITING_FOR_VERIFICATION_CODE:
            logger.info("Detected 'verification code' keyword - resetting to WAITING_FOR_VERIFICATION_CODE state")
            self.state = CaliforniaEDDState.WAITING_FOR_VERIFICATION_CODE

        if any(phrase in speech_lower for phrase in self.redial_phrases):
            logger.info(f"Detected redial phrase: {speech_lower} - resetting state machine and retrying call")
            self.reset()
            threading.Thread(target=self.make_call).start()
            response.hangup()

        elif self.state == CaliforniaEDDState.WAITING_FOR_BANANA and 'banana' in speech_result.lower():
            logger.info("Banana keyword detected - initiating transfer")
            if self.banana_timeout:
                self.banana_timeout.cancel()
                self.banana_timeout = None
            self.state = CaliforniaEDDState.COMPLETE
            self.mark_complete()
            response.dial(self.transfer_number)
            logger.info(f"Call transferred to {self.transfer_number}")

        elif self.state == CaliforniaEDDState.WAITING_FOR_PHONE_TREE:
            digits = '3 1 0'
            logger.info(f"Phone tree prompt detected - sending digits: {digits}")
            self.state = CaliforniaEDDState.WAITING_FOR_BANANA

            response.pause(length=1)
            response.play(digits=format_digits_with_pauses(digits, 5))
            response.redirect('/voice')

            logger.info("Waiting for banana keyword")
            self.banana_timeout = threading.Timer(self.banana_timeout_seconds, self.banana_timeout_handler, args=(call_sid,))
            self.banana_timeout.start()

        elif self.state == CaliforniaEDDState.WAITING_FOR_VERIFICATION_CODE:
            code = None

            if 'code' in speech_lower:
                speech_lower = speech_lower.replace('zero', '0').replace('one', '1').replace('two', '2')\
                    .replace('three', '3').replace('four', '4').replace('five', '5').replace('six', '6')\
                    .replace('seven', '7').replace('eight', '8').replace('nine', '9')
                speech_lower = speech_lower.replace(' ', '')
                digit_sequences = re.findall(r'\d+', speech_lower)
                logger.debug(f"Digit sequences found: {digit_sequences}")
                if digit_sequences:
                    code = max(digit_sequences, key=len)

            if code:
                code = ''.join(filter(str.isalnum, code))
                logger.info(f"Verification code detected: {code}")
                self.current_call_sid = call_sid
                self.state = CaliforniaEDDState.WAITING_FOR_PHONE_TREE

                response.pause(length=1)
                response.play(digits=format_digits_with_pauses(code, 0.5))
                response.redirect('/voice')
                logger.info(f"Successfully sent code: {code}")
            else:
                response.pause(length=2)
                response.redirect('/voice')
        else:
            logger.debug("No action taken - continuing to gather speech")
            response.pause(length=1)
            response.redirect('/voice')

        return Response(str(response), mimetype='text/xml')
