# Call Automation System

Automated phone call system for navigating IVR menus and verification codes.

## Structure

```
src/
├── callers/
│   ├── base.py               # Base caller class with common interface
│   └── california_edd.py     # California EDD caller implementation
└── utils/
    └── logging.py            # Shared logging configuration
```

## Architecture

All callers extend `BaseCaller` which provides:
- Common initialization (Twilio, Flask, ngrok)
- Standard monitoring methods (`is_calling()`, `is_done()`, `get_status()`)
- Lifecycle management (`start_server()`, `make_call()`, `stop()`)
- Standardized status fields for logging/monitoring

### Creating a New Caller

```python
from src.callers.base import BaseCaller, BaseCallState

class MyCaller(BaseCaller):
    def _register_routes(self):
        # Register Flask routes
        self.app.add_url_rule('/voice', 'voice', self.voice, methods=['GET', 'POST'])

    def get_state(self):
        # Map internal state to BaseCallState
        if self.my_state == MyState.COMPLETE:
            return BaseCallState.COMPLETE
        elif self.current_call_sid:
            return BaseCallState.CALLING
        return BaseCallState.IDLE

    def reset(self):
        # Reset internal state
        self.my_state = MyState.INITIAL
        self.current_call_sid = None
```

## CaliforniaEDDCaller API

### Initialization

```python
from src.callers.california_edd import CaliforniaEDDCaller

caller = CaliforniaEDDCaller(
    twilio_sid='...',
    twilio_token='...',
    twilio_number='+1234567890',
    target_number='+0987654321',
    transfer_number='+1111111111',
    ngrok_token='...'  # optional
)
```

### Methods

#### `start_server()`
Starts the Flask server and ngrok tunnel. Must be called before `make_call()`.

```python
caller.start_server()
```

#### `make_call()`
Initiates the phone call. Requires `start_server()` to be called first.

```python
caller.make_call()
```

#### `is_calling() -> bool`
Returns `True` if a call is currently active and not complete.

```python
if caller.is_calling():
    print("Call in progress")
```

#### `is_done() -> bool`
Returns `True` if the call has completed successfully.

```python
if caller.is_done():
    print("Call completed")
```

#### `get_status() -> dict`
Returns comprehensive status information (standardized across all callers).

```python
status = caller.get_status()
# {
#     'caller_type': 'CaliforniaEDDCaller',
#     'state': 'CALLING',                    # BaseCallState
#     'call_sid': 'CA123...',
#     'target_number': '+1234567890',
#     'is_calling': True,
#     'is_done': False,
#     'is_failed': False,
#     'call_started_at': '2025-01-19T12:00:00',
#     'call_ended_at': None,
#     'duration_seconds': 45.2,
#     'last_error': None
# }
```

**Standard Fields (exported by all callers):**
- `caller_type`: Class name of the caller
- `state`: Current BaseCallState (IDLE, CALLING, COMPLETE, FAILED)
- `call_sid`: Twilio call SID
- `target_number`: Phone number being called
- `is_calling`: Boolean flag for active call
- `is_done`: Boolean flag for successful completion
- `is_failed`: Boolean flag for failed call
- `call_started_at`: ISO timestamp when call began
- `call_ended_at`: ISO timestamp when call ended
- `duration_seconds`: Call duration in seconds
- `last_error`: Error message if failed

#### `get_call_sid() -> str`
Returns the current Twilio call SID.

```python
call_sid = caller.get_call_sid()
```

#### `stop()`
Stops the current call and resets the state machine.

```python
caller.stop()
```

#### `reset()`
Resets the state machine without hanging up.

```python
caller.reset()
```

## Usage

### Standalone

```python
from src.callers.california_edd import CaliforniaEDDCaller

caller = CaliforniaEDDCaller(...)
caller.start_server()
caller.make_call()

while caller.is_calling():
    time.sleep(1)

print(caller.get_status())
```

### With Django APScheduler

See `django_example.py` for integration example.

```python
# Schedule call for 8 AM daily
scheduler.add_job(
    start_edd_call,
    'cron',
    hour=8,
    minute=0,
    args=[caller_config]
)

# Monitor every minute
scheduler.add_job(
    check_call_status,
    'interval',
    minutes=1,
    args=['caller_id']
)
```

## Environment Variables

```bash
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+1234567890
TARGET_PHONE_NUMBER=+0987654321
TRANSFER_NUMBER=+1111111111
NGROK_AUTH_TOKEN=...

SPEECH_LANGUAGE=en-US
SPEECH_PROFANITY_FILTER=false
BANANA_TIMEOUT=120
```

## Call States

1. `WAITING_FOR_VERIFICATION_CODE` - Listening for verification code
2. `WAITING_FOR_PHONE_TREE` - Waiting to navigate phone menu
3. `WAITING_FOR_BANANA` - Waiting for transfer keyword
4. `COMPLETE` - Call completed and transferred
