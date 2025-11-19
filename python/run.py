import os
import time
from dotenv import load_dotenv
from src.utils.logging import logger
from src.callers.california_edd import CaliforniaEDDCaller, CaliforniaEDDState, GATHER_CONFIGS

load_dotenv()

TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER')
TARGET_PHONE_NUMBER = os.environ.get('TARGET_PHONE_NUMBER')
TRANSFER_NUMBER = os.environ.get('TRANSFER_NUMBER')
NGROK_AUTH_TOKEN = os.environ.get('NGROK_AUTH_TOKEN')

SPEECH_LANGUAGE = os.environ.get('SPEECH_LANGUAGE', 'en-US')
BANANA_TIMEOUT = int(os.environ.get('BANANA_TIMEOUT', '120'))

if __name__ == "__main__":
    logger.info("="*60)
    logger.info("Starting California EDD Call Automation")
    logger.info("="*60)
    logger.info(f"Speech language: {SPEECH_LANGUAGE}")
    logger.info(f"Banana timeout: {BANANA_TIMEOUT}s")
    logger.info("Dynamic speech models per state:")
    for state, config in GATHER_CONFIGS.items():
        logger.info(f"  {state.name}: {config['speechModel']} (timeout={config['timeout']}s)")
    logger.info("="*60)

    caller = CaliforniaEDDCaller(
        twilio_sid=TWILIO_ACCOUNT_SID,
        twilio_token=TWILIO_AUTH_TOKEN,
        twilio_number=TWILIO_PHONE_NUMBER,
        target_number=TARGET_PHONE_NUMBER,
        transfer_number=TRANSFER_NUMBER,
        ngrok_token=NGROK_AUTH_TOKEN
    )
    logger.info(f"Server port: {caller.port}")

    caller.start_server()
    caller.make_call()

    while caller.is_calling():
        time.sleep(1)

    logger.info(f"Call completed with status: {caller.get_status()}")
