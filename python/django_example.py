"""
Example of how to integrate CaliforniaEDDCaller with Django APScheduler

In your Django app, you would:
1. Create a management command or app config
2. Use APScheduler to schedule calls
3. Monitor call status via the helper methods
"""

from apscheduler.schedulers.background import BackgroundScheduler
from src.callers.california_edd import CaliforniaEDDCaller
from src.utils.logging import logger
import os

active_callers = {}

def start_edd_call(caller_id, twilio_sid, twilio_token, twilio_number, target_number, transfer_number, ngrok_token=None):
    if caller_id in active_callers and active_callers[caller_id].is_calling():
        logger.warning(f"Caller {caller_id} already active")
        return

    caller = CaliforniaEDDCaller(
        twilio_sid=twilio_sid,
        twilio_token=twilio_token,
        twilio_number=twilio_number,
        target_number=target_number,
        transfer_number=transfer_number,
        ngrok_token=ngrok_token
    )

    active_callers[caller_id] = caller

    caller.start_server()
    caller.make_call()

    logger.info(f"Caller {caller_id} started: {caller.get_status()}")

def check_call_status(caller_id):
    if caller_id not in active_callers:
        logger.warning(f"Caller {caller_id} not found")
        return None

    caller = active_callers[caller_id]
    status = caller.get_status()

    logger.info(f"Caller {caller_id} status: {status}")

    if caller.is_done():
        logger.info(f"Caller {caller_id} completed")
        del active_callers[caller_id]

    return status

def stop_call(caller_id):
    if caller_id in active_callers:
        caller = active_callers[caller_id]
        caller.stop()
        del active_callers[caller_id]
        logger.info(f"Caller {caller_id} stopped")

def setup_scheduler():
    scheduler = BackgroundScheduler()

    scheduler.add_job(
        start_edd_call,
        'cron',
        hour=8,
        minute=0,
        args=['morning_call', os.getenv('TWILIO_ACCOUNT_SID'), os.getenv('TWILIO_AUTH_TOKEN'),
              os.getenv('TWILIO_PHONE_NUMBER'), os.getenv('TARGET_PHONE_NUMBER'),
              os.getenv('TRANSFER_NUMBER'), os.getenv('NGROK_AUTH_TOKEN')]
    )

    scheduler.add_job(
        check_call_status,
        'interval',
        minutes=1,
        args=['morning_call']
    )

    scheduler.start()
    logger.info("Scheduler started")
    return scheduler
