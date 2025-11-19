# Twilio Environment Variables

## TWILIO_ACCOUNT_SID & TWILIO_AUTH_TOKEN

1. Go to [console.twilio.com](https://console.twilio.com)
2. Login or create account
3. Copy **Account SID** and **Auth Token** from dashboard

## TWILIO_PHONE_NUMBER

1. In Twilio Console, go to **Phone Numbers** → **Manage** → **Buy a number**
2. Purchase a number with Voice capability
3. Copy the phone number (format: `+1234567890`)

## TARGET_PHONE_NUMBER

- The phone number you want to call (format: `+1234567890`)

## TRANSFER_NUMBER

- The phone number to transfer calls to (format: `+1234567890`)

## Setup

Add to `.env`:
```
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_PHONE_NUMBER=+1234567890
TARGET_PHONE_NUMBER=+1234567890
TRANSFER_NUMBER=+1234567890
```
