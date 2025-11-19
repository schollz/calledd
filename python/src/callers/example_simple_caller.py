from flask import request, Response
from twilio.twiml.voice_response import VoiceResponse
from enum import Enum
from src.utils.logging import logger
from src.callers.base import BaseCaller, BaseCallState

class SimpleCaller(BaseCaller):
    def __init__(self, twilio_sid, twilio_token, twilio_number, target_number, transfer_number, ngrok_token=None):
        super().__init__(twilio_sid, twilio_token, twilio_number, target_number, transfer_number, ngrok_token)
        self.transferred = False

    def _register_routes(self):
        self.app.add_url_rule('/voice', 'voice', self.voice, methods=['GET', 'POST'])

    def get_state(self):
        if self.transferred:
            return BaseCallState.COMPLETE
        elif self.current_call_sid:
            return BaseCallState.CALLING
        else:
            return BaseCallState.IDLE

    def reset(self):
        logger.info("Resetting simple caller")
        self.transferred = False
        self.current_call_sid = None

    def voice(self):
        logger.debug("Voice endpoint hit - immediately transferring")
        response = VoiceResponse()
        response.dial(self.transfer_number)
        self.transferred = True
        self.mark_complete()
        logger.info(f"Call transferred to {self.transfer_number}")
        return Response(str(response), mimetype='text/xml')
