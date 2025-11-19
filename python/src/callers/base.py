from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from flask import Flask
from twilio.rest import Client
from pyngrok import ngrok
import socket
import threading
import time
from src.utils.logging import logger

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port

class BaseCallState(Enum):
    IDLE = 0
    CALLING = 1
    COMPLETE = 2
    FAILED = 3

class BaseCaller(ABC):
    def __init__(self, twilio_sid, twilio_token, twilio_number, target_number, transfer_number, ngrok_token=None):
        self.app = Flask(self.__class__.__name__)
        self.twilio_client = Client(twilio_sid, twilio_token)
        self.twilio_number = twilio_number
        self.target_number = target_number
        self.transfer_number = transfer_number
        self.ngrok_token = ngrok_token

        self.current_call_sid = None
        self.public_url = None
        self.port = get_free_port()

        self.call_started_at = None
        self.call_ended_at = None
        self.last_error = None

        self._register_routes()

    @abstractmethod
    def _register_routes(self):
        pass

    @abstractmethod
    def get_state(self):
        pass

    @abstractmethod
    def reset(self):
        pass

    def start_server(self):
        if not self.public_url:
            if self.ngrok_token:
                ngrok.set_auth_token(self.ngrok_token)
                logger.debug("ngrok auth token set")
            logger.info(f"Starting ngrok tunnel on port {self.port}...")
            tunnel = ngrok.connect(self.port)
            self.public_url = tunnel.public_url
            logger.info(f"ngrok tunnel established: {self.public_url}")

        logger.info(f"Flask server starting on port {self.port}...")
        threading.Thread(target=lambda: self.app.run(port=self.port, debug=False), daemon=True).start()
        time.sleep(2)

    def make_call(self):
        if not self.public_url:
            raise RuntimeError("Server not started. Call start_server() first.")

        self.call_started_at = datetime.utcnow()
        logger.info(f"Initiating call to {self.target_number}")

        try:
            call = self.twilio_client.calls.create(
                to=self.target_number,
                from_=self.twilio_number,
                url=f"{self.public_url}/voice"
            )
            self.current_call_sid = call.sid
            logger.info(f"Call initiated with SID: {call.sid}")
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Failed to initiate call: {e}")
            raise

    def is_calling(self):
        state = self.get_state()
        return self.current_call_sid is not None and state not in [BaseCallState.COMPLETE, BaseCallState.FAILED]

    def is_done(self):
        return self.get_state() == BaseCallState.COMPLETE

    def is_failed(self):
        return self.get_state() == BaseCallState.FAILED

    def get_status(self):
        state = self.get_state()
        duration = None
        if self.call_started_at:
            end_time = self.call_ended_at or datetime.utcnow()
            duration = (end_time - self.call_started_at).total_seconds()

        return {
            'caller_type': self.__class__.__name__,
            'state': state.name if isinstance(state, Enum) else str(state),
            'call_sid': self.current_call_sid,
            'target_number': self.target_number,
            'is_calling': self.is_calling(),
            'is_done': self.is_done(),
            'is_failed': self.is_failed(),
            'call_started_at': self.call_started_at.isoformat() if self.call_started_at else None,
            'call_ended_at': self.call_ended_at.isoformat() if self.call_ended_at else None,
            'duration_seconds': duration,
            'last_error': self.last_error,
        }

    def get_call_sid(self):
        return self.current_call_sid

    def stop(self):
        logger.info(f"Stopping {self.__class__.__name__}")
        if self.current_call_sid:
            try:
                self.twilio_client.calls(self.current_call_sid).update(status='completed')
                logger.info(f"Call {self.current_call_sid} terminated")
            except Exception as e:
                self.last_error = str(e)
                logger.error(f"Error stopping call: {e}")

        self.call_ended_at = datetime.utcnow()
        self.reset()

    def mark_complete(self):
        self.call_ended_at = datetime.utcnow()
        logger.info(f"Call marked complete. Duration: {(self.call_ended_at - self.call_started_at).total_seconds()}s")

    def mark_failed(self, error):
        self.call_ended_at = datetime.utcnow()
        self.last_error = str(error)
        logger.error(f"Call marked failed: {error}")
